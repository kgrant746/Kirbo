import discord, config, music, llm
from discord.ext import commands, tasks
import command_handler
from datetime import datetime, date, time


intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

CHANNEL_ID = 1428195301188960286  # voice channel ID
TARGET_DATE = date(2025, 11, 14)  # set the CoD release date (YYYY, M, D)
LOCAL_TZ = datetime.now().astimezone().tzinfo

command_handler.setup_all(bot)
music.setup_music(bot)

#------------------ COD Countdown Channel Name Updater ------------------#
def make_name(days: int) -> str:
    if days <= 0:
        return "CoD is out"
    unit = "DAY" if days == 1 else "DAYS"
    return f"{days} {unit} TILL COD"

async def set_channel_name():
    now = datetime.now().date()  # system local date
    days_left = (TARGET_DATE - now).days
    name = make_name(days_left)
    channel = await bot.fetch_channel(CHANNEL_ID)
    await channel.edit(name=name)

@tasks.loop(time=time(0, 0, tzinfo=LOCAL_TZ))
async def nightly_update():
    await set_channel_name()

@bot.event
async def on_ready():
    guild = discord.Object(id=config.GUILD_ID)
    synced = await bot.tree.sync(guild=guild)
    print(f"{bot.user} online - synced {len(synced)} command(s) to guild {guild.id!r}")

    await set_channel_name()
    if not nightly_update.is_running():
        nightly_update.start()
#-----------------------------------------------------------------------#

bot.run(config.TOKEN)
