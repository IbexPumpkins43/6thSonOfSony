# Dukebox

Dukebox is a deliberately small prototype Discord music bot written in Rust.

## Features

- Slash commands
- Join / leave voice
- Play YouTube URLs
- Play SoundCloud URLs through `yt-dlp`
- Search YouTube when `/play` receives plain text
- Spotify **track** URL resolution:
  - reads track metadata from Spotify's Web API
  - searches for a playable equivalent
- Queue
- Pause / resume / skip / stop
- Uses Songbird for Discord voice

## Configure

Create a `.env` file with:

```env
DISCORD_TOKEN=your_discord_bot_token
SPOTIFY_CLIENT_ID=your_spotify_client_id
SPOTIFY_CLIENT_SECRET=your_spotify_client_secret
RUST_LOG=info
```

## Run

```bash
cargo run --release
```

The bot registers slash commands globally. Discord can sometimes take a little while to surface newly registered global commands.

## Nix

Dukebox includes a Nix flake.

Enter the development environment:

```bash
nix develop
```

This provides Rust, Cargo, Clippy, rustfmt, Opus, FFmpeg, `pkg-config`, and `yt-dlp`.

Run Dukebox from the development shell:

```bash
cargo run --release
```

Or run it directly through the flake:

```bash
nix run
```

The first Cargo run will generate `Cargo.lock` if one does not already exist. Once the dependency set is stable, `Cargo.lock` should be committed so Dukebox can also be packaged reproducibly with `rustPlatform.buildRustPackage`.

## Commands

| Command | Description |
|---|---|
| `/join` | Joins the voice channel you are currently in. |
| `/leave` | Disconnects Dukebox from the voice channel. |
| `/play <query-or-url>` | Queues a YouTube or SoundCloud URL, a Spotify track URL, or searches YouTube from plain text. |
| `/pause` | Pauses the current track. |
| `/resume` | Resumes the paused track. |
| `/skip` | Skips the current track and moves to the next queued item. |
| `/stop` | Stops playback and clears the queue. |
| `/queue` | Shows how many tracks are currently queued. |
| `/ping` | Checks whether the bot is responding. |

## Architecture

```text
Discord
   |
   v
Poise / Serenity
   |
   v
Songbird
   ^
   |
48 kHz stereo PCM
   ^
   |
 FFmpeg
   ^
   |
 yt-dlp URL resolution
   |
   +--> YouTube
   |
   +--> SoundCloud
   |
   +--> Spotify Web API --> metadata --> YouTube search
```

For a large production bot, the next architectural decision would be whether to keep direct Songbird playback or move media playback behind Lavalink/audio workers.

## TODO

1. Spotify albums + playlists.
2. Store rich queue metadata.
3. Better matching of Spotify songs to playable results.
4. Handle voice reconnects and idle disconnects.
5. Add `/nowplaying`.
6. Add per-guild volume.
7. Add structured errors and health metrics.
8. Load tests with many guild queues.
9. Consider Lavalink nodes once concurrent playback becomes large.
