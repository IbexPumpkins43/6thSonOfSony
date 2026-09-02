"""Shared authorization checks for Discord interactions."""

from dataclasses import dataclass
from typing import Any

import discord
from discord import app_commands


@dataclass(frozen=True)
class VoiceAccess:
    channel: Any
    voice_client: discord.VoiceClient | None


VOICE_ACCESS_KEY = "dukebox_voice_access"


async def send_ephemeral(interaction: discord.Interaction, message: str) -> None:
    """Send an ephemeral response regardless of response state."""
    if interaction.response.is_done():
        await interaction.followup.send(message, ephemeral=True)
    else:
        await interaction.response.send_message(message, ephemeral=True)


async def require_same_voice(
    interaction: discord.Interaction,
    *,
    require_bot: bool,
) -> VoiceAccess | None:
    """Require the user to share the bot's voice channel for state changes."""
    guild = interaction.guild
    if guild is None:
        await send_ephemeral(interaction, "❌ This command can only be used in a server.")
        return None

    member = guild.get_member(interaction.user.id)
    user_channel = member.voice.channel if member and member.voice else None
    if user_channel is None:
        await send_ephemeral(interaction, "❌ Join a voice channel first.")
        return None

    voice_client = guild.voice_client
    if voice_client and not voice_client.is_connected():
        voice_client = None

    if require_bot and voice_client is None:
        await send_ephemeral(
            interaction,
            "❌ The bot is not connected to voice. Start playback with `/play`.",
        )
        return None

    if voice_client and voice_client.channel != user_channel:
        await send_ephemeral(
            interaction,
            "❌ Join the bot's voice channel before changing playback or the queue.",
        )
        return None

    return VoiceAccess(channel=user_channel, voice_client=voice_client)


def voice_check(*, require_bot: bool):
    """Create an app-command check that stores the validated voice context."""

    async def predicate(interaction: discord.Interaction) -> bool:
        access = await require_same_voice(interaction, require_bot=require_bot)
        if access is None:
            return False
        interaction.extras[VOICE_ACCESS_KEY] = access
        return True

    return app_commands.check(predicate)


def get_voice_access(interaction: discord.Interaction) -> VoiceAccess:
    return interaction.extras[VOICE_ACCESS_KEY]
