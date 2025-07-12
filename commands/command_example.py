from __future__ import annotations
import discord, config
from discord import app_commands
from discord.ext import commands

# Only works in Team Rocket 2.0

def setup(bot: commands.Bot | discord.Bot) -> None:
    @bot.tree.command(name="ucha", description="Gathers the Uchas", guilds=[discord.Object(id=config.GUILD_ID)])
    async def ucha(interaction: discord.Interaction):
        
        await interaction.response.send_message("<:Ucha:1322004947382046780><:AntiUcha:1379669284724277268><:ChiefUcha:1379648767636017254><:FarmerUcha:1393712061489610863>")
