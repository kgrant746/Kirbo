from __future__ import annotations
import asyncio
import random
from collections import deque
from typing import Dict, Deque, Tuple, Optional

import discord
from discord.ext import commands
from discord import app_commands
import yt_dlp
import spotipy
import tempfile

import config
import private


SONG_QUEUES: Dict[str, Deque[Tuple[str, str]]] = {}

async def _search_ytdlp(query: str) -> dict:
    """Return yt-dlp info dict for a single track or ytsearch1 result, with SABR-resistant client fallbacks."""
    import yt_dlp
    from yt_dlp.utils import DownloadError, ExtractorError

    client_chain = ["tv_embedded", "android", "web_creator"]  # avoid plain "web"
    last_err = None
    loop = asyncio.get_running_loop()

    for client in client_chain:
        opts = {
            "format": "ba/bestaudio/best",
            "noplaylist": True,
            # Do not force-disable manifests; let yt-dlp pick workable ones
            "extractor_args": {"youtube": {"player_client": client}},
            "quiet": True,
            "no_warnings": True,
        }
        try:
            return await loop.run_in_executor(
                None, lambda: yt_dlp.YoutubeDL(opts).extract_info(query, download=False)
            )
        except (DownloadError, ExtractorError) as e:
            msg = str(e)
            # SABR or missing URL indicators. Try next client.
            if ("SABR" in msg) or ("missing a url" in msg) or ("Requested format is not available" in msg):
                last_err = e
                continue
            # Other error. Bubble up.
            raise
    # If all clients failed, raise the last error so callers can message the user.
    raise last_err if last_err else RuntimeError("yt-dlp failed with unknown error")


def setup_music(bot: commands.Bot | discord.Bot) -> None:
    spotify = spotipy.Spotify(auth_manager=spotipy.SpotifyClientCredentials(
        client_id=config.SPOTIFY_CLIENT_ID,
        client_secret=config.SPOTIFY_CLIENT_SECRET
    ))
    
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

    @bot.tree.command(name="queue", description="Lists out the songs currently queued to play.", guilds=guilds)
    async def queue(interaction: discord.Interaction):
        guild_id = str(interaction.guild_id)
        queue = SONG_QUEUES.get(guild_id)

        if not queue or len(queue) == 0:
            await interaction.response.send_message("The song queue is currently empty.")
            return

        message_lines = []
        for idx, (_, title) in enumerate(queue):
            message_lines.append(f"{idx + 1}. {title}")

        message = "\n".join(message_lines)
        if len(message) > 2000:
            with tempfile.NamedTemporaryFile(mode="w+", delete=False, suffix=".txt") as temp_file:
                temp_file.write(message)
                temp_file_path = temp_file.name
            await interaction.response.send_message("The queue is too long to display here. See the attached file.", file=discord.File(temp_file_path))
        else:
            await interaction.response.send_message(f"Current Song Queue:\n{message}") 

    @bot.tree.command(name="nowplaying", description="Gets the currently playing song.", guilds=guilds)
    async def nowplaying(interaction: discord.Interaction):
        voice_client = interaction.guild.voice_client

        if voice_client is None or not (voice_client.is_playing() or voice_client.is_paused()):
            await interaction.response.send_message("No song is currently playing.")
            return

        guild_id = str(interaction.guild_id)
        queue = SONG_QUEUES.get(guild_id)

        if queue and len(queue) > 0:
            current_song = queue[0][1]
            await interaction.response.send_message(f"Currently playing: **{current_song}**")
        else:
            await interaction.response.send_message("Currently playing a song, but the title is unknown.")

    @bot.tree.command(name="playlist", description="Queue the songs from a *PUBLIC* Spotify playlist (doesn't work if playlist is private).", guilds=guilds)
    @app_commands.describe(spotify_url="Link to a Spotify playlist", shuffle="Whether to randomize track order")
    async def playlist(interaction: discord.Interaction, spotify_url: str, shuffle: bool = False):
        await interaction.response.defer()
        try:
            raw_id = spotify_url.rstrip("/").split("/")[-1]
            playlist_id = raw_id.split("?")[0]
        except Exception:
            return await interaction.followup.send("Couldn't parse a playlist ID from that URL.", ephemeral=True)

        items = []
        results = spotify.playlist_items(
            playlist_id,
            fields="items.track(name,artists(name)),next",
            additional_types=["track"]
        )
        for it in results["items"]:
            t = it["track"]
            if t and t.get("name"):
                artists = ", ".join(a["name"] for a in t["artists"])
                items.append((t["name"], artists))

        if not items:
            return await interaction.followup.send("No tracks found in that playlist.", ephemeral=True)

        if shuffle:
            random.shuffle(items)

        voice_channel = interaction.user.voice.channel
        if not voice_channel:
            return await interaction.followup.send("You must be in a voice channel.")
        voice_client = interaction.guild.voice_client or await voice_channel.connect()
        if voice_client.channel.id != voice_channel.id:
            await voice_client.move_to(voice_channel)

        guild_id = str(interaction.guild_id)
        if guild_id not in SONG_QUEUES:
            SONG_QUEUES[guild_id] = deque()

        first_name, first_artists = items.pop(0)
        first_query = f"ytsearch1:{first_name} – {first_artists}"
        try:
            first_info = await _search_ytdlp(first_query)
            first_entry = first_info["entries"][0] if "entries" in first_info else first_info
        except Exception:
            return await interaction.followup.send("Failed to enqueue the first track.", ephemeral=True)

        SONG_QUEUES[guild_id].append((first_entry["url"], first_entry.get("title", first_name)))

        if not (voice_client.is_playing() or voice_client.is_paused()):
            await interaction.followup.send(f"Now playing: **{first_entry.get('title', first_name)}**")
            await play_next_song(voice_client, guild_id, interaction.channel, post_now_playing=False)
        else:
            await interaction.followup.send(f"Added **{first_entry.get('title', first_name)}** to the queue.")
        print(f"Qeueued: {first_artists} – {first_name}")

        async def enqueue_rest():
            count = 1
            for name, artists in items:
                q = f"ytsearch1:{artists} – {name}"
                try:
                    info = await _search_ytdlp(q)
                    entry = info["entries"][0] if "entries" in info else info
                except Exception:
                    continue
                SONG_QUEUES[guild_id].append((entry["url"], entry.get("title", name)))
                print(f"Qeueued: {artists} – {name}")
                count += 1
            await interaction.channel.send(f"Queued **{count}** tracks.")

        asyncio.create_task(enqueue_rest())

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
