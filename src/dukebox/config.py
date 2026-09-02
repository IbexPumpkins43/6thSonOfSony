"""Application configuration loaded from environment variables."""

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


PROJECT_ROOT = Path(__file__).resolve().parents[2]

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
    "extractor_args": {
        "soundcloud": {
            "formats": ["hls_aac"],
        }
    },
}

YDL_PLAYLIST_OPTIONS = {
    "extract_flat": "in_playlist",
    "noplaylist": False,
    "ignoreerrors": True,
    "quiet": True,
    "no_warnings": True,
    "source_address": "0.0.0.0",
}


@dataclass(frozen=True)
class Settings:
    discord_token: str
    spotify_client_id: str
    spotify_client_secret: str


def load_settings(dotenv_path: Path | None = None) -> Settings:
    """Load credentials from the process environment and the local .env file."""
    if dotenv_path is not None:
        load_dotenv(dotenv_path)
    else:
        working_directory_env = Path.cwd() / ".env"
        project_env = PROJECT_ROOT / ".env"
        load_dotenv(working_directory_env)
        if project_env != working_directory_env:
            load_dotenv(project_env)

    required_names = (
        "DISCORD_TOKEN",
        "SPOTIFY_CLIENT_ID",
        "SPOTIFY_CLIENT_SECRET",
    )
    environment = {name: os.getenv(name, "").strip() for name in required_names}
    missing = [name for name, value in environment.items() if not value]

    if missing:
        missing_names = ", ".join(missing)
        raise RuntimeError(
            f"Missing required environment variable(s): {missing_names}. "
            "Copy .env.example to .env and add your credentials."
        )

    return Settings(
        discord_token=environment["DISCORD_TOKEN"],
        spotify_client_id=environment["SPOTIFY_CLIENT_ID"],
        spotify_client_secret=environment["SPOTIFY_CLIENT_SECRET"],
    )
