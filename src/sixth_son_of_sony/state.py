"""Per-guild music playback state."""

import asyncio
from collections import deque

import discord


class GuildMusicState:
    def __init__(self) -> None:
        self.lock = asyncio.Lock()
        self.queue: deque[dict] = deque()
        self.current: dict | None = None
        self.loop: bool = False
        self.generation: int = 0
        self.starting: bool = False
        self.idle_notified: bool = True
        self.text_channel = None
        self.empty_channel_task: asyncio.Task | None = None
        self.prefetch_task: asyncio.Task | None = None
        self.prefetched: dict | None = None
        self.now_playing_message: discord.Message | None = None
        self.now_playing_view: discord.ui.View | None = None


class MusicStateStore:
    def __init__(self) -> None:
        self._states: dict[int, GuildMusicState] = {}

    def get(self, guild_id: int) -> GuildMusicState:
        if guild_id not in self._states:
            self._states[guild_id] = GuildMusicState()
        return self._states[guild_id]

    def pop(self, guild_id: int) -> GuildMusicState | None:
        return self._states.pop(guild_id, None)
