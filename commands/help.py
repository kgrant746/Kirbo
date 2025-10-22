from __future__ import annotations
import discord, config
from discord import app_commands
from discord.ext import commands


def setup(bot: commands.Bot | discord.Bot) -> None:
    @bot.tree.command(name="help", description="Shows a list of all commands and what they do.", guilds=[discord.Object(id=config.GUILD_ID)])
    async def help(interaction: discord.Interaction):
        print(f"{interaction.user} used /help")
        
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

        embed.add_field(
            name="**🃏 /blackjack [bet_amount]**",
            value=(
                "**Description:**\n"
                " • Starts a new game of blackjack.\n"
                "**Parameters:**\n"
                " • `bet_amount` — Amount of money you want to bet. There is no max bet.\n"
            ),
            inline=False
        )

        embed.add_field(
            name="**🃏 /hit**",
            value=(
                "**Description:**\n"
                " • Draws another card in your current blackjack hand.\n"
            ),
            inline=False
        )

        embed.add_field(
            name="**🃏 /stand**",
            value=(
                "**Description:**\n"
                " • Ends your turn and lets the dealer play.\n"
            ),
            inline=False
        )

        embed.add_field(
            name="**🃏 /double**",
            value=(
                "**Description:**\n"
                " • Doubles your bet and draws one final card before standing.\n"
            ),
            inline=False
        )

        embed.add_field(
            name="**🃏 /fold**",
            value=(
                "**Description:**\n"
                " • Ends your current hand early and returns half your bet.\n"
            ),
            inline=False
        )

        embed.add_field(
            name="**🃏 /balance**",
            value=(
                "**Description:**\n"
                " • Shows how much money you currently have.\n"
            ),
            inline=False
        )

        embed.add_field(
            name="**🃏 /leaderboard**",
            value=(
                "**Description:**\n"
                " • Displays the richest players on the server.\n"
            ),
            inline=False
        )

        embed.add_field(
            name="**🃏 /charity**",
            value=(
                "**Description:**\n"
                " • Once per day, receive a random amount of money (0–1000) from the charity pool.\n"
            ),
            inline=False
        )

        embed.add_field(
            name="**🃏 /broke**",
            value=(
                "**Description:**\n"
                " • Usable only when at $0. Gives a random amount of money (1-50) so you can keep feeding the gambling addiction.\n"
            ),
            inline=False
        )

        embed.set_footer(text="Sucka mah dih")

        await interaction.response.send_message(embed=embed, ephemeral=True)