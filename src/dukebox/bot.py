"""Discord bot construction and lifecycle hooks."""

import discord
from discord.ext import commands

from .config import Settings
from .music import MusicCog


class MusicBot(commands.Bot):
    def __init__(self, settings: Settings) -> None:
        super().__init__(
            command_prefix="/",
            intents=discord.Intents.default(),
            help_command=None,
        )
        self.settings = settings
        self.synced_command_count = 0

    async def setup_hook(self) -> None:
        await self.add_cog(MusicCog(self, self.settings))
        synced_commands = await self.tree.sync()
        self.synced_command_count = len(synced_commands)

    async def on_ready(self) -> None:
        print(f"✅ Logged in as {self.user} (ID: {self.user.id})")
        print(f"Synced {self.synced_command_count} global slash commands.")


def create_bot(settings: Settings) -> MusicBot:
    return MusicBot(settings)
