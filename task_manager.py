from discord.ext import commands, tasks
from datetime import datetime, date, time
import config

bot: commands.Bot | None = None
LOCAL_TZ = datetime.now().astimezone().tzinfo


#------------------ Holiday Profile Picture Changer ------------------#

TRIGGER_DATES = [
    (10, 1), 
    (11, 1), 
    (1, 1)
]

CHRISTMAS_PIC = "C:\\Users\\Recor\\Kirbo\\bin\\profile-pictures\\kirbo-christmas.png"
HALLOWEEN_PIC = "C:\\Users\\Recor\\Kirbo\\bin\\profile-pictures\\kirbo-halloween.png"

@tasks.loop(time=time(0, 0, tzinfo=LOCAL_TZ))
async def holiday_check():
    await run_holiday_check()

async def run_holiday_check():
    today = datetime.now().date()

    print(f"Today is {today}")
    if (today.month, today.day) in TRIGGER_DATES:
        channel = await bot.fetch_channel(config.GENERAL_CHAT_CHANNEL_ID) # currently BOT TEST channel
        print("Trigger date matched!")

        if today.month == 11:
            print("It's November, setting Christmas pic")
            await holiday_time(CHRISTMAS_PIC)
            await channel.send("JSCHLATT TIME!")
        elif today.month == 10:
            print("It's October, setting Halloween pic")
            await holiday_time(HALLOWEEN_PIC)
            await channel.send("SPOOKY TIME!")

async def holiday_time(image_path: str):
    with open(image_path, "rb") as img:
        await bot.user.edit(avatar=img.read())


#------------------ COD Countdown Channel Name Updater ------------------#
CHANNEL_ID = config.COD_VOICE_CHANNEL_ID  # voice channel ID
TARGET_DATE = date(2025, 11, 14)  # set the CoD release date (YYYY, M, D)

def make_name(days: int) -> str:
    if days <= 0:
        return "COD IS HERE!!!"
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
