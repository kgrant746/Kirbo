import discord, config, music
from discord.ext import commands


intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

music.setup_music(bot)

@bot.event
async def on_ready():
    guild = discord.Object(id=config.GUILD_ID)
    synced = await bot.tree.sync(guild=guild)
    print(f"{bot.user} online — synced {len(synced)} command(s) to guild {guild.id!r}")

bot.run(config.TOKEN)
