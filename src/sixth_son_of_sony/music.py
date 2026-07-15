"""Music playback engine and Discord slash commands."""

import asyncio
import logging
from collections import deque
from concurrent.futures import CancelledError as FutureCancelledError

import discord
from discord import app_commands
from discord.ext import commands

from .checks import get_voice_access, send_ephemeral, voice_check
from .config import FFMPEG_OPTIONS, Settings
from .media import MediaResolver, is_http_url, is_spotify_url
from .state import GuildMusicState, MusicStateStore
from .views import (
    NowPlayingView,
    QueueView,
    build_queue_embed,
    format_duration,
    make_now_playing_embed,
    sync_now_playing,
)


logger = logging.getLogger(__name__)


class MusicCog(commands.Cog):
    def __init__(self, bot: commands.Bot, settings: Settings) -> None:
        self.bot = bot
        self.media = MediaResolver(settings)
        self.states = MusicStateStore()

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.guild is not None:
            return True
        await send_ephemeral(
            interaction,
            "❌ Music commands can only be used in a server.",
        )
        return False

    async def cog_app_command_error(
        self,
        interaction: discord.Interaction,
        error: app_commands.AppCommandError,
    ) -> None:
        if isinstance(error, app_commands.CheckFailure) and interaction.response.is_done():
            return
        original = (
            error.original
            if isinstance(error, app_commands.CommandInvokeError)
            else error
        )
        logger.error(
            "Unhandled application command error",
            exc_info=(type(original), original, original.__traceback__),
        )
        await send_ephemeral(
            interaction,
            "❌ That command failed unexpectedly. Please try again.",
        )

    @staticmethod
    def _cancel_task(task: asyncio.Task | None) -> None:
        if task is None or task.done():
            return
        if task is asyncio.current_task():
            return
        task.cancel()

    def _clear_prefetch_locked(self, state: GuildMusicState) -> None:
        self._cancel_task(state.prefetch_task)
        state.prefetch_task = None
        state.prefetched = None

    def _schedule_prefetch_locked(self, state: GuildMusicState) -> None:
        if not state.current or not state.queue:
            return

        target_url = state.queue[0]["webpage_url"]
        if state.prefetched and state.prefetched.get("webpage_url") == target_url:
            return
        if state.prefetch_task and not state.prefetch_task.done():
            return

        generation = state.generation

        async def prefetch() -> None:
            try:
                result = await asyncio.to_thread(
                    self.media.fetch_audio_info,
                    target_url,
                )
            except asyncio.CancelledError:
                return
            except Exception:
                logger.warning(
                    "Failed to prefetch queued track",
                    exc_info=True,
                )
                return

            result["webpage_url"] = target_url
            async with state.lock:
                if (
                    generation == state.generation
                    and state.queue
                    and state.queue[0]["webpage_url"] == target_url
                ):
                    state.prefetched = result
                if state.prefetch_task is asyncio.current_task():
                    state.prefetch_task = None

        state.prefetch_task = asyncio.create_task(prefetch())

    async def _append_tracks(
        self,
        guild: discord.Guild,
        tracks: list[dict],
    ) -> bool:
        state = self.states.get(guild.id)
        async with state.lock:
            state.queue.extend(tracks)
            state.idle_notified = False
            voice_client = guild.voice_client
            active = bool(
                voice_client
                and voice_client.is_connected()
                and (voice_client.is_playing() or voice_client.is_paused())
            )
            if active:
                self._schedule_prefetch_locked(state)
            return bool(
                voice_client
                and voice_client.is_connected()
                and not active
                and not state.starting
            )

    async def _disable_now_playing(
        self,
        view: discord.ui.View | None,
        message: discord.Message | None,
    ) -> None:
        if not isinstance(view, NowPlayingView) or message is None:
            return
        view.disable_all()
        try:
            await message.edit(view=view)
        except Exception:
            logger.debug("Could not disable an old now-playing view", exc_info=True)

    async def _reset_playback(
        self,
        guild: discord.Guild,
        *,
        disconnect: bool,
        update_message: bool,
    ):
        state = self.states.get(guild.id)
        async with state.lock:
            state.generation += 1
            state.starting = False
            state.idle_notified = True
            state.queue.clear()
            state.current = None
            state.loop = False
            self._clear_prefetch_locked(state)
            self._cancel_task(state.empty_channel_task)
            state.empty_channel_task = None
            view = state.now_playing_view
            message = state.now_playing_message
            state.now_playing_view = None
            state.now_playing_message = None
            text_channel = state.text_channel
            state.text_channel = None

        voice_client = guild.voice_client
        if voice_client:
            try:
                if voice_client.is_playing() or voice_client.is_paused():
                    voice_client.stop()
                if disconnect and voice_client.is_connected():
                    await voice_client.disconnect(force=True)
            except Exception:
                logger.exception("Failed to stop or disconnect the voice client")

        if update_message:
            await self._disable_now_playing(view, message)
        return text_channel

    async def _stop_from_view(self, guild: discord.Guild) -> None:
        await self._reset_playback(
            guild,
            disconnect=True,
            update_message=False,
        )

    def _after_playback(self, guild: discord.Guild, error: Exception | None) -> None:
        if error:
            logger.error("Discord voice playback error: %s", error)
        future = asyncio.run_coroutine_threadsafe(self.play_next(guild), self.bot.loop)

        def log_result(completed_future) -> None:
            try:
                completed_future.result()
            except FutureCancelledError:
                pass
            except Exception:
                logger.exception("Failed to advance the music queue")

        future.add_done_callback(log_result)

    async def _track_failed(
        self,
        guild: discord.Guild,
        state: GuildMusicState,
        track: dict,
        generation: int,
        error: Exception,
    ) -> None:
        logger.error(
            "Failed to prepare track %r",
            track.get("title"),
            exc_info=(type(error), error, error.__traceback__),
        )
        async with state.lock:
            if generation != state.generation or state.current is not track:
                return
            state.starting = False
            state.current = None
            text_channel = state.text_channel

        if text_channel:
            try:
                await text_channel.send(
                    f"❌ Skipping **{track['title']}** because its audio could not be loaded."
                )
            except Exception:
                logger.debug("Could not report a failed track", exc_info=True)
        await self.play_next(guild)

    async def play_next(self, guild: discord.Guild) -> None:
        state = self.states.get(guild.id)

        async with state.lock:
            voice_client = guild.voice_client
            if (
                not voice_client
                or not voice_client.is_connected()
                or state.starting
                or voice_client.is_playing()
                or voice_client.is_paused()
            ):
                return

            if state.loop and state.current:
                state.queue.appendleft(state.current)

            generation = state.generation
            if not state.queue:
                should_notify = not state.idle_notified
                state.idle_notified = True
                state.current = None
                old_view = state.now_playing_view
                old_message = state.now_playing_message
                state.now_playing_view = None
                state.now_playing_message = None
                text_channel = state.text_channel
                track = None
                prefetched = None
            else:
                track = state.queue.popleft()
                prefetched = (
                    state.prefetched
                    if state.prefetched
                    and state.prefetched.get("webpage_url") == track["webpage_url"]
                    else None
                )
                self._clear_prefetch_locked(state)
                state.current = track
                state.starting = True
                state.idle_notified = False
                should_notify = False
                old_view = None
                old_message = None
                text_channel = state.text_channel

        if track is None:
            await self._disable_now_playing(old_view, old_message)
            async with state.lock:
                still_idle = (
                    generation == state.generation
                    and not state.queue
                    and state.current is None
                    and not state.starting
                )
            if should_notify and still_idle and text_channel:
                try:
                    await text_channel.send(
                        "✅ Queue finished! Add more songs with `/play`."
                    )
                except Exception:
                    logger.debug("Could not announce the finished queue", exc_info=True)
            return

        try:
            info = prefetched or await asyncio.to_thread(
                self.media.fetch_audio_info,
                track["webpage_url"],
            )
            if track.get("spotify"):
                track["title"] = info["title"]
                track["duration"] = info["duration"]
            source = discord.FFmpegPCMAudio(
                info["stream_url"],
                **FFMPEG_OPTIONS,
            )
        except asyncio.CancelledError:
            async with state.lock:
                if generation == state.generation and state.current is track:
                    state.starting = False
            raise
        except Exception as error:
            await self._track_failed(guild, state, track, generation, error)
            return

        play_error: Exception | None = None
        async with state.lock:
            voice_client = guild.voice_client
            valid = (
                generation == state.generation
                and state.current is track
                and voice_client is not None
                and voice_client.is_connected()
                and not voice_client.is_playing()
                and not voice_client.is_paused()
            )
            if not valid:
                if generation == state.generation and state.current is track:
                    state.starting = False
                source.cleanup()
                return

            try:
                voice_client.play(
                    source,
                    after=lambda error: self._after_playback(guild, error),
                )
            except Exception as error:
                play_error = error
                state.starting = False
                source.cleanup()
            else:
                state.starting = False
                self._schedule_prefetch_locked(state)
                old_view = state.now_playing_view
                old_message = state.now_playing_message
                state.now_playing_view = None
                state.now_playing_message = None
                text_channel = state.text_channel

        if play_error:
            await self._track_failed(guild, state, track, generation, play_error)
            return

        await self._disable_now_playing(old_view, old_message)
        if not text_channel:
            return

        embed = make_now_playing_embed(track, state)
        view = NowPlayingView(
            guild,
            self.states,
            stop_callback=self._stop_from_view,
        )
        try:
            message = await text_channel.send(embed=embed, view=view)
        except Exception:
            logger.exception("Could not send the now-playing message")
            return

        async with state.lock:
            still_current = (
                generation == state.generation
                and state.current is track
                and guild.voice_client is not None
                and guild.voice_client.is_connected()
            )
            if still_current:
                state.now_playing_message = message
                state.now_playing_view = view

        if not still_current:
            view.disable_all()
            try:
                await message.edit(view=view)
            except Exception:
                logger.debug("Could not disable a stale now-playing view", exc_info=True)

    @app_commands.command(
        name="play",
        description="Add a YouTube, Spotify, or SoundCloud URL or search",
    )
    @app_commands.describe(
        query="A supported track/playlist URL, or search terms like 'alice in chains'"
    )
    @voice_check(require_bot=False)
    async def play(self, interaction: discord.Interaction, query: str) -> None:
        access = get_voice_access(interaction)
        await interaction.response.defer()

        voice_client = access.voice_client
        if voice_client is None:
            try:
                voice_client = await access.channel.connect()
            except Exception:
                logger.exception("Failed to connect to the user's voice channel")
                await interaction.followup.send(
                    "❌ I could not connect to that voice channel. Check my permissions."
                )
                return

        state = self.states.get(interaction.guild.id)
        async with state.lock:
            state.text_channel = interaction.channel

        if is_spotify_url(query):
            await interaction.followup.send("🎵 Resolving Spotify link...")
            try:
                spotify_tracks = await asyncio.to_thread(
                    self.media.resolve_spotify,
                    query,
                )
            except Exception:
                logger.exception("Failed to resolve a Spotify link")
                await interaction.followup.send(
                    "❌ I could not resolve that Spotify link. Please try again."
                )
                return

            if not spotify_tracks:
                await interaction.followup.send(
                    "❌ No tracks were found at that Spotify link."
                )
                return

            queued_tracks = [
                {
                    "webpage_url": spotify_track["search_query"],
                    "title": spotify_track["title"],
                    "duration": spotify_track["duration"],
                    "requester": str(interaction.user.display_name),
                    "spotify": True,
                }
                for spotify_track in spotify_tracks
            ]
            should_start = await self._append_tracks(
                interaction.guild,
                queued_tracks,
            )

            count = len(spotify_tracks)
            if count == 1:
                await interaction.followup.send(
                    f"➕ Added to queue: **{spotify_tracks[0]['title']}**"
                )
            else:
                await interaction.followup.send(
                    f"➕ Added **{count} tracks** from Spotify to the queue."
                )
            if should_start:
                await self.play_next(interaction.guild)
            return

        if is_http_url(query):
            try:
                collection = await asyncio.to_thread(
                    self.media.fetch_collection,
                    query,
                )
            except Exception:
                logger.info(
                    "URL did not resolve as a playlist; trying it as a single track",
                    exc_info=True,
                )
                collection = None

            if collection is not None:
                if not collection.tracks:
                    await interaction.followup.send(
                        "❌ No playable tracks were found in that playlist."
                    )
                    return

                queued_tracks = [
                    {
                        "webpage_url": collection_track["webpage_url"],
                        "title": collection_track["title"],
                        "duration": collection_track["duration"],
                        "requester": str(interaction.user.display_name),
                    }
                    for collection_track in collection.tracks
                ]
                should_start = await self._append_tracks(
                    interaction.guild,
                    queued_tracks,
                )
                collection_title = collection.title[:150]
                await interaction.followup.send(
                    f"➕ Added **{len(collection.tracks)} tracks** from "
                    f"{collection.platform} playlist **{collection_title}**."
                )
                if should_start:
                    await self.play_next(interaction.guild)
                return

        await interaction.followup.send(f"🔍 Fetching info for `{query}`...")
        try:
            info = await asyncio.to_thread(self.media.fetch_audio_info, query)
        except Exception:
            logger.exception("Failed to resolve a media URL or search")
            await interaction.followup.send(
                "❌ I could not find playable audio for that request."
            )
            return

        track = {
            "webpage_url": info["webpage_url"],
            "title": info["title"],
            "duration": info["duration"],
            "requester": str(interaction.user.display_name),
        }
        should_start = await self._append_tracks(interaction.guild, [track])
        duration = format_duration(info["duration"])

        if should_start:
            await self.play_next(interaction.guild)
        else:
            state = self.states.get(interaction.guild.id)
            async with state.lock:
                position = len(state.queue)
            await interaction.followup.send(
                f"➕ Added to queue (position **#{position}**): "
                f"**{track['title']}** `[{duration}]`"
            )

    @app_commands.command(name="skip", description="Skip the current track")
    @voice_check(require_bot=True)
    async def skip(self, interaction: discord.Interaction) -> None:
        access = get_voice_access(interaction)
        voice_client = access.voice_client
        if voice_client and (voice_client.is_playing() or voice_client.is_paused()):
            voice_client.stop()
            await interaction.response.send_message("⏭️ Skipped.")
        else:
            await send_ephemeral(interaction, "❌ Nothing is currently playing.")

    @app_commands.command(name="queue", description="Show the current queue")
    async def queue_cmd(self, interaction: discord.Interaction) -> None:
        state = self.states.get(interaction.guild.id)
        async with state.lock:
            if not state.current and not state.queue:
                embed = None
            else:
                embed, page, _ = build_queue_embed(state, 0)
        if embed is None:
            await interaction.response.send_message("📋 The queue is empty.")
            return

        view = QueueView(self.states, guild_id=interaction.guild.id, page=page)
        view.refresh_buttons(interaction.guild)
        await interaction.response.send_message(embed=embed, view=view)

    @app_commands.command(
        name="remove",
        description="Remove a track from the queue by position",
    )
    @app_commands.describe(position="The queue position to remove (e.g. 2)")
    @voice_check(require_bot=True)
    async def remove(
        self,
        interaction: discord.Interaction,
        position: int,
    ) -> None:
        state = self.states.get(interaction.guild.id)
        async with state.lock:
            if not state.queue:
                error_message = "❌ The queue is empty."
                removed = None
            elif position < 1 or position > len(state.queue):
                error_message = (
                    f"❌ Invalid position. Queue has {len(state.queue)} track(s)."
                )
                removed = None
            else:
                queue = list(state.queue)
                removed = queue.pop(position - 1)
                state.queue = deque(queue)
                error_message = None
                self._clear_prefetch_locked(state)
                self._schedule_prefetch_locked(state)

        if error_message:
            await send_ephemeral(interaction, error_message)
        else:
            await interaction.response.send_message(
                f"🗑️ Removed **{removed['title']}** from the queue."
            )

    @app_commands.command(
        name="clear",
        description="Clear all tracks from the queue (keeps current track playing)",
    )
    @voice_check(require_bot=True)
    async def clear(self, interaction: discord.Interaction) -> None:
        state = self.states.get(interaction.guild.id)
        async with state.lock:
            state.queue.clear()
            self._clear_prefetch_locked(state)
        await interaction.response.send_message("🗑️ Queue cleared.")

    @app_commands.command(
        name="loop",
        description="Toggle looping of the current track",
    )
    @voice_check(require_bot=True)
    async def loop_cmd(self, interaction: discord.Interaction) -> None:
        state = self.states.get(interaction.guild.id)
        async with state.lock:
            state.loop = not state.loop
            loop_enabled = state.loop
            current = state.current
            view = state.now_playing_view
            message = state.now_playing_message

        status = "enabled 🔁" if loop_enabled else "disabled"
        if view:
            button = discord.utils.get(view.children, custom_id="np_loop")
            if button:
                button.label = "Loop: On" if loop_enabled else "Loop: Off"
                button.style = (
                    discord.ButtonStyle.success
                    if loop_enabled
                    else discord.ButtonStyle.secondary
                )
            if current and message:
                try:
                    await message.edit(
                        embed=make_now_playing_embed(current, state),
                        view=view,
                    )
                except Exception:
                    logger.debug("Could not update the loop control", exc_info=True)
        await interaction.response.send_message(
            f"🔁 Loop {status}.",
            ephemeral=True,
        )

    @app_commands.command(name="pause", description="Pause the current track")
    @voice_check(require_bot=True)
    async def pause(self, interaction: discord.Interaction) -> None:
        access = get_voice_access(interaction)
        voice_client = access.voice_client
        if voice_client and voice_client.is_playing():
            voice_client.pause()
            state = self.states.get(interaction.guild.id)
            if state.now_playing_view:
                button = discord.utils.get(
                    state.now_playing_view.children,
                    custom_id="np_pause",
                )
                if button:
                    button.emoji = discord.PartialEmoji.from_str("▶️")
                    button.label = "Resume"
                    button.style = discord.ButtonStyle.success
                await sync_now_playing(state)
            await interaction.response.send_message("⏸️ Paused.", ephemeral=True)
        else:
            await send_ephemeral(interaction, "❌ Nothing is currently playing.")

    @app_commands.command(name="resume", description="Resume paused audio")
    @voice_check(require_bot=True)
    async def resume(self, interaction: discord.Interaction) -> None:
        access = get_voice_access(interaction)
        voice_client = access.voice_client
        if voice_client and voice_client.is_paused():
            voice_client.resume()
            state = self.states.get(interaction.guild.id)
            if state.now_playing_view:
                button = discord.utils.get(
                    state.now_playing_view.children,
                    custom_id="np_pause",
                )
                if button:
                    button.emoji = discord.PartialEmoji.from_str("⏸️")
                    button.label = "Pause"
                    button.style = discord.ButtonStyle.secondary
                await sync_now_playing(state)
            await interaction.response.send_message("▶️ Resumed.", ephemeral=True)
        else:
            await send_ephemeral(interaction, "❌ Audio is not paused.")

    @app_commands.command(
        name="stop",
        description="Stop playback, clear the queue, and disconnect",
    )
    @voice_check(require_bot=True)
    async def stop(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer()
        await self._reset_playback(
            interaction.guild,
            disconnect=True,
            update_message=True,
        )
        await interaction.followup.send(
            "⏹️ Stopped playback, cleared queue, and disconnected."
        )

    @app_commands.command(
        name="nowplaying",
        description="Show the currently playing track",
    )
    async def nowplaying(self, interaction: discord.Interaction) -> None:
        state = self.states.get(interaction.guild.id)
        async with state.lock:
            embed = (
                make_now_playing_embed(state.current, state)
                if state.current
                else None
            )
        if embed:
            await interaction.response.send_message(embed=embed)
        else:
            await interaction.response.send_message(
                "❌ Nothing is currently playing."
            )

    @app_commands.command(name="help", description="Show all available commands")
    async def help_cmd(self, interaction: discord.Interaction) -> None:
        embed = discord.Embed(
            title="🎵 Music Bot Help",
            description="Here are all available music commands.",
            color=discord.Color.blurple(),
        )
        embed.add_field(
            name="▶️ Playback",
            value=(
                "`/play <url or search>` — Add a supported track, playlist, or search\n"
                "`/pause` — Pause the current track\n"
                "`/resume` — Resume a paused track\n"
                "`/skip` — Skip to the next track in the queue\n"
                "`/stop` — Stop playback, clear the queue, and disconnect\n"
            ),
            inline=False,
        )
        embed.add_field(
            name="📋 Queue",
            value=(
                "`/queue` — Show the current queue\n"
                "`/remove <position>` — Remove a track by its queue position\n"
                "`/clear` — Clear the queue (keeps current track playing)\n"
                "`/loop` — Toggle looping of the current track 🔁\n"
            ),
            inline=False,
        )
        embed.add_field(
            name="ℹ️ Info",
            value=(
                "`/nowplaying` — Show the currently playing track\n"
                "`/help` — Show this help message\n"
            ),
            inline=False,
        )
        embed.set_footer(
            text="Supports YouTube, Spotify & SoundCloud • Audio streamed via yt-dlp"
        )
        await interaction.response.send_message(embed=embed)

    @commands.Cog.listener()
    async def on_voice_state_update(
        self,
        member: discord.Member,
        before: discord.VoiceState,
        after: discord.VoiceState,
    ) -> None:
        if (
            self.bot.user
            and member.id == self.bot.user.id
            and before.channel
            and after.channel is None
        ):
            await self._reset_playback(
                member.guild,
                disconnect=False,
                update_message=True,
            )
            return

        voice_client = member.guild.voice_client
        if not voice_client or not voice_client.is_connected():
            return

        state = self.states.get(member.guild.id)
        bot_channel = voice_client.channel
        human_members = [
            channel_member
            for channel_member in bot_channel.members
            if not channel_member.bot
        ]

        if not human_members:
            async with state.lock:
                if state.empty_channel_task and not state.empty_channel_task.done():
                    return

                async def disconnect_if_empty() -> None:
                    try:
                        await asyncio.sleep(300)
                        current_voice = member.guild.voice_client
                        if (
                            current_voice
                            and current_voice.is_connected()
                            and current_voice.channel == bot_channel
                            and not any(
                                not channel_member.bot
                                for channel_member in bot_channel.members
                            )
                        ):
                            text_channel = await self._reset_playback(
                                member.guild,
                                disconnect=True,
                                update_message=True,
                            )
                            if text_channel:
                                await text_channel.send(
                                    "👋 Left the voice channel due to inactivity "
                                    "(5 minutes)."
                                )
                    except asyncio.CancelledError:
                        pass
                    except Exception:
                        logger.exception("Inactivity disconnect failed")
                    finally:
                        async with state.lock:
                            if state.empty_channel_task is asyncio.current_task():
                                state.empty_channel_task = None

                state.empty_channel_task = asyncio.create_task(
                    disconnect_if_empty()
                )
        else:
            async with state.lock:
                self._cancel_task(state.empty_channel_task)
                state.empty_channel_task = None

    @commands.Cog.listener()
    async def on_guild_remove(self, guild: discord.Guild) -> None:
        await self._reset_playback(
            guild,
            disconnect=True,
            update_message=False,
        )
        self.states.pop(guild.id)
