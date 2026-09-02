"""Discord embeds and interactive controls for music playback."""

import logging
from collections.abc import Awaitable, Callable

import discord

from .checks import require_same_voice, send_ephemeral
from .state import GuildMusicState, MusicStateStore


PAGE_SIZE = 10
logger = logging.getLogger(__name__)


def format_duration(seconds: int) -> str:
    if not seconds:
        return "?"
    minutes, seconds = divmod(int(seconds), 60)
    hours, minutes = divmod(minutes, 60)
    return f"{hours}:{minutes:02}:{seconds:02}" if hours else f"{minutes}:{seconds:02}"


def make_now_playing_embed(track: dict, state: GuildMusicState) -> discord.Embed:
    duration = format_duration(track.get("duration", 0))
    queue_length = len(state.queue)
    embed = discord.Embed(
        title="▶️ Now Playing",
        description=f"### {track['title']}",
        color=discord.Color.blurple(),
    )
    embed.add_field(name="⏱️ Duration", value=f"`{duration}`", inline=True)
    if track.get("requester"):
        embed.add_field(name="👤 Requested by", value=track["requester"], inline=True)
    if queue_length:
        suffix = "s" if queue_length != 1 else ""
        embed.add_field(name="📋 Up next", value=f"{queue_length} track{suffix}", inline=True)
    loop_note = " • 🔁 Loop on" if state.loop else ""
    embed.set_footer(text=f"Use the buttons below to control playback{loop_note}")
    return embed


class MusicView(discord.ui.View):
    async def on_error(
        self,
        interaction: discord.Interaction,
        error: Exception,
        item: discord.ui.Item,
    ) -> None:
        logger.error(
            "Unhandled music control error for item %s",
            item,
            exc_info=(type(error), error, error.__traceback__),
        )
        await send_ephemeral(
            interaction,
            "❌ That control failed unexpectedly. Please try again.",
        )


class NowPlayingView(MusicView):
    def __init__(
        self,
        guild: discord.Guild,
        states: MusicStateStore,
        stop_callback: Callable[[discord.Guild], Awaitable[None]],
    ) -> None:
        super().__init__(timeout=None)
        self.guild = guild
        self.states = states
        self.stop_callback = stop_callback

    def disable_all(self) -> None:
        for item in self.children:
            item.disabled = True

    @discord.ui.button(
        emoji="⏸️",
        label="Pause",
        style=discord.ButtonStyle.secondary,
        custom_id="np_pause",
        row=0,
    )
    async def pause_resume(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        if not await require_same_voice(interaction, require_bot=True):
            return

        voice_client = interaction.guild.voice_client
        if not voice_client:
            await interaction.response.defer()
            return

        if voice_client.is_playing():
            voice_client.pause()
            button.emoji = discord.PartialEmoji.from_str("▶️")
            button.label = "Resume"
            button.style = discord.ButtonStyle.success
        elif voice_client.is_paused():
            voice_client.resume()
            button.emoji = discord.PartialEmoji.from_str("⏸️")
            button.label = "Pause"
            button.style = discord.ButtonStyle.secondary
        await interaction.response.edit_message(view=self)

    @discord.ui.button(
        emoji="⏭️",
        label="Skip",
        style=discord.ButtonStyle.secondary,
        custom_id="np_skip",
        row=0,
    )
    async def skip_btn(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        if not await require_same_voice(interaction, require_bot=True):
            return

        voice_client = interaction.guild.voice_client
        if voice_client and (voice_client.is_playing() or voice_client.is_paused()):
            await interaction.response.defer()
            voice_client.stop()
        else:
            await interaction.response.send_message(
                "❌ Nothing is playing.",
                ephemeral=True,
            )

    @discord.ui.button(
        emoji="⏹️",
        label="Stop",
        style=discord.ButtonStyle.danger,
        custom_id="np_stop",
        row=0,
    )
    async def stop_btn(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        if not await require_same_voice(interaction, require_bot=True):
            return
        await interaction.response.defer()
        await self.stop_callback(interaction.guild)
        self.disable_all()
        await interaction.edit_original_response(view=self)
        self.stop()

    @discord.ui.button(
        emoji="🔁",
        label="Loop: Off",
        style=discord.ButtonStyle.secondary,
        custom_id="np_loop",
        row=0,
    )
    async def loop_btn(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        if not await require_same_voice(interaction, require_bot=True):
            return

        state = self.states.get(interaction.guild.id)
        async with state.lock:
            state.loop = not state.loop
            loop_enabled = state.loop
            current = state.current
        button.label = "Loop: On" if loop_enabled else "Loop: Off"
        button.style = (
            discord.ButtonStyle.success
            if loop_enabled
            else discord.ButtonStyle.secondary
        )

        if current and state.now_playing_message:
            embed = make_now_playing_embed(current, state)
            await interaction.response.edit_message(embed=embed, view=self)
        else:
            await interaction.response.edit_message(view=self)


async def sync_now_playing(state: GuildMusicState) -> None:
    if state.now_playing_view and state.now_playing_message:
        try:
            await state.now_playing_message.edit(view=state.now_playing_view)
        except Exception:
            pass


def build_queue_embed(
    state: GuildMusicState,
    page: int,
) -> tuple[discord.Embed, int, int]:
    queue = list(state.queue)
    total_tracks = len(queue)
    total_pages = max(1, (total_tracks + PAGE_SIZE - 1) // PAGE_SIZE)
    page = max(0, min(page, total_pages - 1))

    embed = discord.Embed(title="🎵 Music Queue", color=discord.Color.blurple())

    if state.current:
        duration = format_duration(state.current.get("duration", 0))
        loop_icon = " 🔁" if state.loop else ""
        embed.add_field(
            name=f"▶️ Now Playing{loop_icon}",
            value=(
                f"**{state.current['title']}** `[{duration}]`\n"
                f"Requested by {state.current.get('requester', '?')}"
            ),
            inline=False,
        )

    if queue:
        start = page * PAGE_SIZE
        lines = []
        for index, track in enumerate(
            queue[start : start + PAGE_SIZE],
            start=start + 1,
        ):
            duration = format_duration(track.get("duration", 0))
            lines.append(
                f"`{index}.` **{track['title']}** `[{duration}]` — "
                f"{track.get('requester', '?')}"
            )
        embed.add_field(name="📋 Up Next", value="\n".join(lines), inline=False)
    else:
        embed.add_field(name="📋 Up Next", value="*Queue is empty*", inline=False)

    total_seconds = sum(track.get("duration", 0) for track in queue)
    if state.current:
        total_seconds += state.current.get("duration", 0)
    total_duration = format_duration(total_seconds) if total_seconds else "?"
    total_count = total_tracks + (1 if state.current else 0)
    suffix = "s" if total_count != 1 else ""
    embed.set_footer(
        text=(
            f"Page {page + 1}/{total_pages} • {total_count} track{suffix} • "
            f"Total runtime: {total_duration}"
        )
    )
    return embed, page, total_pages


class QueueView(MusicView):
    def __init__(
        self,
        states: MusicStateStore,
        guild_id: int,
        page: int = 0,
    ) -> None:
        super().__init__(timeout=60)
        self.states = states
        self.guild_id = guild_id
        self.page = page
        self.refresh_buttons()

    def refresh_buttons(self, guild: discord.Guild | None = None) -> None:
        state = self.states.get(self.guild_id)
        total_pages = max(1, (len(state.queue) + PAGE_SIZE - 1) // PAGE_SIZE)
        self.prev_btn.disabled = self.page <= 0
        self.next_btn.disabled = self.page >= total_pages - 1
        voice_client = guild.voice_client if guild else None
        self.skip_btn.disabled = not (
            voice_client and (voice_client.is_playing() or voice_client.is_paused())
        )

    @discord.ui.button(
        emoji="⬅️",
        label="Prev",
        style=discord.ButtonStyle.secondary,
    )
    async def prev_btn(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        self.page -= 1
        state = self.states.get(interaction.guild.id)
        embed, self.page, _ = build_queue_embed(state, self.page)
        self.refresh_buttons(interaction.guild)
        await interaction.response.edit_message(embed=embed, view=self)

    @discord.ui.button(
        emoji="➡️",
        label="Next",
        style=discord.ButtonStyle.secondary,
    )
    async def next_btn(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        self.page += 1
        state = self.states.get(interaction.guild.id)
        embed, self.page, _ = build_queue_embed(state, self.page)
        self.refresh_buttons(interaction.guild)
        await interaction.response.edit_message(embed=embed, view=self)

    @discord.ui.button(
        emoji="⏭️",
        label="Skip",
        style=discord.ButtonStyle.primary,
    )
    async def skip_btn(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        access = await require_same_voice(interaction, require_bot=True)
        if access is None:
            return
        voice_client = access.voice_client
        assert voice_client is not None
        if not (voice_client.is_playing() or voice_client.is_paused()):
            await interaction.response.send_message(
                "❌ Nothing is currently playing.",
                ephemeral=True,
            )
            return

        voice_client.stop()
        state = self.states.get(interaction.guild.id)
        embed, self.page, _ = build_queue_embed(state, self.page)
        self.refresh_buttons(interaction.guild)
        await interaction.response.edit_message(embed=embed, view=self)

    async def on_timeout(self) -> None:
        for item in self.children:
            item.disabled = True
