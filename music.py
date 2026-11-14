"""Discord commands backed by the same controller as the website."""
import random
import discord
from discord import app_commands
import config
import private
from music_service import MusicController


def setup_music(bot):
    music = MusicController(bot, config)
    bot.music = music
    guilds = [discord.Object(id=config.GUILD_ID)]

    async def allowed(interaction):
        if interaction.user.id in private.BLACKLISTED_USERS:
            await interaction.response.send_message(private.BLACKLISTED_MESSAGE, ephemeral=True)
            return False
        return True

    async def add_song(interaction, query, next_up=False):
        if not await allowed(interaction):
            return
        await interaction.response.defer()
        try:
            row = await music.add(query, interaction.user, interaction.user.name, next_up)
            await interaction.edit_original_response(content=f"{'Will play next' if next_up else 'Added to queue'}: **{row['title']}**")
        except Exception as error:
            await interaction.edit_original_response(content=str(error)[:1800])

    @bot.tree.command(name='play', description='Play a song or add it to the queue.', guilds=guilds)
    @app_commands.describe(song_query='Song name, YouTube link, or Spotify track link')
    async def play(interaction: discord.Interaction, song_query: str):
        await add_song(interaction, song_query)

    @bot.tree.command(name='playnext', description='Insert a song at the front of the waiting queue.', guilds=guilds)
    @app_commands.describe(song_query='Song name, YouTube link, or Spotify track link')
    async def playnext(interaction: discord.Interaction, song_query: str):
        await add_song(interaction, song_query, True)

    def register_control(action, description):
        @bot.tree.command(name=action, description=description, guilds=guilds)
        async def command(interaction: discord.Interaction):
            if not await allowed(interaction):
                return
            await interaction.response.defer()
            try:
                await music.control(action, interaction.user.name, interaction.user)
                await interaction.edit_original_response(content={'pause': 'Playback paused.', 'resume': 'Playback resumed.', 'skip': 'Skipped the current song.', 'stop': 'Stopped playback and disconnected.'}[action])
            except Exception as error:
                await interaction.edit_original_response(content=str(error)[:1800])
    for action, description in [('pause', 'Pause playback.'), ('resume', 'Resume playback.'), ('skip', 'Skip the current song.'), ('stop', 'Stop playback and clear the queue.')]:
        register_control(action, description)

    @bot.tree.command(name='queue', description='List songs waiting to play.', guilds=guilds)
    async def queue(interaction: discord.Interaction):
        rows = [f"{i + 1}. {row['title']}" for i, row in enumerate(music.queue)]
        content = '\n'.join(rows) or 'The song queue is currently empty.'
        if len(content) > 1900:
            content = content[:1800] + '\n… Open the Kirbo website to view the full queue.'
        await interaction.response.send_message(content)

    @bot.tree.command(name='nowplaying', description='Show the track currently playing.', guilds=guilds)
    async def nowplaying(interaction: discord.Interaction):
        row = music.current
        content = f"{'Loading' if music.loading else 'Currently playing'}: **{row['title']}**" if row else 'No song is currently playing.'
        await interaction.response.send_message(content)

    @bot.tree.command(name='playlist', description='Queue a Spotify playlist URL or a saved playlist name.', guilds=guilds)
    async def playlist(interaction: discord.Interaction, source: str, shuffle: bool = False):
        if not await allowed(interaction):
            return
        await interaction.response.defer()
        try:
            if source.startswith('https://open.spotify.com/'):
                rows = await music.spotify_rows(source)
            else:
                saved = music.store.playlist(source)
                if not saved:
                    raise ValueError('Playlist not found. Use the website to create or import it.')
                rows = saved['tracks']
            if not rows:
                raise ValueError('That playlist is empty.')
            await music.enqueue_playlist(rows, interaction.user, interaction.user.name, shuffle)
            music.changed(interaction.user.name, f'queued playlist {source}')
            await interaction.edit_original_response(content=f'Added **{len(rows)}** playlist songs to the queue.')
        except Exception as error:
            await interaction.edit_original_response(content=str(error)[:1800])
    return music
