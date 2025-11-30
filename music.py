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

from pathlib import Path

PLAYLISTS_DIR = Path(getattr(config, "PLAYLISTS_DIR", Path(__file__).parent / "playlists"))
PLAYLISTS_DIR.mkdir(parents=True, exist_ok=True)


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


def _is_spotify_playlist(s: str) -> bool:
    s = s.strip().lower()
    return (s.startswith("http://") or s.startswith("https://")) and "open.spotify.com/playlist" in s

def _load_text_playlist(filename: str) -> list[str]:
    name = filename.strip()
    if not name.lower().endswith(".txt"):
        name += ".txt"
    p = (PLAYLISTS_DIR / name).resolve()
    # prevent path traversal
    if PLAYLISTS_DIR.resolve() not in p.parents:
        raise ValueError("Invalid filename.")
    with p.open("r", encoding="utf-8") as f:
        lines = [ln.strip() for ln in f if ln.strip() and not ln.lstrip().startswith("#")]
    return lines


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


    @bot.tree.command(name="playnext", description="Insert a song to play next (requires something already playing and a non-empty queue).", guilds=guilds)
    @app_commands.describe(song_query="Search query or URL to play next")
    async def playnext(interaction: discord.Interaction, song_query: str):
        # optional: match your /play gates
        if interaction.user.id in private.BLACKLISTED_USERS:
            await interaction.response.send_message(private.BLACKLISTED_MESSAGE, ephemeral=True)
            return

        await interaction.response.defer()

        vc = interaction.guild.voice_client
        if vc is None or not (vc.is_playing() or vc.is_paused()):
            await interaction.followup.send("Nothing is currently playing. Use /play instead.", ephemeral=True)
            return

        guild_id = str(interaction.guild_id)
        q = SONG_QUEUES.get(guild_id)

        # Per your requirement: only works if there is currently stuff in the queue
        if not q or len(q) == 0:
            await interaction.followup.send("The queue is empty. Use /play to start the queue, then /playnext to insert the next track.", ephemeral=True)
            return

        # Build query like /play
        query = song_query if song_query.startswith(("http://", "https://")) else f"ytsearch1:{song_query}"

        try:
            results = await _search_ytdlp(query)
        except Exception:
            await interaction.followup.send("Search failed for that query.", ephemeral=True)
            return

        if "entries" in results:
            entries = results.get("entries") or []
            if not entries:
                await interaction.followup.send("No results found.", ephemeral=True)
                return
            first = entries[0]
        else:
            first = results

        audio_url = first["url"]
        title = first.get("title", "Untitled")

        # Insert at the front so it becomes the very next song
        q.appendleft((audio_url, title))

        await interaction.followup.send(f"Will play next: **{title}**")


    @bot.tree.command(
        name="playlist",
        description="Queue a Spotify playlist URL or a saved .txt file of queries.",
        guilds=guilds
    )
    @app_commands.describe(
        source="Spotify playlist URL or a saved text filename in the playlists folder",
        shuffle="Whether to randomize track order"
    )
    async def playlist(interaction: discord.Interaction, source: str, shuffle: bool = False):
        await interaction.response.defer()

        # Voice checks
        vc_state = getattr(interaction.user, "voice", None)
        if not vc_state or not vc_state.channel:
            return await interaction.followup.send("You must be in a voice channel.")
        voice_channel = vc_state.channel

        voice_client = interaction.guild.voice_client or await voice_channel.connect()
        if voice_client.channel.id != voice_channel.id:
            await voice_client.move_to(voice_channel)

        guild_id = str(interaction.guild_id)
        if guild_id not in SONG_QUEUES:
            SONG_QUEUES[guild_id] = deque()

        # Mode A: Spotify playlist URL
        if _is_spotify_playlist(source):
            try:
                raw_id = source.rstrip("/").split("/")[-1]
                playlist_id = raw_id.split("?")[0]
            except Exception:
                return await interaction.followup.send("Couldn't parse a playlist ID from that URL.", ephemeral=True)

            items: list[tuple[str, str]] = []
            # fetch first page
            results = spotify.playlist_items(
                playlist_id,
                fields="items.track(name,artists(name)),next",
                additional_types=["track"]
            )
            def _collect(page):
                for it in page.get("items", []):
                    t = it.get("track")
                    if t and t.get("name"):
                        artists = ", ".join(a["name"] for a in t.get("artists", []))
                        items.append((t["name"], artists))

            _collect(results)
            # optional: follow pagination if you want full lists in the future
            # next_url = results.get("next")
            # while next_url:
            #     page = spotify.next(results)
            #     results = page
            #     _collect(results)
            if not items:
                return await interaction.followup.send("No tracks found in that playlist.", ephemeral=True)

            if shuffle:
                random.shuffle(items)

            # Enqueue first immediately
            first_name, first_artists = items.pop(0)
            first_query = f"ytsearch1:{first_name} {first_artists}"
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

            async def enqueue_rest_spotify():
                count = 1
                for name, artists in items:
                    q = f"ytsearch1:{name} {artists}"
                    try:
                        info = await _search_ytdlp(q)
                        entry = info["entries"][0] if "entries" in info else info
                    except Exception:
                        continue
                    SONG_QUEUES[guild_id].append((entry["url"], entry.get("title", name)))
                    count += 1
                await interaction.channel.send(f"Queued **{count}** tracks.")
            asyncio.create_task(enqueue_rest_spotify())
            return

        # Mode B: text filename playlist
        try:
            lines = _load_text_playlist(source)
        except FileNotFoundError:
            return await interaction.followup.send("Text playlist not found.", ephemeral=True)
        except ValueError:
            return await interaction.followup.send("Invalid filename.", ephemeral=True)
        except Exception:
            return await interaction.followup.send("Failed to read that text playlist.", ephemeral=True)

        if not lines:
            return await interaction.followup.send("That text playlist is empty.", ephemeral=True)

        if shuffle:
            random.shuffle(lines)

        # Enqueue first line immediately
        first_line = lines.pop(0)
        if first_line.startswith("http://") or first_line.startswith("https://"):
            first_query = first_line
        else:
            first_query = f"ytsearch1:{first_line}"
        try:
            first_info = await _search_ytdlp(first_query)
            first_entry = first_info["entries"][0] if "entries" in first_info else first_info
        except Exception:
            return await interaction.followup.send("Failed to enqueue the first line from the file.", ephemeral=True)

        SONG_QUEUES[guild_id].append((first_entry["url"], first_entry.get("title", first_line)))

        if not (voice_client.is_playing() or voice_client.is_paused()):
            await interaction.followup.send(f"Now playing: **{first_entry.get('title', first_line)}**")
            await play_next_song(voice_client, guild_id, interaction.channel, post_now_playing=False)
        else:
            await interaction.followup.send(f"Added **{first_entry.get('title', first_line)}** to the queue.")

        async def enqueue_rest_file():
            count = 1
            for line in lines:
                q = line if (line.startswith("http://") or line.startswith("https://")) else f"ytsearch1:{line}"
                try:
                    info = await _search_ytdlp(q)
                    entry = info["entries"][0] if "entries" in info else info
                except Exception:
                    continue
                SONG_QUEUES[guild_id].append((entry["url"], entry.get("title", line)))
                count += 1
            await interaction.channel.send(f"Queued **{count}** tracks.")
        asyncio.create_task(enqueue_rest_file())


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



