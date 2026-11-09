import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import discord
from discord_profile import DiscordProfile, WEBSITE_URL


class ProfileTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.app = SimpleNamespace(description='POYO POYO', edit=AsyncMock())
        self.bot = SimpleNamespace(application_info=AsyncMock(return_value=self.app),
                                   wait_until_ready=AsyncMock(), change_presence=AsyncMock())
        self.music = SimpleNamespace(current=None, loading=False, paused=False, listeners=set())
        self.profile = DiscordProfile(self.bot, self.music)

    async def asyncTearDown(self):
        await self.profile.close()

    async def wait_for_updates(self, count):
        async def wait():
            while self.bot.change_presence.await_count < count:
                await asyncio.sleep(0.001)
        await asyncio.wait_for(wait(), 2)

    def notify(self):
        for event in self.music.listeners:
            event.set()

    async def test_description_preserves_existing_text_and_adds_link_only_once(self):
        await self.profile.update_description()
        self.app.edit.assert_awaited_once_with(description='POYO POYO\n\nMusic controller: '+WEBSITE_URL)
        await self.profile.update_description()
        self.bot.application_info.assert_awaited_once()

    async def test_existing_link_needs_no_edit_and_failure_does_not_break_startup(self):
        self.app.description = 'Control me at '+WEBSITE_URL
        await self.profile.update_description()
        self.app.edit.assert_not_awaited()
        self.profile.description_synced = False
        self.bot.application_info.side_effect = OSError('Discord unavailable')
        with self.assertLogs('discord_profile', level='WARNING'):
            await self.profile.update_description()
        self.assertFalse(self.profile.description_synced)

    async def test_presence_tracks_actual_playback_pause_resume_and_completion(self):
        with patch('discord_profile.PRESENCE_INTERVAL', 0.005):
            self.profile.start()
            await self.wait_for_updates(1)
            self.assertIsNone(self.bot.change_presence.call_args.kwargs['activity'])
            self.music.current = {'title':'Current song', 'artist':'Artist', 'query':'song'}
            self.notify()
            await self.wait_for_updates(2)
            activity = self.bot.change_presence.call_args.kwargs['activity']
            self.assertEqual(activity.type, discord.ActivityType.listening)
            self.assertEqual((activity.name,activity.state), ('Current song','Artist'))
            self.music.paused = True
            self.notify()
            await self.wait_for_updates(3)
            self.assertIsNone(self.bot.change_presence.call_args.kwargs['activity'])
            self.music.paused = False
            self.notify()
            await self.wait_for_updates(4)
            self.assertEqual(self.bot.change_presence.call_args.kwargs['activity'].name, 'Current song')
            self.music.current = None
            self.notify()
            await self.wait_for_updates(5)
            self.assertIsNone(self.bot.change_presence.call_args.kwargs['activity'])

    async def test_fast_changes_are_coalesced_and_unrelated_events_do_not_spam_presence(self):
        with patch('discord_profile.PRESENCE_INTERVAL', 0.03):
            self.profile.start()
            await self.wait_for_updates(1)
            for i in range(20):
                self.music.current = {'title':f'Song {i}', 'query':'song'}
                self.notify()
            await self.wait_for_updates(2)
            self.assertEqual(self.bot.change_presence.call_args.kwargs['activity'].name, 'Song 19')
            for _ in range(20):
                self.notify()
            await asyncio.sleep(0.05)
            self.assertEqual(self.bot.change_presence.await_count, 2)
            await self.profile.close()
            self.assertFalse(self.music.listeners)

    async def test_loading_is_not_advertised_as_playing(self):
        self.music.current = {'title':'Not ready', 'query':'song'}
        self.music.loading = True
        self.assertIsNone(self.profile.activity_key())

    async def test_presence_retries_temporary_errors_and_resends_after_reconnect(self):
        self.bot.change_presence.side_effect = [OSError('disconnected'), None, None]
        with patch('discord_profile.PRESENCE_INTERVAL', 0.005):
            with self.assertLogs('discord_profile', level='WARNING'):
                self.profile.start()
                await self.wait_for_updates(2)
            self.profile.start()
            await self.wait_for_updates(3)
        self.assertFalse(self.profile.force)
