# 6thSonOfSony

A focused Discord music bot with per-server queues, interactive playback controls, and YouTube and Spotify link support.

Spotify is used for track and playlist metadata. Audio for Spotify tracks is found on YouTube and streamed through `yt-dlp` and FFmpeg.

## Features

- Play YouTube URLs, YouTube playlists, Spotify tracks, Spotify albums, Spotify playlists, or plain-text searches
- Per-server queues with pagination, removal, clearing, looping, and next-track prefetching
- Buttons for pause, resume, skip, stop, and loop
- Same-channel authorization for every command or button that changes playback
- Concurrency-safe queue transitions and cleanup of stale background work
- Automatic voice-channel disconnect after five minutes of inactivity

## Requirements

- Python 3.10 or newer
- [FFmpeg](https://ffmpeg.org/download.html) available on your system `PATH`
- [Deno](https://docs.deno.com/runtime/getting_started/installation/) recommended for full YouTube support in current `yt-dlp` releases
- A Discord application and bot token
- A Spotify developer application with a client ID and client secret

You can verify the external tools after installing them:

```text
ffmpeg -version
deno --version
```

## Discord setup

1. Open the [Discord Developer Portal](https://discord.com/developers/applications) and create an application.
2. Open the application's **Bot** page, create the bot, and reset/copy its token.
3. Open **OAuth2 > URL Generator**.
4. Select the `bot` and `applications.commands` scopes.
5. Give the bot these permissions:
   - View Channels
   - Send Messages
   - Embed Links
   - Connect
   - Speak
6. Open the generated URL and add the bot to your server.

The bot uses slash commands, so the privileged Message Content intent is not required.

## Spotify setup

1. Sign in to the [Spotify Developer Dashboard](https://developer.spotify.com/dashboard).
2. Create an application.
3. Open its settings and copy the client ID and client secret.

The bot uses Spotify's client-credentials flow. It does not ask server members to sign in to Spotify.

## Installation

Clone the repository, then create an isolated Python environment and install the pinned dependencies.

### Windows PowerShell

```powershell
py -3.10 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
Copy-Item .env.example .env
```

### Linux or macOS

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
cp .env.example .env
```

Open the new `.env` file and add the credentials created above:

```dotenv
DISCORD_TOKEN=your_discord_bot_token
SPOTIFY_CLIENT_ID=your_spotify_client_id
SPOTIFY_CLIENT_SECRET=your_spotify_client_secret
```

Do not commit `.env` or share its contents. It is excluded by `.gitignore`.

## Running the bot

Activate the virtual environment, then run the installed package:

```text
python -m sixth_son_of_sony
```

The `sixth-son-of-sony` console command and the original `python 6thSonOfSony.py` command are also available after installation.

When startup succeeds, the terminal will show the bot account and confirm that its slash commands were synced. Global Discord commands can take a little while to appear after their first sync.

## Project structure

Application code lives in `src/sixth_son_of_sony`:

| Module | Responsibility |
| --- | --- |
| `checks.py` | Shared voice-channel authorization |
| `config.py` | Environment settings and FFmpeg/yt-dlp options |
| `media.py` | Spotify metadata and YouTube stream resolution |
| `state.py` | Per-server queue and playback state |
| `views.py` | Now-playing and queue embeds/buttons |
| `music.py` | Playback orchestration and slash commands |
| `bot.py` | Discord bot creation and command registration |
| `__main__.py` | Application entry point |

The top-level `6thSonOfSony.py` file is retained as a small compatibility launcher.

## Commands

### Music

| Command | Description |
| --- | --- |
| `/play <url or search>` | Add a YouTube/Spotify link or search result to the queue |
| `/pause` | Pause playback |
| `/resume` | Resume playback |
| `/skip` | Skip the current track |
| `/stop` | Stop, clear the queue, and disconnect |
| `/queue` | Show the queue |
| `/remove <position>` | Remove a queued track |
| `/clear` | Clear queued tracks without stopping the current track |
| `/loop` | Toggle looping for the current track |
| `/nowplaying` | Show the current track |
| `/help` | Show the music command list in Discord |

## Troubleshooting

### Missing environment variables

If startup reports missing variables, confirm that `.env` is beside `6thSonOfSony.py`, that all three values are filled in, and that there are no extra spaces around the variable names.

The old `discord_token.txt` and `spotify_token.txt` files are no longer used. After copying their values into `.env`, they can be removed from your machine.

### FFmpeg is not found

Install the FFmpeg application, then restart the terminal and run `ffmpeg -version`. Installing a Python package named `ffmpeg` is not a substitute for the FFmpeg executable.

### YouTube extraction stops working

YouTube changes frequently. First confirm that FFmpeg and Deno are available. If extraction still fails, check for a newer stable `yt-dlp` release, update its pinned version in `pyproject.toml`, and reinstall the requirements.

### The bot connects but cannot play audio

Confirm that the bot has **Connect** and **Speak** permissions in that voice channel and that the channel has not reached its user limit.

### Slash commands do not appear

Confirm that the bot was invited with the `applications.commands` scope. Restart the bot to sync commands again, then allow time for Discord's global command registration to propagate.

## Dependency updates

Direct runtime dependencies in `pyproject.toml` are pinned so installs stay predictable; `requirements.txt` installs the project in editable mode. Upgrade dependencies deliberately and test music playback, Spotify resolution, and the interactive controls before committing updated pins. `yt-dlp` usually needs updates more often than the other packages because supported websites change independently of this project.

## Tests

The automated tests do not connect to Discord, Spotify, or YouTube. Run them from the project root with the virtual environment active:

```text
python -m unittest discover -s tests -v
```
