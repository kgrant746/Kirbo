from __future__ import annotations
import discord, config
from discord import app_commands
from discord.ext import commands


def setup(bot: commands.Bot | discord.Bot) -> None:
    @bot.tree.command(name="help", description="Shows a list of all commands and what they do.", guilds=[discord.Object(id=config.GUILD_ID)])
    async def help(interaction: discord.Interaction):
        embed = discord.Embed(
            title="📘 Kirbo Command Guide",
            description="YURRRRRRRRRRRR",
            color=discord.Color.blurple()
        )

        embed.add_field(
            name="**🎵 /play [song_query]**",
            value=(
                "**Description:**\n"
                " • Plays a song or adds it to the queue.\n"
                "**Parameters:**\n"
                " • `song_query` — The title or YouTube URL of the song to play.\n"
            ),
            inline=False
        )

        embed.add_field(
            name="**🎵 /skip**",
            value=(
                "**Description:**\n" 
                " • Skips the currently playing song.\n"
            ),
            inline=False
        )

        embed.add_field(
            name="**🎵 /pause**",
            value=(
                "**Description:**\n" 
                " • Pauses the current song.\n"
            ),
            inline=False
        )

        embed.add_field(
            name="**🎵 /resume**",
            value=(
                "**Description:**\n" 
                " • Resumes a paused song.\n"
            ),
            inline=False
        )

        embed.add_field(
            name="**🎵 /stop**",
            value=(
                "**Description:**\n" 
                " • Stops playback and clears the queue.\n"
            ),
            inline=False
        )

        embed.add_field(
            name="**🎵 /playlist [playlist_url] [shuffle]**",
            value=(
                "**Description:**\n"
                " • Plays all songs from a public Spotify playlist.\n"
                "**Parameters:**\n"
                " • `playlist_url` — Link to the Spotify playlist.\n"
                " • `shuffle` *(True/False)* — Randomizes the track order."
            ),
            inline=False
        )

        embed.add_field(
            name="**🛠️ /cringe [target_user] [duration]**",
            value=(
                "**Description:**\n"
                " • Prevents cringe.\n"
                "**Parameters:**\n"
                " • `target_user` — User being cringe.\n"
                " • `duration` — Duration in seconds to prevent cringe."
            ),
            inline=False
        )

        embed.add_field(
            name="**🛠️ /timeout [target_user] [duration]**",
            value=(
                "**Description:**\n"
                " • Puts a user in timeout.\n"
                "**Parameters:**\n"
                " • `target_user` — User in need of a timeout.\n"
                " • `duration` — Duration in seconds for user to sit in the corner of shame."
            ),
            inline=False
        )

        embed.add_field(
            name="**🛠️ /purge**",
            value=(
                "**Description:**\n"
                " • Deletes messages. Supports the following subcommands.\n"
                "**Subcommands:**\n"
                " • `/purge any [num_messages]` — Deletes any messages.\n"
                " • `/purge bots [num_messages]` — Deletes only bot messages.\n"
                " • `/purge humans [num_messages]` — Deletes only user messages.\n"
                " • `/purge images [num_messages]` — Deletes messages containing images.\n"
                " • `/purge embeds [num_messages]` — Deletes messages containing embeds.\n"
                " • `/purge contains [num_messages] [phrase]` — Deletes messages containing a specified phrase."
            ),
            inline=False
        )

        embed.set_footer(text="Sucka mah dih")

        await interaction.response.send_message(embed=embed, ephemeral=True)