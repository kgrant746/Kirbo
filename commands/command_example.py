from __future__ import annotations
import discord, config
from discord import app_commands
from discord.ext import commands

def setup(bot: commands.Bot | discord.Bot) -> None:
    @bot.tree.command(name="ucha", description="Gathers the Uchas", guilds=[discord.Object(id=config.GUILD_ID)])
    async def ucha(interaction: discord.Interaction):
        
        await interaction.response.send_message(":Ucha::AntiUcha::ChiefUcha")
