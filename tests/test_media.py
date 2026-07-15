import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from sixth_son_of_sony.media import MediaResolver, is_http_url


def youtube_dl_with(info):
    downloader = MagicMock()
    downloader.extract_info.return_value = info
    context = MagicMock()
    context.__enter__.return_value = downloader
    context.__exit__.return_value = False
    return context, downloader


class CollectionResolutionTests(unittest.TestCase):
    def test_recognizes_only_http_urls(self):
        self.assertTrue(is_http_url("https://youtube.com/watch?v=abc"))
        self.assertTrue(is_http_url("http://soundcloud.com/artist/set"))
        self.assertFalse(is_http_url("alice in chains"))
        self.assertFalse(is_http_url("youtube.com/watch?v=abc"))

    def test_single_track_is_not_treated_as_collection(self):
        context, _ = youtube_dl_with(
            {
                "id": "track",
                "title": "Single Track",
                "webpage_url": "https://example.com/track",
            }
        )
        with patch("sixth_son_of_sony.media.yt_dlp.YoutubeDL", return_value=context):
            collection = MediaResolver.fetch_collection("https://example.com/track")

        self.assertIsNone(collection)

    def test_normalizes_youtube_playlist_entries(self):
        context, downloader = youtube_dl_with(
            {
                "title": "YouTube Mix",
                "extractor_key": "YoutubeTab",
                "entries": [
                    {
                        "id": "abc123",
                        "url": "abc123",
                        "ie_key": "Youtube",
                        "title": "First Song",
                        "duration": 120,
                    },
                    None,
                ],
            }
        )
        with patch("sixth_son_of_sony.media.yt_dlp.YoutubeDL", return_value=context):
            collection = MediaResolver.fetch_collection(
                "https://youtube.com/playlist?list=playlist-id"
            )

        self.assertEqual(collection.title, "YouTube Mix")
        self.assertEqual(collection.platform, "YouTube")
        self.assertEqual(
            collection.tracks,
            [
                {
                    "webpage_url": "https://www.youtube.com/watch?v=abc123",
                    "title": "First Song",
                    "duration": 120,
                }
            ],
        )
        downloader.extract_info.assert_called_once_with(
            "https://youtube.com/playlist?list=playlist-id",
            download=False,
        )

    def test_preserves_soundcloud_set_urls(self):
        context, _ = youtube_dl_with(
            {
                "title": "SoundCloud Set",
                "extractor_key": "SoundcloudSet",
                "entries": [
                    {
                        "id": "one",
                        "url": "https://soundcloud.com/artist/track-one",
                        "title": "Track One",
                        "duration": 90,
                    },
                    {
                        "id": "two",
                        "webpage_url": "https://soundcloud.com/artist/track-two",
                        "title": "Track Two",
                        "duration": 100,
                    },
                ],
            }
        )
        with patch("sixth_son_of_sony.media.yt_dlp.YoutubeDL", return_value=context):
            collection = MediaResolver.fetch_collection(
                "https://soundcloud.com/artist/sets/example"
            )

        self.assertEqual(collection.platform, "SoundCloud")
        self.assertEqual(len(collection.tracks), 2)
        self.assertEqual(
            collection.tracks[1]["webpage_url"],
            "https://soundcloud.com/artist/track-two",
        )

    def test_spotify_playlists_are_paginated(self):
        first_page = {
            "items": [
                {
                    "track": {
                        "name": "First",
                        "artists": [{"name": "Artist One"}],
                        "duration_ms": 120000,
                    }
                }
            ],
            "next": "next-page",
        }
        second_page = {
            "items": [
                {
                    "track": {
                        "name": "Second",
                        "artists": [{"name": "Artist Two"}],
                        "duration_ms": 180000,
                    }
                }
            ],
            "next": None,
        }
        spotify = SimpleNamespace(
            playlist_items=MagicMock(return_value=first_page),
            next=MagicMock(return_value=second_page),
        )
        resolver = object.__new__(MediaResolver)
        resolver.spotify = spotify

        with patch("sixth_son_of_sony.media.time.sleep"):
            tracks = resolver.resolve_spotify(
                "https://open.spotify.com/playlist/playlist-id"
            )

        self.assertEqual(len(tracks), 2)
        self.assertEqual(tracks[0]["search_query"], "Artist One - First")
        self.assertEqual(tracks[1]["duration"], 180)
        spotify.next.assert_called_once_with(first_page)
