"""Discord profile link and the music player's public listening activity."""
import asyncio
import logging

import discord

WEBSITE_URL = 'https://kirbo.bot'
PRESENCE_INTERVAL = 5
log = logging.getLogger(__name__)


class DiscordProfile:
    def __init__(self, bot, music):
        self.bot, self.music = bot, music
        self.event = asyncio.Event()
        self.task = None
        self.description_synced = False
        self.force = True
        self.last_activity = None
        self.sent_at = float('-inf')

    async def update_description(self):
        if self.description_synced:
            return
        try:
            app = await self.bot.application_info()
            description = app.description or ''
            if WEBSITE_URL not in description.lower():
                description = description.rstrip() + '\n\nMusic controller: ' + WEBSITE_URL
                await app.edit(description=description.lstrip())
            self.description_synced = True
        except Exception:
            log.warning('Could not add the website to the bot description. Add https://kirbo.bot in Discord Developer Portal > General Information > Description.')

    def start(self):
        self.force = True
        self.music.listeners.add(self.event)
        if self.task is None or self.task.done():
            self.task = asyncio.create_task(self.run())
        self.event.set()

    def activity_key(self):
        row = self.music.current
        if not row or self.music.loading or self.music.paused:
            return None
        return ((str(row.get('title') or '').strip() or str(row['query']))[:128],
                str(row.get('artist') or '').strip()[:128])

    async def run(self):
        while True:
            await self.event.wait()
            await self.bot.wait_until_ready()
            delay = PRESENCE_INTERVAL - (asyncio.get_running_loop().time() - self.sent_at)
            if delay > 0:
                await asyncio.sleep(delay)
            self.event.clear()
            key = self.activity_key()
            if key == self.last_activity and not self.force:
                continue
            activity = discord.Activity(type=discord.ActivityType.listening, name=key[0], state=key[1] or None) if key else None
            try:
                await self.bot.change_presence(activity=activity)
            except Exception as error:
                log.warning('Could not update Discord music status (%s); will retry', type(error).__name__)
                self.event.set()
            else:
                self.last_activity = key
                self.force = False
            finally:
                self.sent_at = asyncio.get_running_loop().time()

    async def close(self):
        self.music.listeners.discard(self.event)
        if self.task:
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)
