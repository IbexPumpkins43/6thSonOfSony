import unittest

from sixth_son_of_sony.bot import MusicBot
from sixth_son_of_sony.config import Settings
from sixth_son_of_sony.music import MusicCog


EXPECTED_COMMANDS = {
    "clear",
    "help",
    "loop",
    "nowplaying",
    "pause",
    "play",
    "queue",
    "remove",
    "resume",
    "skip",
    "stop",
}

VOICE_MUTATING_COMMANDS = {
    "clear",
    "loop",
    "pause",
    "play",
    "remove",
    "resume",
    "skip",
    "stop",
}


class BotRegistrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_registers_only_music_commands_with_voice_checks(self):
        settings = Settings("discord", "spotify-id", "spotify-secret")
        bot = MusicBot(settings)
        await bot.add_cog(MusicCog(bot, settings))
        commands = {command.name: command for command in bot.tree.get_commands()}

        self.assertEqual(set(commands), EXPECTED_COMMANDS)
        for name in VOICE_MUTATING_COMMANDS:
            self.assertEqual(len(commands[name].checks), 1, name)
        for name in EXPECTED_COMMANDS - VOICE_MUTATING_COMMANDS:
            self.assertEqual(len(commands[name].checks), 0, name)
