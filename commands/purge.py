from __future__ import annotations
import discord, config
from discord import app_commands
from discord.ext import commands

def setup(bot: commands.Bot) -> None:
    purge = app_commands.Group(name="purge", description="Bulk‐delete messages", guild_ids=[config.GUILD_ID])

    async def _do_purge(interaction, limit, check=None):
        if not interaction.user.guild_permissions.manage_messages:
            return await interaction.response.send_message(
                "You do not have permission to use this command.", ephemeral=True
            )

        n = max(1, min(limit, 100))

        await interaction.response.defer(ephemeral=True)

        if check is None:
            deleted = await interaction.channel.purge(limit=n)
        else:
            deleted = await interaction.channel.purge(limit=n, check=check)

        await interaction.followup.send(
            f"🧹 Deleted {len(deleted)} message(s).", ephemeral=True
        )

    @purge.command(name="any", description="Delete the most recent messages regardless of author")
    @app_commands.describe(count="How many messages to delete (1–100)")
    async def purge_any(interaction: discord.Interaction, count: int):
        await _do_purge(interaction, count)

    @purge.command(name="bots", description="Delete only messages sent by bots")
    @app_commands.describe(count="How many bot‐messages to delete (1–100)")
    async def purge_bots(interaction: discord.Interaction, count: int):
        await _do_purge(interaction, count, check=lambda m: m.author.bot)

    @purge.command(name="humans", description="Delete only messages sent by humans")
    @app_commands.describe(count="How many human‐messages to delete (1–100)")
    async def purge_humans(interaction: discord.Interaction, count: int):
        await _do_purge(interaction, count, check=lambda m: not m.author.bot)

    @purge.command(name="images", description="Delete messages that contain an image")
    @app_commands.describe(count="How many image‐messages to delete (1–100)")
    async def purge_images(interaction: discord.Interaction, count: int):
        await _do_purge(interaction, count, check=lambda m: m.attachments)

    @purge.command(name="embeds", description="Delete messages that contain an embed")
    @app_commands.describe(count="How many image‐messages to delete (1–100)")
    async def purge_images(interaction: discord.Interaction, count: int):
        await _do_purge(interaction, count, check=lambda m: m.embeds)
    
    @purge.command(name="contains", description="Delete messages containing a given substring",)
    @app_commands.describe(count="How many recent messages to scan (1–100)", substring="Text to match (case-insensitive)",)
    async def purge_contains(interaction: discord.Interaction, count: int, substring: str,):
        await _do_purge(interaction, count, check=lambda m: substring.lower() in m.content.lower())


    bot.tree.add_command(purge)
