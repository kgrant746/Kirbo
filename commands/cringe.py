from __future__ import annotations
import discord, config
from discord import app_commands
from discord.ext import commands
import asyncio


def setup(bot: commands.Bot | discord.Bot) -> None:
    @bot.tree.command(name="cringe", description="Prevents cringe", guilds=[discord.Object(id=config.GUILD_ID)])
    @app_commands.describe(member="Target user", seconds="Duration of cringe prevention in seconds (default 10)")
    @app_commands.checks.has_permissions(administrator=True)
    async def cringe(interaction: discord.Interaction, member: discord.Member, seconds: int=10):
        await interaction.response.defer(ephemeral=False)

        if not member.voice or not member.voice.channel:
            return await interaction.followup.send("That user is not in a voice channel.")

        end_time = asyncio.get_event_loop().time() + seconds    # Can just replace seconds with and int to make the duration fixed
                                                                # (for when dowski sets the duration to like, 10000 seconds)
        await member.edit(mute=True, reason="No more cringe")

        await interaction.followup.send(f"Preventing {member.display_name} from spouting more cringe...")

        while asyncio.get_event_loop().time() < end_time:
            vs = member.voice
            if vs and not vs.mute:
                await member.edit(mute=True, reason="Surpressing cringe.")
            await asyncio.sleep(0.1)

        await member.edit(mute=False, reason="Cringe legalized")

        await interaction.followup.send(f"{member.display_name} is now free to spread their cringe once more.")

        

        











