import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from sixth_son_of_sony.config import load_settings


class SettingsTests(unittest.TestCase):
    def test_bot_modules_import_without_credentials(self):
        environment = os.environ.copy()
        environment.pop("DISCORD_TOKEN", None)
        environment.pop("SPOTIFY_CLIENT_ID", None)
        environment.pop("SPOTIFY_CLIENT_SECRET", None)
        with tempfile.TemporaryDirectory() as directory:
            result = subprocess.run(
                [sys.executable, "-c", "import sixth_son_of_sony.bot"],
                cwd=directory,
                env=environment,
                capture_output=True,
                text=True,
                check=False,
            )

        self.assertEqual(result.returncode, 0, result.stderr)

    def test_loads_explicit_dotenv_file(self):
        with tempfile.TemporaryDirectory() as directory:
            dotenv_path = Path(directory) / ".env"
            dotenv_path.write_text(
                "DISCORD_TOKEN=discord\n"
                "SPOTIFY_CLIENT_ID=spotify-id\n"
                "SPOTIFY_CLIENT_SECRET=spotify-secret\n",
                encoding="utf-8",
            )
            with patch.dict(os.environ, {}, clear=True):
                settings = load_settings(dotenv_path)

        self.assertEqual(settings.discord_token, "discord")
        self.assertEqual(settings.spotify_client_id, "spotify-id")
        self.assertEqual(settings.spotify_client_secret, "spotify-secret")

    def test_reports_all_missing_settings(self):
        with tempfile.TemporaryDirectory() as directory:
            dotenv_path = Path(directory) / ".env"
            dotenv_path.write_text("", encoding="utf-8")
            with patch.dict(os.environ, {}, clear=True):
                with self.assertRaises(RuntimeError) as raised:
                    load_settings(dotenv_path)

        message = str(raised.exception)
        self.assertIn("DISCORD_TOKEN", message)
        self.assertIn("SPOTIFY_CLIENT_ID", message)
        self.assertIn("SPOTIFY_CLIENT_SECRET", message)
