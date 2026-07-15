"""yt-dlp collection extraction and Spotify-to-YouTube resolution."""

import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

import spotipy
import yt_dlp
from spotipy.oauth2 import SpotifyClientCredentials

from .config import Settings, YDL_OPTIONS, YDL_PLAYLIST_OPTIONS


SPOTIFY_TRACK_RE = re.compile(r"open\.spotify\.com/track/([A-Za-z0-9]+)")
SPOTIFY_ALBUM_RE = re.compile(r"open\.spotify\.com/album/([A-Za-z0-9]+)")
SPOTIFY_PLAYLIST_RE = re.compile(r"open\.spotify\.com/playlist/([A-Za-z0-9]+)")


@dataclass(frozen=True)
class MediaCollection:
    title: str
    platform: str
    tracks: list[dict]


def is_spotify_url(url: str) -> bool:
    return "open.spotify.com" in url


def is_http_url(value: str) -> bool:
    parsed = urlparse(value.strip())
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def _platform_name(info: dict) -> str:
    extractor = str(info.get("extractor_key") or info.get("extractor") or "")
    normalized = extractor.lower()
    if "youtube" in normalized:
        return "YouTube"
    if "soundcloud" in normalized:
        return "SoundCloud"
    if extractor:
        return extractor.replace("_", " ").strip()
    return "media"


def _entry_webpage_url(entry: dict) -> str | None:
    for key in ("webpage_url", "original_url", "url"):
        candidate = entry.get(key)
        if isinstance(candidate, str) and is_http_url(candidate):
            return candidate

    extractor = str(entry.get("ie_key") or entry.get("extractor_key") or "")
    entry_id = entry.get("id")
    if entry_id and "youtube" in extractor.lower():
        return f"https://www.youtube.com/watch?v={entry_id}"
    return None


class MediaResolver:
    def __init__(self, settings: Settings) -> None:
        self.spotify = spotipy.Spotify(
            auth_manager=SpotifyClientCredentials(
                client_id=settings.spotify_client_id,
                client_secret=settings.spotify_client_secret,
            )
        )

    @staticmethod
    def fetch_collection(url: str) -> MediaCollection | None:
        """Return a flat collection for any direct URL supported by yt-dlp."""
        with yt_dlp.YoutubeDL(YDL_PLAYLIST_OPTIONS) as ydl:
            info = ydl.extract_info(url, download=False)

        entries = info.get("entries")
        if entries is None:
            return None

        tracks = []
        for entry in entries:
            if not entry:
                continue
            webpage_url = _entry_webpage_url(entry)
            if not webpage_url:
                continue
            tracks.append(
                {
                    "webpage_url": webpage_url,
                    "title": entry.get("title") or "Unknown Title",
                    "duration": entry.get("duration") or 0,
                }
            )
        return MediaCollection(
            title=info.get("title") or "Untitled playlist",
            platform=_platform_name(info),
            tracks=tracks,
        )

    @staticmethod
    def _spotify_request(
        fn: Callable[..., Any],
        *args,
        retries: int = 3,
        delay: int = 2,
        **kwargs,
    ) -> Any:
        """Call a Spotipy function with retries and incremental backoff."""
        last_error: Exception | None = None
        for attempt in range(retries):
            try:
                return fn(*args, **kwargs)
            except Exception as error:
                last_error = error
                if attempt < retries - 1:
                    time.sleep(delay * (attempt + 1))

        if last_error is not None:
            raise last_error
        raise RuntimeError("Spotify request failed without an error response.")

    def resolve_spotify(self, url: str) -> list[dict]:
        """Convert a Spotify track, album, or playlist into YouTube searches."""
        tracks = []

        track_match = SPOTIFY_TRACK_RE.search(url)
        album_match = SPOTIFY_ALBUM_RE.search(url)
        playlist_match = SPOTIFY_PLAYLIST_RE.search(url)

        if track_match:
            track = self._spotify_request(self.spotify.track, track_match.group(1))
            artist = track["artists"][0]["name"]
            title = track["name"]
            tracks.append(
                {
                    "search_query": f"{artist} - {title}",
                    "title": f"{title} — {artist}",
                    "duration": track["duration_ms"] // 1000,
                }
            )

        elif album_match:
            album = self._spotify_request(self.spotify.album, album_match.group(1))
            for item in album["tracks"]["items"]:
                artist = item["artists"][0]["name"]
                title = item["name"]
                tracks.append(
                    {
                        "search_query": f"{artist} - {title}",
                        "title": f"{title} — {artist}",
                        "duration": item["duration_ms"] // 1000,
                    }
                )

        elif playlist_match:
            results = self._spotify_request(
                self.spotify.playlist_items,
                playlist_match.group(1),
                additional_types=["track"],
            )
            while results:
                for item in results["items"]:
                    track = item.get("track")
                    if not track:
                        continue
                    artist = track["artists"][0]["name"]
                    title = track["name"]
                    tracks.append(
                        {
                            "search_query": f"{artist} - {title}",
                            "title": f"{title} — {artist}",
                            "duration": track["duration_ms"] // 1000,
                        }
                    )

                if results["next"]:
                    time.sleep(0.5)
                    results = self._spotify_request(self.spotify.next, results)
                else:
                    results = None

        return tracks

    @staticmethod
    def fetch_audio_info(url_or_query: str) -> dict:
        """Resolve a URL or search query to a playable stream and its metadata."""
        options = dict(YDL_OPTIONS)
        if not url_or_query.startswith("http"):
            options["default_search"] = "ytsearch"
            url_or_query = f"{url_or_query} audio"

        with yt_dlp.YoutubeDL(options) as ydl:
            info = ydl.extract_info(url_or_query, download=False)
            if "entries" in info:
                info = info["entries"][0]
            return {
                "stream_url": info["url"],
                "title": info.get("title") or "Unknown Title",
                "webpage_url": info.get("webpage_url", url_or_query),
                "duration": info.get("duration") or 0,
            }
