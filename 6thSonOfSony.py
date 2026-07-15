import discord
from discord import app_commands
from discord.ext import commands
import yt_dlp
import asyncio
import re
import random
import html
import aiohttp
from collections import deque
import spotipy
from spotipy.oauth2 import SpotifyClientCredentials

# --- Configuration ---
FFMPEG_OPTIONS = {
    "before_options": "-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5",
    "options": "-vn",
}

YDL_OPTIONS = {
    "format": "bestaudio/best",
    "noplaylist": True,
    "quiet": False,
    "no_warnings": False,
    "default_search": "auto",
    "source_address": "0.0.0.0",
    "extractor-args": {
        "soundcloud": {
            "formats": ["hls_aac"]
        }
    }
}

YDL_PLAYLIST_OPTIONS = {
    "extract_flat": "in_playlist",
    "quiet": True,
    "no_warnings": True,
    "source_address": "0.0.0.0",
}

# --- Bot Setup ---
intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(command_prefix="/", intents=intents, help_command=None)


# --- Spotify Client Setup ---
# Reads credentials from spotify.txt — two lines: client_id on line 1, client_secret on line 2
with open("spotify_token.txt", "r") as f:
    lines = f.read().splitlines()
    SPOTIFY_CLIENT_ID = lines[0].strip()
    SPOTIFY_CLIENT_SECRET = lines[1].strip()

sp = spotipy.Spotify(auth_manager=SpotifyClientCredentials(
    client_id=SPOTIFY_CLIENT_ID,
    client_secret=SPOTIFY_CLIENT_SECRET,
))


# --- Spotify URL Detection ---
SPOTIFY_TRACK_RE = re.compile(r"open\.spotify\.com/track/([A-Za-z0-9]+)")
SPOTIFY_ALBUM_RE = re.compile(r"open\.spotify\.com/album/([A-Za-z0-9]+)")
SPOTIFY_PLAYLIST_RE = re.compile(r"open\.spotify\.com/playlist/([A-Za-z0-9]+)")

def is_spotify_url(url: str) -> bool:
    return "open.spotify.com" in url

# --- YouTube Playlist Detection & Resolution ---
YT_PLAYLIST_RE = re.compile(r"youtube\.com/.*[?&]list=([A-Za-z0-9_-]+)")

def is_youtube_playlist(url: str) -> bool:
    return bool(YT_PLAYLIST_RE.search(url))

def fetch_youtube_playlist(url: str) -> list[dict]:
    """Returns a flat list of tracks from a YouTube playlist without fetching stream URLs."""
    with yt_dlp.YoutubeDL(YDL_PLAYLIST_OPTIONS) as ydl:
        info = ydl.extract_info(url, download=False)
    tracks = []
    for entry in info.get("entries", []):
        if not entry or not entry.get("id"):
            continue
        tracks.append({
            "webpage_url": f"https://www.youtube.com/watch?v={entry['id']}",
            "title": entry.get("title", "Unknown Title"),
            "duration": entry.get("duration", 0),
        })
    return tracks


# --- Spotify: Resolve URL to list of search queries ---
def spotify_request(fn, *args, retries=3, delay=2, **kwargs):
    """Calls a spotipy function with automatic retries on connection errors."""
    import time
    last_error = None
    for attempt in range(retries):
        try:
            return fn(*args, **kwargs)
        except Exception as e:
            last_error = e
            if attempt < retries - 1:
                time.sleep(delay * (attempt + 1))  # back off: 2s, 4s
    raise last_error


def resolve_spotify(url: str) -> list[dict]:
    """
    Returns a list of dicts with 'search_query' and 'title' for each track.
    Handles single tracks, albums, and playlists.
    """
    import time
    tracks = []

    if SPOTIFY_TRACK_RE.search(url):
        # Single track
        match = SPOTIFY_TRACK_RE.search(url)
        track = spotify_request(sp.track, match.group(1))
        artist = track["artists"][0]["name"]
        title = track["name"]
        tracks.append({
            "search_query": f"{artist} - {title}",
            "title": f"{title} — {artist}",
            "duration": track["duration_ms"] // 1000,
        })

    elif SPOTIFY_ALBUM_RE.search(url):
        # Album — fetch all tracks
        match = SPOTIFY_ALBUM_RE.search(url)
        album = spotify_request(sp.album, match.group(1))
        for item in album["tracks"]["items"]:
            artist = item["artists"][0]["name"]
            title = item["name"]
            tracks.append({
                "search_query": f"{artist} - {title}",
                "title": f"{title} — {artist}",
                "duration": item["duration_ms"] // 1000,
            })

    elif SPOTIFY_PLAYLIST_RE.search(url):
        # Playlist — paginate through all tracks with retries per page
        match = SPOTIFY_PLAYLIST_RE.search(url)
        results = spotify_request(sp.playlist_items, match.group(1), additional_types=["track"])
        while results:
            for item in results["items"]:
                track = item.get("track")
                if not track:
                    continue
                artist = track["artists"][0]["name"]
                title = track["name"]
                tracks.append({
                    "search_query": f"{artist} - {title}",
                    "title": f"{title} — {artist}",
                    "duration": track["duration_ms"] // 1000,
                })
            if results["next"]:
                time.sleep(0.5)  # small pause between pages to avoid rate limiting
                results = spotify_request(sp.next, results)
            else:
                results = None

    return tracks


# --- Per-guild music state ---
class GuildMusicState:
    def __init__(self):
        self.queue: deque[dict] = deque()   # Each item: {"webpage_url", "title", "duration", "requester"}
        self.current: dict | None = None    # Currently playing track
        self.loop: bool = False             # Loop current track
        self.text_channel = None            # Channel to send now-playing messages
        self.empty_channel_task: asyncio.Task | None = None  # Auto-disconnect timer
        self.prefetch_task: asyncio.Task | None = None       # Background pre-fetch for next track
        self.prefetched: dict | None = None                  # Result of pre-fetch: includes stream_url
        self.now_playing_message: discord.Message | None = None
        self.now_playing_view: "NowPlayingView | None" = None

music_states: dict[int, GuildMusicState] = {}

def get_state(guild_id: int) -> GuildMusicState:
    if guild_id not in music_states:
        music_states[guild_id] = GuildMusicState()
    return music_states[guild_id]


# --- Helper: Extract audio info from YouTube (URL or search query) ---
def fetch_audio_info(url_or_query: str) -> dict:
    """Returns dict with stream URL, title, duration, and webpage URL.
    Accepts a YouTube URL or a plain text search query."""
    opts = dict(YDL_OPTIONS)
    # If it's not a URL, prefix with ytsearch: so yt-dlp treats it as a search.
    # Appending "audio" biases YouTube results toward official audio uploads over music videos.
    if not url_or_query.startswith("http"):
        opts["default_search"] = "ytsearch"
        url_or_query = f"{url_or_query} audio"
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url_or_query, download=False)
        # yt-dlp wraps search results in an 'entries' list
        if "entries" in info:
            info = info["entries"][0]
        return {
            "stream_url": info["url"],
            "title": info.get("title", "Unknown Title"),
            "webpage_url": info.get("webpage_url", url_or_query),
            "duration": info.get("duration", 0),
        }


def format_duration(seconds: int) -> str:
    if not seconds:
        return "?"
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    return f"{h}:{m:02}:{s:02}" if h else f"{m}:{s:02}"


# --- Music Controls UI ---

def make_now_playing_embed(track: dict, state: GuildMusicState) -> discord.Embed:
    duration_str = format_duration(track.get("duration", 0))
    queue_len = len(state.queue)
    embed = discord.Embed(
        title="▶️ Now Playing",
        description=f"### {track['title']}",
        color=discord.Color.blurple(),
    )
    embed.add_field(name="⏱️ Duration", value=f"`{duration_str}`", inline=True)
    if track.get("requester"):
        embed.add_field(name="👤 Requested by", value=track["requester"], inline=True)
    if queue_len:
        embed.add_field(name="📋 Up next", value=f"{queue_len} track{'s' if queue_len != 1 else ''}", inline=True)
    loop_note = " • 🔁 Loop on" if state.loop else ""
    embed.set_footer(text=f"Use the buttons below to control playback{loop_note}")
    return embed


class NowPlayingView(discord.ui.View):
    def __init__(self, guild: discord.Guild):
        super().__init__(timeout=None)
        self.guild = guild

    async def _in_voice(self, interaction: discord.Interaction) -> bool:
        member = interaction.guild.get_member(interaction.user.id)
        vc = interaction.guild.voice_client
        if not vc or not member.voice or member.voice.channel != vc.channel:
            await interaction.response.send_message("❌ Join the voice channel first.", ephemeral=True)
            return False
        return True

    def _disable_all(self):
        for item in self.children:
            item.disabled = True

    @discord.ui.button(emoji="⏸️", label="Pause", style=discord.ButtonStyle.secondary, custom_id="np_pause", row=0)
    async def pause_resume(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._in_voice(interaction):
            return
        vc = interaction.guild.voice_client
        if not vc:
            await interaction.response.defer()
            return
        if vc.is_playing():
            vc.pause()
            button.emoji = discord.PartialEmoji.from_str("▶️")
            button.label = "Resume"
            button.style = discord.ButtonStyle.success
        elif vc.is_paused():
            vc.resume()
            button.emoji = discord.PartialEmoji.from_str("⏸️")
            button.label = "Pause"
            button.style = discord.ButtonStyle.secondary
        await interaction.response.edit_message(view=self)

    @discord.ui.button(emoji="⏭️", label="Skip", style=discord.ButtonStyle.secondary, custom_id="np_skip", row=0)
    async def skip_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._in_voice(interaction):
            return
        vc = interaction.guild.voice_client
        if vc and (vc.is_playing() or vc.is_paused()):
            await interaction.response.defer()
            vc.stop()
        else:
            await interaction.response.send_message("❌ Nothing is playing.", ephemeral=True)

    @discord.ui.button(emoji="⏹️", label="Stop", style=discord.ButtonStyle.danger, custom_id="np_stop", row=0)
    async def stop_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._in_voice(interaction):
            return
        state = get_state(interaction.guild.id)
        state.queue.clear()
        state.current = None
        state.loop = False
        if state.prefetch_task and not state.prefetch_task.done():
            state.prefetch_task.cancel()
        state.prefetched = None
        vc = interaction.guild.voice_client
        if vc and vc.is_connected():
            vc.stop()
            await vc.disconnect()
        self._disable_all()
        await interaction.response.edit_message(view=self)
        self.stop()

    @discord.ui.button(emoji="🔁", label="Loop: Off", style=discord.ButtonStyle.secondary, custom_id="np_loop", row=0)
    async def loop_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._in_voice(interaction):
            return
        state = get_state(interaction.guild.id)
        state.loop = not state.loop
        button.label = "Loop: On" if state.loop else "Loop: Off"
        button.style = discord.ButtonStyle.success if state.loop else discord.ButtonStyle.secondary
        # Rebuild embed footer to reflect loop state
        if state.current and state.now_playing_message:
            embed = make_now_playing_embed(state.current, state)
            await interaction.response.edit_message(embed=embed, view=self)
        else:
            await interaction.response.edit_message(view=self)


async def _sync_now_playing(state: GuildMusicState):
    """Push the current view state to the now-playing message (used by slash commands)."""
    if state.now_playing_view and state.now_playing_message:
        try:
            await state.now_playing_message.edit(view=state.now_playing_view)
        except Exception:
            pass


# --- Core: Play next track in queue ---
async def play_next(guild: discord.Guild):
    state = get_state(guild.id)
    voice_client = guild.voice_client

    if not voice_client or not voice_client.is_connected():
        return

    # Handle loop: re-queue the current track at the front
    if state.loop and state.current:
        state.queue.appendleft(state.current)

    if not state.queue:
        state.current = None
        # Disable controls on the last now-playing message
        if state.now_playing_view and state.now_playing_message:
            state.now_playing_view._disable_all()
            try:
                await state.now_playing_message.edit(view=state.now_playing_view)
            except Exception:
                pass
            state.now_playing_view = None
            state.now_playing_message = None
        if state.text_channel:
            await state.text_channel.send("✅ Queue finished! Add more songs with `/play`.")
        return

    # Cancel any stale pre-fetch (e.g. user skipped)
    if state.prefetch_task and not state.prefetch_task.done():
        state.prefetch_task.cancel()

    # Pop next track
    track = state.queue.popleft()
    state.current = track

    # Use pre-fetched stream info if it matches this track, otherwise fetch now.
    # For Spotify tracks, webpage_url is a search query — yt-dlp will search YouTube.
    try:
        if state.prefetched and state.prefetched.get("webpage_url") == track["webpage_url"]:
            info = state.prefetched
        else:
            event_loop = asyncio.get_event_loop()
            info = await event_loop.run_in_executor(None, fetch_audio_info, track["webpage_url"])
        state.prefetched = None
        stream_url = info["stream_url"]
        # Update title/duration with the actual YouTube result for Spotify tracks
        if track.get("spotify"):
            track["title"] = info["title"]
            track["duration"] = info["duration"]
    except Exception as e:
        state.prefetched = None
        if state.text_channel:
            await state.text_channel.send(f"❌ Skipping **{track['title']}** — failed to fetch audio: `{e}`")
        await play_next(guild)
        return

    # Pre-fetch the next track in the background while this one plays
    if state.queue:
        next_track = state.queue[0]
        async def _prefetch(webpage_url: str):
            try:
                event_loop = asyncio.get_event_loop()
                result = await event_loop.run_in_executor(None, fetch_audio_info, webpage_url)
                result["webpage_url"] = webpage_url
                state.prefetched = result
            except Exception:
                state.prefetched = None
        state.prefetch_task = asyncio.create_task(_prefetch(next_track["webpage_url"]))

    def after_play(error):
        if error:
            print(f"Playback error: {error}")
        asyncio.run_coroutine_threadsafe(play_next(guild), bot.loop)

    source = discord.FFmpegPCMAudio(stream_url, **FFMPEG_OPTIONS)
    voice_client.play(source, after=after_play)

    if state.text_channel:
        # Disable controls on the previous now-playing message
        if state.now_playing_view and state.now_playing_message:
            state.now_playing_view._disable_all()
            try:
                await state.now_playing_message.edit(view=state.now_playing_view)
            except Exception:
                pass

        embed = make_now_playing_embed(track, state)
        view = NowPlayingView(guild)
        msg = await state.text_channel.send(embed=embed, view=view)
        state.now_playing_message = msg
        state.now_playing_view = view


# --- /play command ---
@bot.tree.command(name="play", description="Add a YouTube/Spotify URL or search by name to the queue")
@app_commands.describe(query="A YouTube/Spotify URL, or search terms like 'alice in chains'")
async def play(interaction: discord.Interaction, query: str):
    await interaction.response.defer()

    member = interaction.guild.get_member(interaction.user.id)
    if not member.voice or not member.voice.channel:
        await interaction.followup.send("❌ You need to be in a voice channel first!")
        return

    voice_channel = member.voice.channel
    voice_client = interaction.guild.voice_client

    if voice_client:
        if voice_client.channel != voice_channel:
            await voice_client.move_to(voice_channel)
    else:
        voice_client = await voice_channel.connect()

    state = get_state(interaction.guild.id)
    state.text_channel = interaction.channel

    # --- Spotify handling ---
    if is_spotify_url(query):
        await interaction.followup.send("🎵 Resolving Spotify link...")
        try:
            loop = asyncio.get_event_loop()
            spotify_tracks = await loop.run_in_executor(None, resolve_spotify, query)
        except Exception as e:
            await interaction.followup.send(f"❌ Failed to resolve Spotify link: `{e}`")
            return

        if not spotify_tracks:
            await interaction.followup.send("❌ No tracks found from that Spotify link.")
            return

        for spotify_track in spotify_tracks:
            state.queue.append({
                "webpage_url": spotify_track["search_query"],  # used as search query at play time
                "title": spotify_track["title"],
                "duration": spotify_track["duration"],
                "requester": str(interaction.user.display_name),
                "spotify": True,
            })

        count = len(spotify_tracks)
        if count == 1:
            await interaction.followup.send(f"➕ Added to queue: **{spotify_tracks[0]['title']}**")
        else:
            await interaction.followup.send(f"➕ Added **{count} tracks** from Spotify to the queue.")

        if not voice_client.is_playing() and not voice_client.is_paused():
            await play_next(interaction.guild)
        return

    # --- YouTube playlist handling ---
    if is_youtube_playlist(query):
        await interaction.followup.send("📋 Fetching YouTube playlist...")
        try:
            event_loop = asyncio.get_event_loop()
            yt_tracks = await event_loop.run_in_executor(None, fetch_youtube_playlist, query)
        except Exception as e:
            await interaction.followup.send(f"❌ Failed to load playlist: `{e}`")
            return

        if not yt_tracks:
            await interaction.followup.send("❌ No tracks found in that playlist.")
            return

        for yt_track in yt_tracks:
            state.queue.append({
                "webpage_url": yt_track["webpage_url"],
                "title": yt_track["title"],
                "duration": yt_track["duration"],
                "requester": str(interaction.user.display_name),
            })

        await interaction.followup.send(f"➕ Added **{len(yt_tracks)} tracks** from YouTube playlist to the queue.")

        if not voice_client.is_playing() and not voice_client.is_paused():
            await play_next(interaction.guild)
        return

    # --- YouTube/search handling ---
    await interaction.followup.send(f"🔍 Fetching info for `{query}`...")

    try:
        loop = asyncio.get_event_loop()
        info = await loop.run_in_executor(None, fetch_audio_info, query)
    except Exception as e:
        await interaction.followup.send(f"❌ Failed to fetch audio: `{e}`")
        return

    track = {
        "webpage_url": info["webpage_url"],
        "title": info["title"],
        "duration": info["duration"],
        "requester": str(interaction.user.display_name),
    }

    state.queue.append(track)
    duration_str = format_duration(info["duration"])

    if voice_client.is_playing() or voice_client.is_paused():
        pos = len(state.queue)
        await interaction.followup.send(
            f"➕ Added to queue (position **#{pos}**): **{track['title']}** `[{duration_str}]`"
        )
    else:
        await play_next(interaction.guild)


# --- /skip command ---
@bot.tree.command(name="skip", description="Skip the current track")
async def skip(interaction: discord.Interaction):
    voice_client = interaction.guild.voice_client
    if voice_client and (voice_client.is_playing() or voice_client.is_paused()):
        voice_client.stop()  # triggers after_play → play_next
        await interaction.response.send_message("⏭️ Skipped.")
    else:
        await interaction.response.send_message("❌ Nothing is currently playing.")


# --- /queue command ---
PAGE_SIZE = 10

def build_queue_embed(state: GuildMusicState, page: int) -> discord.Embed:
    queue_list = list(state.queue)
    total_tracks = len(queue_list)
    total_pages = max(1, (total_tracks + PAGE_SIZE - 1) // PAGE_SIZE)
    page = max(0, min(page, total_pages - 1))

    embed = discord.Embed(title="🎵 Music Queue", color=discord.Color.blurple())

    if state.current:
        duration_str = format_duration(state.current.get("duration", 0))
        loop_icon = " 🔁" if state.loop else ""
        embed.add_field(
            name=f"▶️ Now Playing{loop_icon}",
            value=f"**{state.current['title']}** `[{duration_str}]`\nRequested by {state.current.get('requester', '?')}",
            inline=False,
        )

    if queue_list:
        start = page * PAGE_SIZE
        lines = []
        for i, track in enumerate(queue_list[start:start + PAGE_SIZE], start=start + 1):
            duration_str = format_duration(track.get("duration", 0))
            lines.append(f"`{i}.` **{track['title']}** `[{duration_str}]` — {track.get('requester', '?')}")
        embed.add_field(name="📋 Up Next", value="\n".join(lines), inline=False)
    else:
        embed.add_field(name="📋 Up Next", value="*Queue is empty*", inline=False)

    total_seconds = sum(t.get("duration", 0) for t in queue_list)
    if state.current:
        total_seconds += state.current.get("duration", 0)
    total_str = format_duration(total_seconds) if total_seconds else "?"
    total_count = total_tracks + (1 if state.current else 0)
    embed.set_footer(
        text=f"Page {page + 1}/{total_pages} • {total_count} track{'s' if total_count != 1 else ''} • Total runtime: {total_str}"
    )
    return embed, page, total_pages


class QueueView(discord.ui.View):
    def __init__(self, guild_id: int, page: int = 0):
        super().__init__(timeout=60)
        self.guild_id = guild_id
        self.page = page
        self._refresh_buttons()

    def _refresh_buttons(self, guild: discord.Guild | None = None):
        state = get_state(self.guild_id)
        total_pages = max(1, (len(state.queue) + PAGE_SIZE - 1) // PAGE_SIZE)
        self.prev_btn.disabled = self.page <= 0
        self.next_btn.disabled = self.page >= total_pages - 1
        vc = guild.voice_client if guild else None
        self.skip_btn.disabled = not (vc and (vc.is_playing() or vc.is_paused()))

    @discord.ui.button(emoji="⬅️", label="Prev", style=discord.ButtonStyle.secondary)
    async def prev_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.page -= 1
        state = get_state(interaction.guild.id)
        embed, self.page, _ = build_queue_embed(state, self.page)
        self._refresh_buttons(interaction.guild)
        await interaction.response.edit_message(embed=embed, view=self)

    @discord.ui.button(emoji="➡️", label="Next", style=discord.ButtonStyle.secondary)
    async def next_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.page += 1
        state = get_state(interaction.guild.id)
        embed, self.page, _ = build_queue_embed(state, self.page)
        self._refresh_buttons(interaction.guild)
        await interaction.response.edit_message(embed=embed, view=self)

    @discord.ui.button(emoji="⏭️", label="Skip", style=discord.ButtonStyle.primary)
    async def skip_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        member = interaction.guild.get_member(interaction.user.id)
        vc = interaction.guild.voice_client
        if not vc or not member.voice or member.voice.channel != vc.channel:
            await interaction.response.send_message("❌ Join the voice channel first.", ephemeral=True)
            return
        if not vc or not (vc.is_playing() or vc.is_paused()):
            await interaction.response.send_message("❌ Nothing is currently playing.", ephemeral=True)
            return
        vc.stop()
        state = get_state(interaction.guild.id)
        embed, self.page, _ = build_queue_embed(state, self.page)
        self._refresh_buttons(interaction.guild)
        await interaction.response.edit_message(embed=embed, view=self)

    async def on_timeout(self):
        self.prev_btn.disabled = True
        self.next_btn.disabled = True


@bot.tree.command(name="queue", description="Show the current queue")
async def queue_cmd(interaction: discord.Interaction):
    state = get_state(interaction.guild.id)

    if not state.current and not state.queue:
        await interaction.response.send_message("📋 The queue is empty.")
        return

    embed, page, total_pages = build_queue_embed(state, 0)
    view = QueueView(guild_id=interaction.guild.id, page=page)
    view._refresh_buttons(interaction.guild)
    await interaction.response.send_message(embed=embed, view=view)


# --- /remove command ---
@bot.tree.command(name="remove", description="Remove a track from the queue by position")
@app_commands.describe(position="The queue position to remove (e.g. 2)")
async def remove(interaction: discord.Interaction, position: int):
    state = get_state(interaction.guild.id)

    if not state.queue:
        await interaction.response.send_message("❌ The queue is empty.")
        return

    if position < 1 or position > len(state.queue):
        await interaction.response.send_message(f"❌ Invalid position. Queue has {len(state.queue)} track(s).")
        return

    queue_list = list(state.queue)
    removed = queue_list.pop(position - 1)
    state.queue = deque(queue_list)
    await interaction.response.send_message(f"🗑️ Removed **{removed['title']}** from the queue.")


# --- /clear command ---
@bot.tree.command(name="clear", description="Clear all tracks from the queue (keeps current track playing)")
async def clear(interaction: discord.Interaction):
    state = get_state(interaction.guild.id)
    state.queue.clear()
    await interaction.response.send_message("🗑️ Queue cleared.")


# --- /loop command ---
@bot.tree.command(name="loop", description="Toggle looping of the current track")
async def loop_cmd(interaction: discord.Interaction):
    state = get_state(interaction.guild.id)
    state.loop = not state.loop
    status = "enabled 🔁" if state.loop else "disabled"
    # Sync button state on the now-playing message
    if state.now_playing_view:
        btn = discord.utils.get(state.now_playing_view.children, custom_id="np_loop")
        if btn:
            btn.label = "Loop: On" if state.loop else "Loop: Off"
            btn.style = discord.ButtonStyle.success if state.loop else discord.ButtonStyle.secondary
        if state.current and state.now_playing_message:
            embed = make_now_playing_embed(state.current, state)
            try:
                await state.now_playing_message.edit(embed=embed, view=state.now_playing_view)
            except Exception:
                pass
    await interaction.response.send_message(f"🔁 Loop {status}.", ephemeral=True)


# --- /pause command ---
@bot.tree.command(name="pause", description="Pause the current track")
async def pause(interaction: discord.Interaction):
    voice_client = interaction.guild.voice_client
    if voice_client and voice_client.is_playing():
        voice_client.pause()
        state = get_state(interaction.guild.id)
        if state.now_playing_view:
            btn = discord.utils.get(state.now_playing_view.children, custom_id="np_pause")
            if btn:
                btn.emoji = discord.PartialEmoji.from_str("▶️")
                btn.label = "Resume"
                btn.style = discord.ButtonStyle.success
            await _sync_now_playing(state)
        await interaction.response.send_message("⏸️ Paused.", ephemeral=True)
    else:
        await interaction.response.send_message("❌ Nothing is currently playing.", ephemeral=True)


# --- /resume command ---
@bot.tree.command(name="resume", description="Resume paused audio")
async def resume(interaction: discord.Interaction):
    voice_client = interaction.guild.voice_client
    if voice_client and voice_client.is_paused():
        voice_client.resume()
        state = get_state(interaction.guild.id)
        if state.now_playing_view:
            btn = discord.utils.get(state.now_playing_view.children, custom_id="np_pause")
            if btn:
                btn.emoji = discord.PartialEmoji.from_str("⏸️")
                btn.label = "Pause"
                btn.style = discord.ButtonStyle.secondary
            await _sync_now_playing(state)
        await interaction.response.send_message("▶️ Resumed.", ephemeral=True)
    else:
        await interaction.response.send_message("❌ Audio is not paused.", ephemeral=True)


# --- /stop command ---
@bot.tree.command(name="stop", description="Stop playback, clear the queue, and disconnect")
async def stop(interaction: discord.Interaction):
    state = get_state(interaction.guild.id)
    state.queue.clear()
    state.current = None
    state.loop = False
    if state.prefetch_task and not state.prefetch_task.done():
        state.prefetch_task.cancel()
    state.prefetched = None

    # Disable now-playing controls
    if state.now_playing_view and state.now_playing_message:
        state.now_playing_view._disable_all()
        try:
            await state.now_playing_message.edit(view=state.now_playing_view)
        except Exception:
            pass
    state.now_playing_view = None
    state.now_playing_message = None

    voice_client = interaction.guild.voice_client
    if voice_client and voice_client.is_connected():
        voice_client.stop()
        await voice_client.disconnect()
        await interaction.response.send_message("⏹️ Stopped playback, cleared queue, and disconnected.")
    else:
        await interaction.response.send_message("❌ I'm not in a voice channel.")


# --- /nowplaying command ---
@bot.tree.command(name="nowplaying", description="Show the currently playing track")
async def nowplaying(interaction: discord.Interaction):
    state = get_state(interaction.guild.id)
    if state.current:
        embed = make_now_playing_embed(state.current, state)
        await interaction.response.send_message(embed=embed)
    else:
        await interaction.response.send_message("❌ Nothing is currently playing.")


# --- /help command ---
@bot.tree.command(name="help", description="Show all available commands")
async def help_cmd(interaction: discord.Interaction):
    embed = discord.Embed(
        title="🎵 Music Bot Help",
        description="Here are all available commands.",
        color=discord.Color.blurple(),
    )

    embed.add_field(
        name="▶️ Playback",
        value=(
            "`/play <url or search>` — Add a YouTube/Spotify URL or search by name\n"
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

    embed.add_field(
        name="🎮 Fun & Games",
        value=(
            "`/coinflip` — Flip a coin\n"
            "`/roll [dice]` — Roll dice (e.g. `2d6`, `d20`)\n"
            "`/8ball <question>` — Ask the magic 8-ball\n"
            "`/rps <choice>` — Rock, paper, scissors vs the bot\n"
            "`/trivia` — Answer a random multiple-choice trivia question\n"
            "`/hangman` — Play a game of hangman\n"
            "`/dadjoke` — Get a random dad joke\n"
        ),
        inline=False,
    )

    embed.set_footer(text="Supports YouTube & Spotify links • Audio streamed via yt-dlp")
    await interaction.response.send_message(embed=embed)


# --- Auto-disconnect after 5 minutes of empty channel ---
@bot.event
async def on_voice_state_update(member: discord.Member, before: discord.VoiceState, after: discord.VoiceState):
    voice_client = member.guild.voice_client
    if not voice_client or not voice_client.is_connected():
        return

    state = get_state(member.guild.id)
    bot_channel = voice_client.channel

    # Count non-bot members in the bot's channel
    human_members = [m for m in bot_channel.members if not m.bot]

    if not human_members:
        # Channel is empty — start the 5-minute disconnect timer if not already running
        if state.empty_channel_task is None or state.empty_channel_task.done():
            async def disconnect_if_empty():
                await asyncio.sleep(300)  # 5 minutes
                # Re-check before disconnecting
                if voice_client.is_connected():
                    still_humans = [m for m in bot_channel.members if not m.bot]
                    if not still_humans:
                        state.queue.clear()
                        state.current = None
                        state.loop = False
                        await voice_client.disconnect()
                        if state.text_channel:
                            await state.text_channel.send("👋 Left the voice channel due to inactivity (5 minutes).")

            state.empty_channel_task = asyncio.create_task(disconnect_if_empty())
    else:
        # Someone is in the channel — cancel any pending disconnect
        if state.empty_channel_task and not state.empty_channel_task.done():
            state.empty_channel_task.cancel()
            state.empty_channel_task = None


# --- Fun & Games ---

# /coinflip
@bot.tree.command(name="coinflip", description="Flip a coin")
async def coinflip(interaction: discord.Interaction):
    result = random.choice(["Heads", "Tails"])
    await interaction.response.send_message(f"🪙 **{result}!**")


# /roll
@bot.tree.command(name="roll", description="Roll dice — e.g. 2d6, d20, 3d8 (default: 1d6)")
@app_commands.describe(dice="Dice notation like 2d6 or d20")
async def roll_cmd(interaction: discord.Interaction, dice: str = "1d6"):
    match = re.fullmatch(r"(\d*)d(\d+)", dice.lower().strip())
    if not match:
        await interaction.response.send_message("❌ Use dice notation like `2d6`, `d20`, or `3d8`.")
        return
    count = int(match.group(1)) if match.group(1) else 1
    sides = int(match.group(2))
    if not (1 <= count <= 20):
        await interaction.response.send_message("❌ Number of dice must be between 1 and 20.")
        return
    if not (2 <= sides <= 1000):
        await interaction.response.send_message("❌ Sides must be between 2 and 1000.")
        return
    rolls = [random.randint(1, sides) for _ in range(count)]
    total = sum(rolls)
    if count == 1:
        await interaction.response.send_message(f"🎲 Rolled **d{sides}**: **{total}**")
    else:
        rolls_str = " + ".join(str(r) for r in rolls)
        await interaction.response.send_message(f"🎲 Rolled **{count}d{sides}**: {rolls_str} = **{total}**")


# /8ball
_8BALL_RESPONSES = [
    ("It is certain.", True), ("It is decidedly so.", True), ("Without a doubt.", True),
    ("Yes, definitely.", True), ("You may rely on it.", True), ("As I see it, yes.", True),
    ("Most likely.", True), ("Outlook good.", True), ("Yes.", True), ("Signs point to yes.", True),
    ("Reply hazy, try again.", None), ("Ask again later.", None), ("Better not tell you now.", None),
    ("Cannot predict now.", None), ("Concentrate and ask again.", None),
    ("Don't count on it.", False), ("My reply is no.", False), ("My sources say no.", False),
    ("Outlook not so good.", False), ("Very doubtful.", False),
]

@bot.tree.command(name="8ball", description="Ask the magic 8-ball a question")
@app_commands.describe(question="Your yes/no question")
async def eightball(interaction: discord.Interaction, question: str):
    response, positive = random.choice(_8BALL_RESPONSES)
    emoji = "🟢" if positive is True else ("🔴" if positive is False else "🟡")
    await interaction.response.send_message(f"🎱 *{question}*\n{emoji} **{response}**")


# /rps
_RPS_BEATS = {"rock": "scissors", "scissors": "paper", "paper": "rock"}
_RPS_EMOJI = {"rock": "🪨", "paper": "📄", "scissors": "✂️"}

@bot.tree.command(name="rps", description="Play rock, paper, scissors against the bot")
@app_commands.describe(choice="Your choice")
@app_commands.choices(choice=[
    app_commands.Choice(name="Rock", value="rock"),
    app_commands.Choice(name="Paper", value="paper"),
    app_commands.Choice(name="Scissors", value="scissors"),
])
async def rps(interaction: discord.Interaction, choice: app_commands.Choice[str]):
    user = choice.value
    bot_pick = random.choice(list(_RPS_BEATS.keys()))
    user_emoji = _RPS_EMOJI[user]
    bot_emoji = _RPS_EMOJI[bot_pick]
    if user == bot_pick:
        result, icon = "It's a tie!", "🤝"
    elif _RPS_BEATS[user] == bot_pick:
        result, icon = "You win!", "🎉"
    else:
        result, icon = "You lose!", "😔"
    await interaction.response.send_message(
        f"{user_emoji} **{user.capitalize()}** vs {bot_emoji} **{bot_pick.capitalize()}**\n{icon} {result}"
    )


# /trivia
class TriviaButton(discord.ui.Button):
    def __init__(self, label: str, answer: str, triggerer_id: int):
        super().__init__(label=label, style=discord.ButtonStyle.secondary)
        self.answer = answer
        self.triggerer_id = triggerer_id

    async def callback(self, interaction: discord.Interaction):
        view: TriviaView = self.view
        if interaction.user.id != self.triggerer_id:
            await interaction.response.send_message("❌ This trivia question isn't for you!", ephemeral=True)
            return
        if view.answered:
            await interaction.response.defer()
            return
        view.answered = True
        for item in view.children:
            item.disabled = True
            if item.answer == view.correct:
                item.style = discord.ButtonStyle.success
            elif item.answer == self.answer and self.answer != view.correct:
                item.style = discord.ButtonStyle.danger
        await interaction.response.edit_message(view=view)
        if self.answer == view.correct:
            await interaction.followup.send(f"✅ Correct! The answer was **{view.correct}**.")
        else:
            await interaction.followup.send(f"❌ Wrong! The correct answer was **{view.correct}**.")


class TriviaView(discord.ui.View):
    def __init__(self, correct: str, all_answers: list[str], triggerer_id: int):
        super().__init__(timeout=30)
        self.correct = correct
        self.answered = False
        labels = ["🅐", "🅑", "🅒", "🅓"]
        for i, answer in enumerate(all_answers):
            self.add_item(TriviaButton(
                label=f"{labels[i]}  {answer[:75]}",
                answer=answer,
                triggerer_id=triggerer_id,
            ))

    async def on_timeout(self):
        for item in self.children:
            item.disabled = True


@bot.tree.command(name="trivia", description="Answer a random multiple-choice trivia question")
async def trivia(interaction: discord.Interaction):
    await interaction.response.defer()
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                "https://opentdb.com/api.php",
                params={"amount": 1, "type": "multiple"},
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                data = await resp.json()
    except Exception as e:
        await interaction.followup.send(f"❌ Failed to fetch a trivia question: `{e}`")
        return

    if data.get("response_code") != 0 or not data.get("results"):
        await interaction.followup.send("❌ Couldn't fetch a trivia question right now. Try again!")
        return

    result = data["results"][0]
    question = html.unescape(result["question"])
    correct = html.unescape(result["correct_answer"])
    all_answers = [html.unescape(a) for a in result["incorrect_answers"]] + [correct]
    random.shuffle(all_answers)
    category = html.unescape(result["category"])
    difficulty = result["difficulty"].capitalize()

    embed = discord.Embed(
        title="🧠 Trivia Time!",
        description=question,
        color=discord.Color.gold(),
    )
    embed.set_footer(text=f"{category} • {difficulty} • 30s to answer")
    view = TriviaView(correct=correct, all_answers=all_answers, triggerer_id=interaction.user.id)
    await interaction.followup.send(embed=embed, view=view)


# /hangman
HANGMAN_STAGES = [
    "```\n  +---+\n  |   |\n      |\n      |\n      |\n      |\n=========```",
    "```\n  +---+\n  |   |\n  O   |\n      |\n      |\n      |\n=========```",
    "```\n  +---+\n  |   |\n  O   |\n  |   |\n      |\n      |\n=========```",
    "```\n  +---+\n  |   |\n  O   |\n /|   |\n      |\n      |\n=========```",
    "```\n  +---+\n  |   |\n  O   |\n /|\\  |\n      |\n      |\n=========```",
    "```\n  +---+\n  |   |\n  O   |\n /|\\  |\n /    |\n      |\n=========```",
    "```\n  +---+\n  |   |\n  O   |\n /|\\  |\n / \\  |\n      |\n=========```",
]

HANGMAN_WORDS = [
    "PYTHON", "DISCORD", "GUITAR", "CASTLE", "JUNGLE", "WIZARD", "ROCKET", "PLANET",
    "CANDLE", "DRAGON", "BRIDGE", "FROZEN", "ISLAND", "KNIGHT", "MUFFIN", "NEEDLE",
    "OYSTER", "PIRATE", "RABBIT", "SUNSET", "TIMBER", "WALRUS", "ZIPPER", "ANCHOR",
    "BUTTER", "CACTUS", "DESERT", "FEATHER", "GOBLIN", "HAMMER", "IGLOO", "JESTER",
    "KITTEN", "LANTERN", "MARBLE", "NAPKIN", "PARROT", "RIDDLE", "SILVER", "THRONE",
    "VELVET", "WEASEL", "YOGURT", "ZOMBIE", "AURORA", "BALLOON", "COMPASS", "DOLPHIN",
    "ECLIPSE", "GLACIER", "HORIZON", "ICEBERG", "MEADOW", "NEBULA", "OCTOPUS", "PENGUIN",
    "RAINBOW", "SAPPHIRE", "TORNADO", "UMBRELLA", "VOLCANO", "WHISPER", "XYLOPHONE",
    "ZEPPELIN", "LABYRINTH", "QUICKSAND",
]


class LetterModal(discord.ui.Modal, title="Guess a Letter"):
    letter = discord.ui.TextInput(
        label="Enter a single letter (A–Z)",
        min_length=1,
        max_length=1,
        placeholder="e.g. E",
    )

    def __init__(self, game_view: "HangmanView"):
        super().__init__()
        self.game_view = game_view

    async def on_submit(self, interaction: discord.Interaction):
        ch = self.letter.value.upper()
        if not ch.isalpha():
            await interaction.response.send_message("❌ Please enter a letter A–Z.", ephemeral=True)
            return
        await self.game_view.process_guess(interaction, ch)


class HangmanView(discord.ui.View):
    def __init__(self, word: str, triggerer_id: int):
        super().__init__(timeout=120)
        self.word = word.upper()
        self.triggerer_id = triggerer_id
        self.guessed: set[str] = set()
        self.wrong: int = 0
        self.max_wrong: int = 6

    @property
    def display_word(self) -> str:
        return "  ".join(c if c in self.guessed else r"\_" for c in self.word)

    @property
    def won(self) -> bool:
        return all(c in self.guessed for c in self.word)

    @property
    def lost(self) -> bool:
        return self.wrong >= self.max_wrong

    def build_embed(self) -> discord.Embed:
        if self.lost:
            color, title = discord.Color.red(), "💀 Game Over!"
        elif self.won:
            color, title = discord.Color.green(), "🎉 You Won!"
        else:
            color, title = discord.Color.blurple(), "🎯 Hangman"

        embed = discord.Embed(title=title, color=color)
        embed.description = HANGMAN_STAGES[self.wrong]
        embed.add_field(name="Word", value=f"`{self.display_word}`", inline=False)
        wrong_letters = sorted(c for c in self.guessed if c not in self.word)
        if wrong_letters:
            embed.add_field(name="Wrong Guesses", value=" ".join(wrong_letters), inline=True)
        hearts = "❤️" * (self.max_wrong - self.wrong) + "🖤" * self.wrong
        embed.add_field(name="Lives", value=hearts, inline=True)
        if self.lost:
            embed.add_field(name="The word was", value=f"**{self.word}**", inline=False)
        return embed

    @discord.ui.button(label="Guess a Letter", style=discord.ButtonStyle.primary, emoji="🔤")
    async def guess_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.triggerer_id:
            await interaction.response.send_message("❌ This isn't your game!", ephemeral=True)
            return
        await interaction.response.send_modal(LetterModal(self))

    async def process_guess(self, interaction: discord.Interaction, letter: str):
        if letter in self.guessed:
            await interaction.response.send_message(f"You already guessed **{letter}**!", ephemeral=True)
            return
        self.guessed.add(letter)
        if letter not in self.word:
            self.wrong += 1
        if self.won or self.lost:
            self.guess_btn.disabled = True
            self.guess_btn.emoji = discord.PartialEmoji.from_str("🎉" if self.won else "💀")
            self.guess_btn.label = "You Won!" if self.won else "Game Over"
            self.stop()
        await interaction.response.edit_message(embed=self.build_embed(), view=self)

    async def on_timeout(self):
        self.guess_btn.disabled = True
        self.guess_btn.emoji = discord.PartialEmoji.from_str("⏰")
        self.guess_btn.label = "Timed Out"


@bot.tree.command(name="hangman", description="Play a game of hangman")
async def hangman(interaction: discord.Interaction):
    word = random.choice(HANGMAN_WORDS)
    view = HangmanView(word=word, triggerer_id=interaction.user.id)
    await interaction.response.send_message(embed=view.build_embed(), view=view)


# /dadjoke
@bot.tree.command(name="dadjoke", description="Get a random dad joke")
async def dadjoke(interaction: discord.Interaction):
    await interaction.response.defer()
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                "https://icanhazdadjoke.com/",
                headers={"Accept": "application/json"},
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                data = await resp.json()
        await interaction.followup.send(f"😄 {data['joke']}")
    except Exception as e:
        await interaction.followup.send(f"❌ Couldn't fetch a joke right now: `{e}`")


# --- Bot ready event ---
@bot.event
async def on_ready():
    await bot.tree.sync()
    print(f"✅ Logged in as {bot.user} (ID: {bot.user.id})")
    print(f"Slash commands synced globally on startup.")


# --- Run the bot ---
# Read the token from token.txt (one line, just the token, nothing else)
with open("discord_token.txt", "r") as f:
    token = f.read().strip()

bot.run(token)
