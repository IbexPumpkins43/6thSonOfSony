import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from sixth_son_of_sony.config import Settings
from sixth_son_of_sony.music import MusicCog


class FakeVoiceClient:
    def __init__(self):
        self.connected = True
        self.playing = False
        self.paused = False
        self.play_calls = []
        self.disconnected = False

    def is_connected(self):
        return self.connected

    def is_playing(self):
        return self.playing

    def is_paused(self):
        return self.paused

    def play(self, source, *, after):
        self.play_calls.append((source, after))
        self.playing = True

    def stop(self):
        self.playing = False
        self.paused = False

    async def disconnect(self, *, force=False):
        self.connected = False
        self.disconnected = True


def make_cog_and_guild(loop):
    settings = Settings("discord", "spotify-id", "spotify-secret")
    bot = SimpleNamespace(loop=loop, user=None)
    cog = MusicCog(bot, settings)
    voice_client = FakeVoiceClient()
    guild = SimpleNamespace(id=123, voice_client=voice_client)
    return cog, guild, voice_client


def audio_info(title="Resolved"):
    return {
        "stream_url": "https://stream.example/audio",
        "title": title,
        "webpage_url": "https://youtube.example/watch",
        "duration": 180,
    }


class PlaybackConcurrencyTests(unittest.IsolatedAsyncioTestCase):
    async def test_concurrent_advances_start_only_one_track(self):
        cog, guild, voice_client = make_cog_and_guild(asyncio.get_running_loop())
        state = cog.states.get(guild.id)
        first = {"webpage_url": "first", "title": "First", "duration": 1}
        second = {"webpage_url": "second", "title": "Second", "duration": 1}
        state.queue.extend([first, second])
        state.idle_notified = False
        cog.media.fetch_audio_info = Mock(return_value=audio_info())
        source = Mock()

        with patch(
            "sixth_son_of_sony.music.discord.FFmpegPCMAudio",
            return_value=source,
        ):
            await asyncio.gather(
                cog.play_next(guild),
                cog.play_next(guild),
            )

        self.assertEqual(len(voice_client.play_calls), 1)
        self.assertIs(state.current, first)
        self.assertEqual(list(state.queue), [second])
        self.assertFalse(state.starting)
        async with state.lock:
            cog._clear_prefetch_locked(state)
        await asyncio.sleep(0)

    async def test_reset_invalidates_an_inflight_track_resolution(self):
        cog, guild, voice_client = make_cog_and_guild(asyncio.get_running_loop())
        state = cog.states.get(guild.id)
        track = {"webpage_url": "first", "title": "First", "duration": 1}
        state.queue.append(track)
        state.idle_notified = False
        started = asyncio.Event()
        release = asyncio.Event()

        async def delayed_to_thread(function, *args):
            started.set()
            await release.wait()
            return audio_info()

        source = Mock()
        with (
            patch("sixth_son_of_sony.music.asyncio.to_thread", delayed_to_thread),
            patch(
                "sixth_son_of_sony.music.discord.FFmpegPCMAudio",
                return_value=source,
            ),
        ):
            play_task = asyncio.create_task(cog.play_next(guild))
            await started.wait()
            await cog._reset_playback(
                guild,
                disconnect=False,
                update_message=False,
            )
            release.set()
            await play_task

        self.assertEqual(voice_client.play_calls, [])
        self.assertIsNone(state.current)
        self.assertEqual(list(state.queue), [])
        source.cleanup.assert_called_once()

    async def test_reset_cancels_background_tasks_and_disconnects(self):
        cog, guild, voice_client = make_cog_and_guild(asyncio.get_running_loop())
        state = cog.states.get(guild.id)
        prefetch = asyncio.create_task(asyncio.sleep(60))
        empty_timer = asyncio.create_task(asyncio.sleep(60))
        state.prefetch_task = prefetch
        state.empty_channel_task = empty_timer
        state.queue.append({"title": "Queued"})
        state.current = {"title": "Current"}

        await cog._reset_playback(
            guild,
            disconnect=True,
            update_message=False,
        )
        await asyncio.sleep(0)

        self.assertTrue(prefetch.cancelled())
        self.assertTrue(empty_timer.cancelled())
        self.assertTrue(voice_client.disconnected)
        self.assertEqual(state.generation, 1)
        self.assertIsNone(state.current)
        self.assertEqual(list(state.queue), [])
