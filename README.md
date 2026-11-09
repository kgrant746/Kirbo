# Kirbo

Kirbo is a custom-built Discord music bot developed in Python

## Website controller

Run `start_bot.bat` on the bot computer to start Discord and the website together.
Follow [WEBSITE_SETUP.md](WEBSITE_SETUP.md) to configure Discord sign-in and
connect `https://bigboicucks.org` through an HTTPS tunnel. All website access
requires membership in the server identified by `GUILD_ID`.

The shared music controller includes a live queue, playback controls, collaborative
playlist editing, Spotify imports and a user activity log. Discord music commands
use the same player. Existing text playlists migrate automatically into private
SQLite storage in `data/`; their original files are preserved.

## Key Features

    - Slash commands: /play, /pause, /resume, /skip, and /stop
    - YouTube search and stream using yt-dlp

## Project Structure

The project is organized into three primary files:
    - bot.py: The main entry point that initializes the bot and loads modules
    - config.py: Stores constants, environment variables, and version info
    - music.py: Handles all music commands, queuing logic, and audio streaming

### Additional files:

- .env.example: A template file for required environment variables
- CHANGELOG.md: Version history and updates

## Configuration

To run Kirbo, environment variables are required. Create a .env file using .env.example as a guide. You’ll need to provide:

    KIRBO_TOKEN: Your Discord bot token
    GUILD_ID: The ID of the Discord server (guild) where the bot will be used
    FFMPEG_PATH: The full path to your local ffmpeg.exe file

These values are automatically loaded at runtime via python-dotenv.

## License

This project uses the MIT License. External tools like yt-dlp and FFmpeg each have their own licenses (Unlicensed and LGPL/GPL respectively).
