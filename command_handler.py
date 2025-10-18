from __future__ import annotations
import discord, config
from discord import app_commands
from discord.ext import commands

import commands


def setup_all(bot: commands.Bot | discord.Bot) -> None:
    commands.command_example.setup(bot)
    commands.purge.setup(bot)
    commands.cringe.setup(bot)
    commands.timeout.setup(bot)
    commands.help.setup(bot)
    