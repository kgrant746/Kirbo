import asyncio
import json
import importlib.util
import sys
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from aiohttp.test_utils import TestClient, TestServer

from music_service import MusicController, Store, track, validate_query
from web.app import Website, digest


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        (self.root / 'mix.txt').write_text('First song\n# comment\nSecond song\n', encoding='utf-8')
        self.store = Store(self.root / 'db.sqlite', self.root)

    def tearDown(self):
        self.store.db.close()
        self.temp.cleanup()

    def test_import_preserves_files_and_is_only_done_once(self):
        self.assertEqual(len(self.store.playlists()), 1)
        saved = self.store.playlist('mix.txt')
        self.assertEqual(len(saved['tracks']), 2)
        self.assertTrue((self.root / 'mix.txt').exists())
        self.store.db.close()
        self.store = Store(self.root / 'db.sqlite', self.root)
        self.assertEqual(len(self.store.playlists()), 1)

    def test_stale_playlist_cannot_overwrite_another_edit(self):
        p = self.store.playlist('mix')
        self.store.save_playlist(p['id'], 'New name', p['tracks'], p['revision'])
        with self.assertRaises(ValueError):
            self.store.save_playlist(p['id'], 'Old browser', [], p['revision'])
        self.assertEqual(self.store.playlist(p['id'])['name'], 'New name')

    def test_media_urls_cannot_target_internal_or_arbitrary_hosts(self):
        for url in ['https://127.0.0.1/', 'http://youtube.com/watch?v=1', 'https://youtube.com.evil.test/x', 'file:///etc/passwd']:
            with self.subTest(url=url), self.assertRaises(ValueError):
                validate_query(url)
        self.assertEqual(validate_query('hello world'), 'hello world')


class ControllerCase(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.temp.name) / 'db.sqlite', Path(self.temp.name))
        self.vc = SimpleNamespace(channel=SimpleNamespace(id=4, name='Music'),
                                  is_connected=lambda: True, is_playing=lambda: True,
                                  is_paused=lambda: False, stop=lambda: None)
        self.guild = SimpleNamespace(voice_client=self.vc)
        config = SimpleNamespace(GUILD_ID=42, SPOTIFY_CLIENT_ID='test', SPOTIFY_CLIENT_SECRET='test', FFMPEG_PATH='ffmpeg')
        self.music = MusicController(SimpleNamespace(get_guild=lambda _: self.guild), config, self.store)

    async def asyncTearDown(self):
        self.store.db.close()
        self.temp.cleanup()


class ControllerTests(ControllerCase):
    async def test_reorder_conflict_does_not_drop_simultaneously_added_song(self):
        a, b, c = track('a'), track('b'), track('c')
        self.music.queue = [a, b]
        revision = self.music.revision
        self.music.queue.append(c)
        self.music.changed('friend', 'added c')
        with self.assertRaises(ValueError):
            self.music.reorder([b['id'], a['id']], revision, 'old browser')
        self.assertEqual(self.music.queue, [a, b, c])

    async def test_discord_and_browser_share_events_and_queue(self):
        self.music.resolve = AsyncMock(return_value=track('song', 'Current song'))
        self.music.kick = lambda: None
        event = asyncio.Event()
        self.music.listeners.add(event)
        member = SimpleNamespace(voice=SimpleNamespace(channel=self.vc.channel))
        await self.music.add('song', member, 'Discord friend')
        self.assertTrue(event.is_set())
        snap = self.music.snapshot()
        self.assertEqual(snap['queue'][0]['title'], 'Current song')
        self.assertEqual(snap['logs'][0]['actor'], 'Discord friend')
        self.music.remove(snap['queue'][0]['id'], 'Website friend')
        self.assertFalse(self.music.queue)

    async def test_stopped_or_disconnected_player_does_not_restart_worker(self):
        self.music.queue = [track('a')]
        self.vc.is_connected = lambda: False
        self.music.kick()
        self.assertIsNone(self.music.worker)

    async def test_active_track_comes_from_player_not_next_queue_entry(self):
        self.music.current = track('current', 'Current song')
        self.music.queue = [track('next', 'Next song')]
        self.assertEqual(self.music.snapshot()['current']['title'], 'Current song')

    async def test_downloaded_file_is_played_and_removed_at_track_end(self):
        paths = []
        started = asyncio.Event()
        callback = None
        class Downloader:
            def __init__(self, options):
                self.filename = options['outtmpl'].replace('%(id)s', 'test').replace('%(ext)s', 'webm')
            def __enter__(self): return self
            def __exit__(self, *_): pass
            def extract_info(self, url, download):
                Path(self.filename).write_bytes(b'audio')
                paths.append(self.filename)
                return {}
            def prepare_filename(self, info): return self.filename
        def play(source, after):
            nonlocal callback
            callback = after
            started.set()
        self.vc.play = play
        self.music.queue = [track('test', 'Test track')]
        with patch('music_service.yt_dlp.YoutubeDL', Downloader), patch('music_service.discord.FFmpegOpusAudio', return_value=SimpleNamespace(cleanup=lambda:None)) as ffmpeg:
            self.music.kick()
            worker = self.music.worker
            await asyncio.wait_for(started.wait(), 3)
            self.assertEqual(self.music.current['title'], 'Test track')
            self.assertTrue(Path(paths[0]).exists())
            self.assertEqual(ffmpeg.call_args.args[0], paths[0])
            callback(None)
            await asyncio.wait_for(worker, 3)
        self.assertFalse(Path(paths[0]).exists())
        self.assertIsNone(self.music.current)

    async def test_all_music_slash_commands_register_on_shared_controller(self):
        import discord
        from discord.ext import commands
        with patch.dict(sys.modules, {'config': self.music.config}):
            spec = importlib.util.spec_from_file_location('music_commands_test', Path(__file__).resolve().parents[1] / 'music.py')
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
        module.MusicController = lambda *args: self.music
        bot = commands.Bot(command_prefix='!', intents=discord.Intents.default())
        self.assertIs(module.setup_music(bot), self.music)
        names = {command.name for command in bot.tree.get_commands(guild=discord.Object(id=42))}
        self.assertEqual(names, {'play','playnext','pause','resume','stop','skip','queue','nowplaying','playlist'})
        await bot.close()


class WebSecurityTests(ControllerCase):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.site = Website(self.music)
        async def verified(session):
            session['checked'] = time.time()
            session['user'] = {'id': '12345', 'username': 'friend', 'avatar': None}
        self.site.membership = AsyncMock(side_effect=verified)
        self.client = TestClient(TestServer(self.site.app))
        await self.client.start_server()
        session = dict(user={'id':'12345', 'username':'friend', 'avatar':None}, csrf='csrf-test', checked=time.time(), token={})
        self.store.db.execute('INSERT INTO sessions VALUES(?,?,?)', (digest('test-cookie'), json.dumps(session), time.time()+300))
        self.store.db.commit()
        self.headers = {'Cookie': 'kirbo_session=test-cookie', 'Origin':self.site.origin, 'X-CSRF-Token':'csrf-test'}

    async def asyncTearDown(self):
        await self.client.close()
        await super().asyncTearDown()

    async def test_all_private_endpoints_require_login(self):
        for path in ['/api/me','/api/state','/api/events']:
            response = await self.client.get(path)
            self.assertEqual(response.status, 401)
        response = await self.client.post('/api/action', json={'action':'clear'}, headers={'Origin':self.site.origin})
        self.assertEqual(response.status,401)
        self.assertEqual((await self.client.get('/.env')).status,404)

    async def test_cross_origin_and_missing_csrf_cannot_change_queue(self):
        self.music.queue = [track('protected')]
        for headers in [dict(self.headers,Origin='https://attacker.test'), {'Cookie':self.headers['Cookie'],'Origin':self.site.origin}]:
            response = await self.client.post('/api/action',json={'action':'clear'},headers=headers)
            self.assertEqual(response.status,403)
        self.assertEqual(len(self.music.queue),1)

    async def test_cookie_does_not_allow_removed_member_to_mutate(self):
        from aiohttp import web
        self.site.membership.side_effect = web.HTTPForbidden(reason='Not a member')
        response = await self.client.post('/api/action',json={'action':'clear'},headers=self.headers)
        self.assertEqual(response.status,403)
        self.assertIsNone(self.store.db.execute('SELECT id FROM sessions WHERE id=?',(digest('test-cookie'),)).fetchone())

    async def test_member_can_edit_and_receive_live_changes(self):
        response = await self.client.post('/api/action',json={'action':'save_playlist','name':'Favorites','tracks':[]},headers=self.headers)
        self.assertEqual(response.status,200)
        self.assertEqual((await response.json())['playlists'][0]['name'],'Favorites')
        events = await self.client.get('/api/events',headers=self.headers)
        other = await self.client.get('/api/events',headers=self.headers)
        line = await asyncio.wait_for(events.content.readline(),2)
        self.assertTrue(line.startswith(b'data: '))
        await events.content.readline()
        await other.content.readline()
        await other.content.readline()
        self.music.changed('other friend','added a song')
        line = await asyncio.wait_for(events.content.readline(),2)
        self.assertIn(b'other friend',line)
        self.assertIn(b'other friend', await asyncio.wait_for(other.content.readline(), 2))
        events.close()
        other.close()

    async def test_slow_song_search_does_not_block_quick_queue_removal(self):
        row = track('waiting song')
        self.music.queue = [row]
        self.site.inflight.add('12345')
        response = await self.client.post('/api/action', json={'action':'remove', 'id':row['id']}, headers=self.headers)
        self.assertEqual(response.status, 200)
        self.assertFalse(self.music.queue)

    async def test_oauth_callback_rejects_forged_state(self):
        response = await self.client.get('/auth/callback?code=fake&state=fake')
        self.assertEqual(response.status,403)

    async def test_oauth_login_binds_state_to_browser_and_creates_durable_session(self):
        from urllib.parse import urlparse, parse_qs
        self.site.client_id, self.site.secret = 'test-id', 'test-secret'
        login = await self.client.get('/auth/login', allow_redirects=False)
        params = parse_qs(urlparse(login.headers['Location']).query)
        state = params['state'][0]
        self.assertEqual(params['scope'], ['identify guilds.members.read'])
        self.site.token = AsyncMock(return_value={'access_token':'access','refresh_token':'refresh','expires_at':time.time()+3600})
        response = await self.client.get('/auth/callback?code=test&state='+state,
                                         headers={'Cookie':'kirbo_oauth='+state}, allow_redirects=False)
        self.assertEqual(response.status,302)
        self.assertIn('kirbo_session',response.cookies)
        self.assertTrue(response.cookies['kirbo_session']['httponly'])
        cookie = response.cookies['kirbo_session'].value
        saved = self.store.db.execute('SELECT payload FROM sessions WHERE id=?',(digest(cookie),)).fetchone()
        self.assertEqual(json.loads(saved[0])['token']['refresh_token'],'refresh')
        self.assertNotIn('refresh',response.headers.get('Location',''))

    async def test_remembered_cookie_has_security_flags(self):
        response = await self.client.get('/api/me',headers=self.headers)
        self.assertEqual(response.status,200)
        cookie = response.headers['Set-Cookie']
        for flag in ['HttpOnly','Secure','SameSite=Lax']:
            self.assertIn(flag,cookie)


if __name__ == '__main__':
    unittest.main()
