from __future__ import annotations
import asyncio
from collections import deque
from typing import Dict, Deque, Tuple
import discord
from discord.ext import commands
from discord import app_commands
import yt_dlp

import config
import private


SONG_QUEUES: Dict[str, Deque[Tuple[str, str]]] = {}

async def _search_ytdlp(query: str) -> dict:
    """Return yt-dlp info dict for a single track or ytsearch1 result."""
    opts = {
        "format": "bestaudio/best",
        "noplaylist": True,
        "youtube_include_dash_manifest": False,
        "youtube_include_hls_manifest": False,
        "extractor_args": {"youtube": {"player_skip": "ads", "client": "android"}},
    }

    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(
        None, lambda: yt_dlp.YoutubeDL(opts).extract_info(query, download=False)
    )


def setup_music(bot: commands.Bot | discord.Bot) -> None:
    tree = bot.tree
    guilds = [discord.Object(id=config.GUILD_ID)]

    #-------------------------- /play --------------------------
    @bot.tree.command(name="play", description="Play a song or add it to the queue.", guilds=guilds)
    @app_commands.describe(song_query="Search query")
    async def play(interaction: discord.Interaction, song_query: str):

        if interaction.user.id in private.BLACKLISTED_USERS:
            await interaction.response.send_message(private.BLACKLISTED_MESSAGE)
            return

        await interaction.response.defer()

        if interaction.user.id in private.SPECIAL_USERS:
            await interaction.followup.send(private.SPECIAL_MESSAGE)

        voice_channel = interaction.user.voice.channel
        if voice_channel is None:
            await interaction.followup.send("You must be in a voice channel.")
            return

        voice_client = interaction.guild.voice_client
        if voice_client is None:
            voice_client = await voice_channel.connect()
        elif voice_client.channel.id != voice_channel.id:
            voice_client = await voice_client.move_to(voice_channel)

        if song_query.startswith("http://") or song_query.startswith("https://"):
            query = song_query
        else:
            query = f"ytsearch1:{song_query}"

        results = await _search_ytdlp(query)

        if "entries" in results:
            tracks = results.get("entries", [])
            if not tracks:
                await interaction.followup.send("No results found.")
                return
            first_track = tracks[0]
        else:
            first_track = results

        audio_url = first_track["url"]
        title = first_track.get("title", "Untitled")

        guild_id = str(interaction.guild_id)
        if SONG_QUEUES.get(guild_id) is None:
            SONG_QUEUES[guild_id] = deque()

        SONG_QUEUES[guild_id].append((audio_url, title))

        if voice_client.is_playing() or voice_client.is_paused():
            await interaction.followup.send(f"Added to queue: **{title}**")
        else:
            await interaction.followup.send(f"Now playing: **{title}**")
            await play_next_song(voice_client, guild_id, interaction.channel, post_now_playing=False)

    #-------------------------- /skip --------------------------
    @bot.tree.command(name="skip", description="Skips the current playing song", guilds=guilds)
    async def skip(interaction: discord.Interaction):
        if interaction.guild.voice_client and (interaction.guild.voice_client.is_playing() or interaction.guild.voice_client.is_paused()):
            interaction.guild.voice_client.stop()
            await interaction.response.send_message("Skipped the current song.")
        else:
            await interaction.response.send_message("Not playing anything to skip.")

    #-------------------------- /pause --------------------------
    @bot.tree.command(name="pause", description="Pause the currently playing song.", guilds=guilds)
    async def pause(interaction: discord.Interaction):
        voice_client = interaction.guild.voice_client
        
        if voice_client is None:
            return await interaction.response.send_message("I'm not in a voice channel.")
        
        if not voice_client.is_playing():
            return await interaction.response.send_message("Nothing is currently playing.")
        
        voice_client.pause()
        await interaction.response.send_message("Playback paused.")

    #-------------------------- /resume --------------------------
    @bot.tree.command(name="resume", description="Resume the currently paused song.", guilds=guilds)
    async def resume(interaction: discord.Interaction):
        voice_client = interaction.guild.voice_client
        
        if voice_client is None:
            return await interaction.response.send_message("I'm not in a voice channel.")
        
        if not voice_client.is_paused():
            return await interaction.response.send_message("I'm not paused right now.")
        
        voice_client.resume()
        await interaction.response.send_message("Playback resumed.")

    #-------------------------- /stop --------------------------
    @bot.tree.command(name="stop", description="Stop playback and clear the queue.", guilds=guilds)
    async def stop(interaction: discord.Interaction):
        await interaction.response.defer()
        voice_client = interaction.guild.voice_client

        if not voice_client or not voice_client.is_connected():
            await interaction.followup.send("I'm not in a voice channel.")
            return
        
        guild_id_str = str(interaction.guild_id)
        if guild_id_str in SONG_QUEUES:
            SONG_QUEUES[guild_id_str].clear()

        if voice_client.is_playing() or voice_client.is_paused():
            voice_client.stop()

        await interaction.followup.send("Stopped playback and disconnected.")

        await voice_client.disconnect()

    #-------------------------- helper functions --------------------------
    async def play_next_song(voice_client, guild_id, channel, post_now_playing=True):
        if SONG_QUEUES[guild_id]:
            audio_url, title = SONG_QUEUES[guild_id].popleft()

            ffmpeg_options = {
                "before_options": "-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5",
                "options": '-vn -af "loudnorm=I=-16:TP=-1.5:LRA=11" -c:a libopus -b:a 96k',
            }

            source = discord.FFmpegOpusAudio(audio_url, **ffmpeg_options, executable= config.FFMPEG_PATH)

            def after_play(error):
                if error:
                    print(f"Error playing {title}: {error}")
                asyncio.run_coroutine_threadsafe(
                    play_next_song(voice_client, guild_id, channel, post_now_playing=True),
                    bot.loop
                )

            voice_client.play(source, after=after_play)

            if post_now_playing:
                asyncio.create_task(channel.send(f"Now playing: **{title}**"))

        else:
            await asyncio.sleep(config.DISCONNECT_TIMEOUT)
            await voice_client.disconnect()
            SONG_QUEUES[guild_id].clear()
