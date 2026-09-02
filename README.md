# dukebox

A focused Discord music bot with per-server queues, interactive playback controls, and YouTube, Spotify, and SoundCloud support.

Spotify is used for track and playlist metadata. Audio for Spotify tracks is found on YouTube and streamed through `yt-dlp` and FFmpeg.

## Features

- Play YouTube tracks/playlists, Spotify tracks/albums/playlists, SoundCloud tracks/sets/playlists, or plain-text searches
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

## Supported media

- **YouTube:** individual videos, playlists, and YouTube Music playlist URLs
- **Spotify:** tracks, albums, and playlists; audio is matched and streamed from YouTube
- **SoundCloud:** individual tracks, sets, and playlists

Other direct collection URLs supported by the installed `yt-dlp` backend are also detected automatically. Availability depends on the source website and the corresponding `yt-dlp` extractor.

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

The `sixth-son-of-sony` console command and the original `python dukebox.py` command are also available after installation.

When startup succeeds, the terminal will show the bot account and confirm that its slash commands were synced. Global Discord commands can take a little while to appear after their first sync.

## Commands

### Music

| Command | Description |
| --- | --- |
| `/play <url or search>` | Add a supported track, playlist, album, set, or search result |
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

## Tests

The automated tests do not connect to Discord, Spotify, or YouTube. Run them from the project root with the virtual environment active:

```text
python -m unittest discover -s tests -v
```
