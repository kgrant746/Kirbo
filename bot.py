import discord, config, music
from discord.ext import commands

intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(command_prefix="!", intents=intents)

music.setup_music(bot)

@bot.event
async def on_ready():
    print(f"{bot.user} online - version {config.__version__!r}")

bot.run(config.TOKEN)
