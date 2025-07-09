from __future__ import annotations
import discord, config
from discord import app_commands
from discord.ext import commands

def setup(bot: commands.Bot | discord.Bot) -> None:
    @bot.tree.command(name="purge", description="Deletes a number of messages from channel the command is used in.", guilds=[discord.Object(id=config.GUILD_ID)])
    @app_commands.describe(num_messages="Number of messages to delete (max 1000)")
    async def purge(interaction: discord.Interaction, num_messages: int):

        if not interaction.user.guild_permissions.manage_messages:
            return await interaction.response.send_message("You need the **Manage Messages** permission to use this.", ephemeral=True)

        count = max(1, min(num_messages, 1000))
        await interaction.response.defer(ephemeral=True)

        deleted = await interaction.channel.purge(limit=count)
        await interaction.followup.send(
            f"🧹 Deleted {max(0, len(deleted))} message(s).", ephemeral=True
        )
        