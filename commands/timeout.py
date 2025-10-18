from __future__ import annotations
import discord, config
from discord import app_commands
from discord.ext import commands
import asyncio


def setup(bot: commands.Bot | discord.Bot) -> None:
    @bot.tree.command(name="timeout", description="Puts a user in the corner", guilds=[discord.Object(id=config.GUILD_ID)])
    @app_commands.describe(member="Target user", seconds="Duration of timeout in seconds (default 10)")
    @app_commands.checks.has_permissions(administrator=True)
    async def timeout(interaction: discord.Interaction, member: discord.Member, seconds: int=10):
        await interaction.response.defer(ephemeral=False)

        if not member.voice or not member.voice.channel:
            return await interaction.followup.send("That user is not in a voice channel.")

        end_time = asyncio.get_event_loop().time() + seconds

        await member.edit(mute=True, deafen=True, reason="Hi dowski :)")

        await interaction.followup.send(f"{member.display_name.upper()}, GO SIT IN THE CORNER AND THINK ABOUT WHAT YOU'VE DONE")

        while asyncio.get_event_loop().time() < end_time:
            vs = member.voice
            if vs and (not vs.mute or not vs.deaf):
                await member.edit(mute=True, deafen=True, reason="NO, YOU CAN'T COME OUT OF TIMEOUT YET")
            await asyncio.sleep(0.1)

        await member.edit(mute=False, deafen=False, reason="Freedom.")

        await interaction.followup.send(f"{member.display_name} can come out of the corner now")