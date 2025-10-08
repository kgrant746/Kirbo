# Kirbo

Kirbo is a custom-built Discord music bot developed in Python

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