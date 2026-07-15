import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from sixth_son_of_sony.checks import require_same_voice


def make_interaction(*, user_channel=None, bot_channel=None, connected=True):
    member = SimpleNamespace(
        voice=SimpleNamespace(channel=user_channel) if user_channel else None
    )
    voice_client = None
    if bot_channel is not None:
        voice_client = SimpleNamespace(
            channel=bot_channel,
            is_connected=Mock(return_value=connected),
        )
    guild = SimpleNamespace(
        voice_client=voice_client,
        get_member=Mock(return_value=member),
    )
    response = SimpleNamespace(
        is_done=Mock(return_value=False),
        send_message=AsyncMock(),
    )
    return SimpleNamespace(
        guild=guild,
        user=SimpleNamespace(id=42),
        response=response,
        followup=SimpleNamespace(send=AsyncMock()),
        extras={},
    )


class VoiceCheckTests(unittest.IsolatedAsyncioTestCase):
    async def test_play_is_allowed_before_bot_connects(self):
        channel = object()
        interaction = make_interaction(user_channel=channel)

        access = await require_same_voice(interaction, require_bot=False)

        self.assertIs(access.channel, channel)
        self.assertIsNone(access.voice_client)
        interaction.response.send_message.assert_not_awaited()

    async def test_mutation_requires_connected_bot(self):
        interaction = make_interaction(user_channel=object())

        access = await require_same_voice(interaction, require_bot=True)

        self.assertIsNone(access)
        interaction.response.send_message.assert_awaited_once()

    async def test_user_must_share_bot_channel(self):
        interaction = make_interaction(
            user_channel=object(),
            bot_channel=object(),
        )

        access = await require_same_voice(interaction, require_bot=True)

        self.assertIsNone(access)
        message = interaction.response.send_message.await_args.args[0]
        self.assertIn("bot's voice channel", message)

    async def test_user_in_bot_channel_is_allowed(self):
        channel = object()
        interaction = make_interaction(
            user_channel=channel,
            bot_channel=channel,
        )

        access = await require_same_voice(interaction, require_bot=True)

        self.assertIs(access.channel, channel)
        self.assertIs(access.voice_client, interaction.guild.voice_client)
