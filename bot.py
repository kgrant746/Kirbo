import discord, config, music, task_manager
from discord.ext import commands, tasks
import command_handler
from task_manager import holiday_check, run_holiday_check, nightly_update, set_channel_name


intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)


command_handler.setup_all(bot)
music.setup_music(bot)


@bot.event
async def on_ready():
    guild = discord.Object(id=config.GUILD_ID)
    synced = await bot.tree.sync(guild=guild)
    print(f"{bot.user} online - synced {len(synced)} command(s) to guild {guild.id!r}")

    task_manager.bot = bot
    # CoD Countdown Channel Name Updater
    if not nightly_update.is_running():
        await set_channel_name()
        nightly_update.start()

    # Holiday Profile Picture Changer
    if not holiday_check.is_running():
        print("Running holiday check...")   # Test
        await run_holiday_check()
        print("Finished running holiday check task")    # Test
        holiday_check.start()


bot.run(config.TOKEN)
