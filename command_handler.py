from __future__ import annotations
import discord, config
from discord import app_commands
from discord.ext import commands

import commands.purge


def setup_all(bot: commands.Bot | discord.Bot) -> None:
    commands.purge.setup(bot)