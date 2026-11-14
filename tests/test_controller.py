import asyncio
import json
import io
import array
import math
import shlex
import shutil
import subprocess
import importlib.util
import sys
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from aiohttp.test_utils import TestClient, TestServer
from PIL import Image
from spotipy.exceptions import SpotifyException

from music_service import MusicController, PlaybackSource, NORMALIZE_AUDIO, Store, playlist_title, track, validate_query
from web.app import (Website, cover_image, digest, MAX_COVER_BYTES, CREATOR_ID,
                     LOGIN_ATTEMPTS, LOGIN_WINDOW, MAX_PENDING_LOGINS, login_address, start)


class CoverImageTests(unittest.TestCase):
    def test_reencoding_removes_embedded_metadata(self):
        image = Image.new('RGB', (20, 30), '#e89aba')
        exif = Image.Exif()
        exif[270] = 'Private photo metadata'
        source = io.BytesIO()
        image.save(source, format='PNG', exif=exif, icc_profile=b'private profile')
        with Image.open(io.BytesIO(cover_image(source.getvalue()))) as saved:
            self.assertEqual(saved.size, (20, 30))
            self.assertFalse(saved.getexif())
            self.assertNotIn('icc_profile', saved.info)

    def test_oversized_pixel_dimensions_are_rejected_before_decoding(self):
        with patch('web.app.Image.open') as opened:
            opened.return_value.__enter__.return_value = SimpleNamespace(format='PNG', width=6000, height=5000)
            with self.assertRaisesRegex(ValueError, '25 megapixels'):
                cover_image(b'header')


class AudioTests(unittest.TestCase):
    def test_progress_counts_audio_packets_and_cleanup_runs_once(self):
        underlying = Mock()
        underlying.read.side_effect = [b'packet', b'packet', b'']
        underlying.is_opus.return_value = True
        source = PlaybackSource(underlying)
        self.assertEqual(source.read(), b'packet')
        self.assertEqual(source.read(), b'packet')
        self.assertEqual(source.read(), b'')
        self.assertEqual(source.frames * 0.02, 0.04)
        self.assertTrue(source.is_opus())
        source.cleanup()
        source.cleanup()
        underlying.cleanup.assert_called_once()

    @unittest.skipUnless(shutil.which('ffmpeg'), 'FFmpeg is not installed on this computer')
    def test_loud_and_quiet_sources_reach_consistent_volume(self):
        levels = []
        for gain in (0.03, 0.8):
            result = subprocess.run([shutil.which('ffmpeg'), '-hide_banner', '-loglevel', 'error', '-f', 'lavfi',
                '-i', f'sine=frequency=440:duration=5,volume={gain}', *shlex.split(NORMALIZE_AUDIO),
                '-ac', '1', '-c:a', 'pcm_f32le', '-f', 'f32le', 'pipe:1'], capture_output=True, check=True, timeout=20)
            samples = array.array('f', result.stdout)
            rms = math.sqrt(sum(value * value for value in samples) / len(samples))
            self.assertGreater(rms, 0.05)
            self.assertLess(max(abs(value) for value in samples), 1)
            levels.append(rms)
        self.assertLess(abs(20 * math.log10(levels[0] / levels[1])), 1)


class FakeDownloader:
    def __init__(self, options):
        self.filename = options['outtmpl'].replace('%(id)s', 'test').replace('%(ext)s', 'webm')
    def __enter__(self): return self
    def __exit__(self, *_): pass
    def extract_info(self, url, download):
        Path(self.filename).write_bytes(b'audio')
        return {'duration': 120}
    def prepare_filename(self, info): return self.filename


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

    def test_metadata_merges_with_latest_playlist_without_losing_edits(self):
        p = self.store.playlist('mix')
        original = dict(p['tracks'][0])
        p['tracks'][0]['title'] = 'Custom title'
        p = self.store.save_playlist(p['id'], 'Renamed', list(reversed(p['tracks'])), p['revision'])
        self.store.db.execute('UPDATE playlists SET cover=? WHERE id=?', ('custom.png', p['id']))
        self.store.db.commit()
        self.assertTrue(self.store.merge_metadata(p['id'], original, track('resolved', 'Found title', 123, 'https://example.com/cover.jpg')))
        saved = self.store.playlist(p['id'])
        self.assertEqual(saved['name'], 'Renamed')
        self.assertEqual(saved['cover'], 'custom.png')
        self.assertEqual(saved['tracks'][-1]['id'], original['id'])
        self.assertEqual(saved['tracks'][-1]['title'], 'Custom title')
        self.assertEqual(saved['tracks'][-1]['duration'], 123)
        stale = dict(saved['tracks'][0])
        saved['tracks'][0]['query'] = 'Changed search'
        self.store.save_playlist(saved['id'], saved['name'], saved['tracks'], saved['revision'])
        self.assertFalse(self.store.merge_metadata(saved['id'], stale, track('old search')))

    def test_existing_database_gets_cover_column_without_losing_playlists(self):
        self.store.db.execute('ALTER TABLE playlists DROP COLUMN cover')
        self.store.db.commit()
        self.store.db.close()
        self.store = Store(self.root / 'db.sqlite', self.root)
        p = self.store.playlist('mix')
        self.assertIsNone(p['cover'])
        self.assertEqual(len(p['tracks']), 2)

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
        creator = SimpleNamespace(display_avatar=SimpleNamespace(with_size=lambda size: SimpleNamespace(url='https://cdn.discordapp.com/creator.png')))
        bot = SimpleNamespace(get_guild=lambda _: self.guild, fetch_user=AsyncMock(return_value=creator))
        self.music = MusicController(bot, config, self.store)

    async def asyncTearDown(self):
        self.store.db.close()
        self.temp.cleanup()


class ControllerTests(ControllerCase):
    def test_playlist_title_removes_lyrics_suffix_but_preserves_song_name(self):
        for suffix in (' lyrics', ' (Lyrics)', ' - LYRICS', ' [Official Lyric Video]', ' | Lyrics Video (HD)', '【lyrics】'):
            with self.subTest(suffix=suffix):
                self.assertEqual(playlist_title({'title': 'Guster – Satellite' + suffix}), 'Guster - Satellite')
        self.assertEqual(playlist_title({'artist': 'Guster', 'track': 'Satellite', 'title': 'Unrelated video title'}),
                         'Guster - Satellite')
        self.assertEqual(playlist_title({'title': 'Guster - Lyrics in the Middle'}), 'Guster - Lyrics in the Middle')
        self.assertEqual(playlist_title({'title': 'Satellite (Lyrics)', 'uploader': 'Lyrics Channel'}), 'Satellite')

    async def test_spotify_lookup_failure_does_not_search_youtube(self):
        self.music.spotify.track = Mock(side_effect=SpotifyException(404, -1, 'Not found'))
        with patch('music_service.yt_dlp.YoutubeDL') as youtube:
            with self.assertRaisesRegex(ValueError, 'Could not look up this Spotify track'):
                await self.music.resolve('https://open.spotify.com/track/2up3OPMp9Tb4dAKM2erWXQ')
        youtube.assert_not_called()

    async def test_missing_playlist_thumbnails_are_saved_and_broadcast(self):
        rows = [track('song'), track('song'), track('unavailable'), track('covered', thumbnail='https://example.com/existing.jpg')]
        p = self.store.save_playlist(None, 'Mix', rows)
        event = asyncio.Event()
        self.music.listeners.add(event)
        self.music.resolve = AsyncMock(side_effect=[track('resolved', 'Found title', 120, 'https://example.com/thumb.jpg'), ValueError('Unavailable')])
        with self.assertLogs('music_service', level='WARNING'):
            await self.music.enrich_playlists()
        saved = self.store.playlist(p['id'])
        self.assertEqual([r['id'] for r in saved['tracks']], [r['id'] for r in rows])
        self.assertEqual(saved['tracks'][0]['title'], 'Found title')
        self.assertEqual(saved['tracks'][1]['thumbnail'], 'https://example.com/thumb.jpg')
        self.assertEqual(saved['tracks'][3]['thumbnail'], 'https://example.com/existing.jpg')
        self.assertEqual(self.music.resolve.await_count, 2)
        self.assertTrue(event.is_set())

    async def test_reorder_conflict_does_not_drop_simultaneously_added_song(self):
        a, b, c = track('a'), track('b'), track('c')
        self.music.queue = [a, b]
        revision = self.music.queue_revision
        self.music.queue.append(c)
        self.music.changed('friend', 'added c', queue=True)
        with self.assertRaises(ValueError):
            self.music.reorder([b['id'], a['id']], revision, 'old browser')
        self.assertEqual(self.music.queue, [a, b, c])

    async def test_background_metadata_updates_do_not_reject_queue_reordering(self):
        a, b = track('a'), track('b')
        self.music.queue = [a, b]
        revision = self.music.queue_revision
        self.music.changed('Kirbo', 'updated artwork')
        self.music.reorder([b['id'], a['id']], revision, 'friend')
        self.assertEqual(self.music.queue, [b, a])
        with self.assertRaises(ValueError):
            self.music.reorder([a['id'], b['id']], revision, 'stale browser')

    async def test_whole_playlist_appears_immediately_without_individual_searches(self):
        self.music.resolve = AsyncMock()
        self.music.kick = Mock()
        rows = [track(f'song {i}', thumbnail='https://example.com/thumb.jpg') for i in range(20)]
        member = SimpleNamespace(voice=SimpleNamespace(channel=self.vc.channel))
        await self.music.enqueue_playlist(rows, member, 'friend')
        self.assertEqual(len(self.music.snapshot()['queue']), 20)
        self.assertEqual([r['query'] for r in self.music.queue], [r['query'] for r in rows])
        self.assertTrue(all(r['added_by'] == 'friend' for r in self.music.queue))
        self.assertTrue(set(r['id'] for r in rows).isdisjoint(r['id'] for r in self.music.queue))
        self.music.resolve.assert_not_awaited()
        self.music.kick.assert_called_once()

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
        self.music.queue = [track('https://youtu.be/4tk1He_HFjE', 'Test track')]
        with patch('music_service.yt_dlp.YoutubeDL', Downloader), patch('music_service.discord.FFmpegOpusAudio', return_value=SimpleNamespace(cleanup=lambda:None)) as ffmpeg:
            self.music.kick()
            worker = self.music.worker
            await asyncio.wait_for(started.wait(), 3)
            self.assertEqual(self.music.current['title'], 'Test track')
            self.assertTrue(Path(paths[0]).exists())
            self.assertEqual(ffmpeg.call_args.args[0], paths[0])
            self.assertIn('loudnorm=I=-16:TP=-1.5:LRA=11', ffmpeg.call_args.kwargs['options'])
            callback(None)
            await asyncio.wait_for(worker, 3)
        self.assertFalse(Path(paths[0]).exists())
        self.assertIsNone(self.music.current)
        self.assertEqual(self.music.snapshot()['queue'], [])
        self.assertIsNone(self.music.snapshot()['current'])

    async def test_cleanup_errors_cannot_leave_a_stale_current_track_or_block_the_next(self):
        self.music.queue = [track('https://youtu.be/4tk1He_HFjE'), track('https://youtu.be/4tk1He_HFjE')]
        self.vc.play = lambda source, after: after(None)
        cleared = []
        def cleanup():
            cleared.append(self.music.current is None)
            raise OSError('File still in use')
        with patch('music_service.yt_dlp.YoutubeDL', FakeDownloader), patch('music_service.discord.FFmpegOpusAudio', return_value=SimpleNamespace(cleanup=cleanup)):
            with self.assertLogs('music_service', level='ERROR'):
                await self.music.advance()
        self.assertEqual(cleared, [True, True])
        self.assertIsNone(self.music.snapshot()['current'])
        self.assertEqual(self.music.snapshot()['position'], 0)
        self.assertFalse(self.music.queue)

    async def test_missing_discord_completion_callback_still_clears_idle_player(self):
        self.music.queue = [track('https://youtu.be/4tk1He_HFjE')]
        self.vc.play = lambda *args, **kwargs: None
        self.vc.is_playing = lambda: False
        with patch('music_service.yt_dlp.YoutubeDL', FakeDownloader), patch('music_service.discord.FFmpegOpusAudio', return_value=SimpleNamespace(cleanup=lambda: None)):
            await asyncio.wait_for(self.music.advance(), 3)
        self.assertIsNone(self.music.current)
        self.assertFalse(self.music.loading)
        self.assertFalse(self.music.queue)

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
        self.music.kick = Mock()
        self.music.spotify.track = Mock(return_value={'name': 'Satellite', 'artists': [{'name': 'Guster'}]})
        member = SimpleNamespace(id=12345, name='friend', voice=SimpleNamespace(channel=self.vc.channel))
        interaction = SimpleNamespace(user=member, response=SimpleNamespace(send_message=AsyncMock()),
                                      edit_original_response=AsyncMock())
        play = bot.tree.get_command('play', guild=discord.Object(id=42))
        with patch('music_service.yt_dlp.YoutubeDL') as youtube:
            extract = youtube.return_value.__enter__.return_value.extract_info
            extract.return_value = {'entries': [{'webpage_url': 'https://youtu.be/4tk1He_HFjE', 'title': 'Satellite (Lyrics)'}]}
            resolve = self.music.resolve
            async def resolve_after_message(query):
                interaction.response.send_message.assert_awaited_once()
                return await resolve(query)
            self.music.resolve = resolve_after_message
            await play.callback(interaction, 'https://open.spotify.com/track/4wOHTxl4euh0hMGU9CJERL?si=b632dd9c08a94945')
        self.music.spotify.track.assert_called_once_with('4wOHTxl4euh0hMGU9CJERL')
        extract.assert_called_once_with('ytsearch1:Satellite Guster lyrics', download=False)
        interaction.edit_original_response.assert_awaited_once_with(content='Added to queue: **Satellite (Lyrics)**')
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

    def expire_membership(self, cookie='test-cookie'):
        key = digest(cookie)
        session = json.loads(self.store.db.execute('SELECT payload FROM sessions WHERE id=?', (key,)).fetchone()[0])
        session['checked'] = 0
        self.store.db.execute('UPDATE sessions SET payload=? WHERE id=?', (json.dumps(session), key))
        self.store.db.commit()
        self.site.membership_cache.clear()

    async def test_all_private_endpoints_require_login(self):
        for path in ['/api/me','/api/state','/api/events']:
            response = await self.client.get(path)
            self.assertEqual(response.status, 401)
        response = await self.client.post('/api/action', json={'action':'clear'}, headers={'Origin':self.site.origin})
        self.assertEqual(response.status,401)
        self.assertEqual((await self.client.get('/.env')).status,404)

    def photo(self):
        output = io.BytesIO()
        Image.new('RGB', (900, 700), '#e89aba').save(output, format='PNG')
        return output.getvalue()

    async def test_cover_upload_is_private_persistent_and_shared(self):
        p = self.store.save_playlist(None, 'Mix', [track('song')])
        path = f"/api/playlists/{p['id']}/cover"
        self.assertEqual((await self.client.get(path)).status, 401)
        self.assertEqual((await self.client.post(path, data=self.photo(), headers={'Origin': self.site.origin})).status, 401)
        bad_headers = {k:v for k,v in self.headers.items() if k != 'X-CSRF-Token'}
        self.assertEqual((await self.client.post(path+'?revision=1', data=self.photo(), headers=bad_headers)).status, 403)
        event = asyncio.Event()
        self.music.listeners.add(event)
        events = await self.client.get('/api/events', headers=self.headers)
        await events.content.readline()
        await events.content.readline()
        response = await self.client.post(path+'?revision=1', data=self.photo(), headers=self.headers)
        self.assertEqual(response.status, 200, await response.text())
        saved = (await response.json())['playlists'][0]
        self.assertTrue(event.is_set())
        self.assertTrue((self.store.covers / saved['cover']).is_file())
        self.assertEqual(saved['revision'], 2)
        broadcast = await asyncio.wait_for(events.content.readline(), 2)
        self.assertIn(saved['cover'].encode(), broadcast)
        events.close()
        other = dict(user={'id':'67890', 'username':'other friend'}, csrf='other-csrf', checked=time.time(), token={})
        self.store.db.execute('INSERT INTO sessions VALUES(?,?,?)', (digest('other-cookie'), json.dumps(other), time.time()+300))
        self.store.db.commit()
        other_headers = {'Cookie': 'kirbo_session=other-cookie'}
        response = await self.client.get('/api/state', headers=other_headers)
        self.assertEqual((await response.json())['playlists'][0]['cover'], saved['cover'])
        response = await self.client.get(path, headers=other_headers)
        self.assertEqual(response.status, 200)
        with Image.open(io.BytesIO(await response.read())) as image:
            self.assertEqual(image.format, 'PNG')
            self.assertEqual(image.size, (640, 498))
        self.store.save_playlist(saved['id'], 'Renamed', saved['tracks'], saved['revision'])
        self.assertEqual(self.store.playlist(p['id'])['cover'], saved['cover'])
        reopened = Store(self.store.covers.parent / 'db.sqlite', self.store.covers.parent)
        try:
            self.assertEqual(reopened.playlist(p['id'])['cover'], saved['cover'])
        finally:
            reopened.db.close()
        response = await self.client.post(path+'?revision=3', data=self.photo(), headers=self.headers)
        self.assertEqual(response.status, 200)
        self.assertFalse((self.store.covers / saved['cover']).exists())
        self.assertEqual(len(list(self.store.covers.glob('*.png'))), 1)
        response = await self.client.post('/api/action', json={'action':'delete_playlist','id':p['id'],'revision':4}, headers=self.headers)
        self.assertEqual(response.status, 200)
        self.assertFalse(list(self.store.covers.glob('*.png')))
        self.assertEqual((await self.client.get(path, headers=self.headers)).status, 404)

    async def test_invalid_oversized_and_stale_cover_uploads_do_not_replace_photo(self):
        p = self.store.save_playlist(None, 'Mix', [])
        path = f"/api/playlists/{p['id']}/cover"
        for body in (b'<svg onload="alert(1)"></svg>', b'not an image'):
            response = await self.client.post(path+'?revision=1', data=body, headers=self.headers)
            self.assertEqual(response.status, 409)
        response = await self.client.post(path+'?revision=1', data=io.BytesIO(b'x'*(MAX_COVER_BYTES+1)), headers=self.headers)
        self.assertEqual(response.status, 413)
        self.store.save_playlist(p['id'], 'Changed', [], p['revision'])
        response = await self.client.post(path+'?revision=1', data=self.photo(), headers=self.headers)
        self.assertEqual(response.status, 409)
        self.assertIsNone(self.store.playlist(p['id'])['cover'])
        self.assertFalse(self.store.covers.exists())

    async def test_edit_during_photo_processing_cannot_be_overwritten(self):
        p = self.store.save_playlist(None, 'Mix', [])
        path = f"/api/playlists/{p['id']}/cover?revision=1"
        async def process(function, data):
            self.store.save_playlist(p['id'], 'Concurrent rename', [], 1)
            return cover_image(data)
        with patch('web.app.asyncio.to_thread', side_effect=process):
            response = await self.client.post(path, data=self.photo(), headers=self.headers)
        self.assertEqual(response.status, 409)
        self.assertEqual(self.store.playlist(p['id'])['name'], 'Concurrent rename')
        self.assertFalse(self.store.covers.exists())

    async def test_cross_origin_and_missing_csrf_cannot_change_queue(self):
        self.music.queue = [track('protected')]
        for headers in [dict(self.headers,Origin='https://attacker.test'), {'Cookie':self.headers['Cookie'],'Origin':self.site.origin}]:
            response = await self.client.post('/api/action',json={'action':'clear'},headers=headers)
            self.assertEqual(response.status,403)
        self.assertEqual(len(self.music.queue),1)

    async def test_cookie_does_not_allow_removed_member_to_mutate(self):
        from aiohttp import web
        self.expire_membership()
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

    async def test_compressed_requests_never_reach_the_decoder(self):
        import brotli
        from aiohttp.http_parser import BrotliDecompressor
        with patch.object(BrotliDecompressor, 'decompress_sync', side_effect=AssertionError('Decoder must not run')) as decode:
            response = await self.client.post('/api/action', data=brotli.compress(b'{"action":"clear"}'),
                headers={'Content-Encoding':'br', 'Origin':self.site.origin})
        self.assertEqual(response.status, 415)
        decode.assert_not_called()

    async def test_login_flood_cannot_block_a_different_visitor_or_music_commands(self):
        self.site.client_id, self.site.secret = 'test-id', 'test-secret'
        for _ in range(LOGIN_ATTEMPTS):
            response = await self.client.get('/auth/login', allow_redirects=False, headers={'CF-Connecting-IP':'192.0.2.1'})
            self.assertEqual(response.status, 302)
        response = await self.client.get('/auth/login', allow_redirects=False, headers={'CF-Connecting-IP':'192.0.2.1'})
        self.assertEqual(response.status, 429)
        self.assertIn('Retry-After', response.headers)
        self.assertEqual((await self.client.get('/auth/login', allow_redirects=False, headers={'CF-Connecting-IP':'192.0.2.2'})).status, 302)
        self.assertEqual((await self.client.post('/api/action', json={'action':'clear'}, headers=self.headers)).status, 200)
        self.site.login_attempts['192.0.2.1'] = (time.monotonic()-LOGIN_WINDOW, LOGIN_ATTEMPTS)
        self.site.login_attempts.move_to_end('192.0.2.1', last=False)
        self.assertEqual((await self.client.get('/auth/login', allow_redirects=False, headers={'CF-Connecting-IP':'192.0.2.1'})).status, 302)

    async def test_repeated_sign_in_reuses_browser_state_and_full_pool_does_not_refuse_everyone(self):
        from urllib.parse import urlparse, parse_qs
        self.site.client_id, self.site.secret = 'test-id', 'test-secret'
        first = await self.client.get('/auth/login', allow_redirects=False)
        state = parse_qs(urlparse(first.headers['Location']).query)['state'][0]
        second = await self.client.get('/auth/login', allow_redirects=False, headers={'Cookie':'kirbo_oauth='+state})
        self.assertEqual(parse_qs(urlparse(second.headers['Location']).query)['state'][0], state)
        self.assertEqual(len(self.site.states), 1)
        self.site.states = {str(i):time.time()+600 for i in range(MAX_PENDING_LOGINS)}
        response = await self.client.get('/auth/login', allow_redirects=False)
        self.assertEqual(response.status, 302)
        self.assertEqual(len(self.site.states), MAX_PENDING_LOGINS)

    def test_only_loopback_proxy_can_supply_a_cloudflare_visitor_address(self):
        headers = {'CF-Connecting-IP':'198.51.100.1', 'X-Forwarded-For':'198.51.100.2'}
        self.assertEqual(login_address(SimpleNamespace(remote='203.0.113.1', headers=headers)), '203.0.113.1')
        self.assertEqual(login_address(SimpleNamespace(remote='127.0.0.1', headers=headers)), '198.51.100.1')
        self.assertEqual(login_address(SimpleNamespace(remote='::1', headers={'CF-Connecting-IP':'invalid'})), '::1')
        for address in ('2001:db8::1', '2001:db8::abcd'):
            self.assertEqual(login_address(SimpleNamespace(remote='127.0.0.1', headers={'CF-Connecting-IP':address})), '2001:db8::/64')

    async def test_security_headers_apply_to_the_prepared_live_stream(self):
        events = await self.client.get('/api/events', headers=self.headers)
        self.assertEqual(events.headers['Strict-Transport-Security'], 'max-age=15552000')
        self.assertEqual(events.headers['Cache-Control'], 'no-store')
        self.assertEqual(events.headers['X-Frame-Options'], 'DENY')
        events.close()
        self.site.secure = False
        response = await self.client.get('/')
        self.assertNotIn('Strict-Transport-Security', response.headers)

    async def test_production_start_rejects_public_http_binding(self):
        with patch.dict('os.environ', {'WEB_BIND':'0.0.0.0'}):
            with self.assertRaisesRegex(ValueError, 'loopback'):
                await start(self.music)

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

    async def test_creator_avatar_is_shared_and_cached_without_changing_signed_in_user(self):
        for _ in range(2):
            response = await self.client.get('/api/me', headers=self.headers)
            self.assertEqual(response.status, 200)
            data = await response.json()
            self.assertEqual(data['creator_avatar'], 'https://cdn.discordapp.com/creator.png')
            self.assertEqual(data['id'], '12345')
            self.assertEqual(data['username'], 'friend')
        self.music.bot.fetch_user.assert_awaited_once_with(CREATOR_ID)
        self.site.creator_avatar_expires = 0
        await self.client.get('/api/me', headers=self.headers)
        self.assertEqual(self.music.bot.fetch_user.await_count, 2)

    async def test_creator_avatar_failure_keeps_sign_in_working_and_reuses_previous_photo(self):
        await self.client.get('/api/me', headers=self.headers)
        self.site.creator_avatar_expires = 0
        self.music.bot.fetch_user.side_effect = asyncio.TimeoutError()
        with self.assertLogs(level='WARNING'):
            response = await self.client.get('/api/me', headers=self.headers)
        self.assertEqual(response.status, 200)
        self.assertEqual((await response.json())['creator_avatar'], 'https://cdn.discordapp.com/creator.png')
        await self.client.get('/api/me', headers=self.headers)
        self.assertEqual(self.music.bot.fetch_user.await_count, 2)

    async def test_anonymous_requests_cannot_trigger_creator_profile_lookups(self):
        self.assertEqual((await self.client.get('/api/me')).status, 401)
        self.music.bot.fetch_user.assert_not_awaited()

    async def test_action_verifies_membership_only_once(self):
        self.expire_membership()
        response = await self.client.post('/api/action', json={'action':'clear'}, headers=self.headers)
        self.assertEqual(response.status, 200)
        await self.client.post('/api/action', json={'action':'clear'}, headers=self.headers)
        self.site.membership.assert_awaited_once()

    async def test_twenty_rapid_additions_reuse_verified_membership_and_voice_member(self):
        member = SimpleNamespace(voice=SimpleNamespace(channel=self.vc.channel))
        self.guild.get_member = lambda _: None
        self.guild.fetch_member = AsyncMock(return_value=member)
        async def resolve(query): return track(query)
        self.music.resolve = resolve
        self.music.kick = lambda: None
        for i in range(20):
            response = await self.client.post('/api/action', json={'action':'add','query':f'song {i}'}, headers=self.headers)
            self.assertEqual(response.status, 200, await response.text())
        self.assertEqual(len(self.music.queue), 20)
        self.site.membership.assert_not_awaited()
        self.guild.fetch_member.assert_awaited_once()

    async def test_spotify_track_links_work_in_all_website_song_fields(self):
        member = SimpleNamespace(voice=SimpleNamespace(channel=self.vc.channel))
        self.guild.get_member = lambda _: member
        self.music.kick = Mock()
        playlist = self.store.save_playlist(None, 'Mix', [])
        spotify_id = '2up3OPMp9Tb4dAKM2erWXQ'
        url = f'https://open.spotify.com/intl-de/track/{spotify_id}?si=shared'
        self.music.spotify.track = Mock(return_value={
            'name': 'Example Song', 'artists': [{'name': 'First Artist'}, {'name': 'Second Artist'}],
        })
        actions = [
            {'action': 'add', 'query': url},
            {'action': 'playlist_song', 'query': url, 'id': playlist['id'], 'revision': playlist['revision']},
            {'action': 'queue_playlist', 'url': url},
        ]
        video = 'https://youtu.be/4tk1He_HFjE'
        with patch('music_service.yt_dlp.YoutubeDL') as youtube:
            extract = youtube.return_value.__enter__.return_value.extract_info
            extract.return_value = {'entries': [{'webpage_url': video, 'title': 'Example Song (Lyrics)'}]}
            for data in actions:
                response = await self.client.post('/api/action', json=data, headers=self.headers)
                self.assertEqual(response.status, 200, await response.text())
        self.assertEqual(self.music.spotify.track.call_count, 3)
        self.assertTrue(all(call.args == (spotify_id,) for call in self.music.spotify.track.call_args_list))
        self.assertEqual([call.args[0] for call in extract.call_args_list],
                         ['ytsearch1:Example Song First Artist Second Artist lyrics'] * 3)
        self.assertEqual([row['query'] for row in self.music.queue], [video, video])
        self.assertEqual(self.store.playlist(playlist['id'])['tracks'][0]['query'], video)
        self.assertEqual(self.store.playlist(playlist['id'])['tracks'][0]['title'], 'First Artist, Second Artist - Example Song')
        self.assertEqual(self.music.queue[0]['title'], 'Example Song (Lyrics)')

    async def test_playlist_text_search_uses_lyrics_and_preserves_custom_titles(self):
        playlist = self.store.save_playlist(None, 'Mix', [])
        data = {'action': 'playlist_song', 'id': playlist['id'], 'revision': playlist['revision'], 'query': 'Satellite Guster'}
        video = 'https://youtu.be/4tk1He_HFjE'
        with patch('music_service.yt_dlp.YoutubeDL') as youtube:
            extract = youtube.return_value.__enter__.return_value.extract_info
            extract.return_value = {'entries': [{'webpage_url': video, 'title': 'Guster - Satellite (Lyrics)'}]}
            result = await self.client.post('/api/action', json=data, headers=self.headers)
            self.assertEqual(result.status, 200, await result.text())
            extract.assert_called_once_with('ytsearch1:Satellite Guster lyrics', download=False)
            saved = self.store.playlist(playlist['id'])
            self.assertEqual(saved['tracks'][0]['title'], 'Guster - Satellite')
            extract.return_value = extract.return_value['entries'][0]
            result = await self.client.post('/api/action', headers=self.headers, json=dict(
                data, query=video, song_id=saved['tracks'][0]['id'], revision=saved['revision'], title='My custom title'))
            self.assertEqual(result.status, 200, await result.text())
            extract.assert_called_with(video, download=False)
        self.assertEqual(self.store.playlist(playlist['id'])['tracks'][0]['title'], 'My custom title')

    async def test_expired_membership_uses_the_bot_instead_of_repeating_oauth_requests(self):
        self.expire_membership()
        payload = json.loads(self.store.db.execute('SELECT payload FROM sessions WHERE id=?', (digest('test-cookie'),)).fetchone()[0])
        payload['token'] = {'expires_at': time.time()+3600, 'access_token': 'test-token'}
        self.store.db.execute('UPDATE sessions SET payload=? WHERE id=?', (json.dumps(payload), digest('test-cookie')))
        self.store.db.commit()
        self.site.membership = Website.membership.__get__(self.site)
        member = SimpleNamespace(id=12345, name='friend', avatar=None, voice=SimpleNamespace(channel=self.vc.channel))
        self.guild.fetch_member = AsyncMock(return_value=member)
        self.site.discord = AsyncMock(side_effect=AssertionError('Repeated OAuth membership request'))
        self.music.kick = lambda: None
        self.music.resolve = AsyncMock(side_effect=lambda query: track(query))
        for i in range(10):
            response = await self.client.post('/api/action', json={'action':'add', 'query':f'song {i}'}, headers=self.headers)
            self.assertEqual(response.status, 200, await response.text())
        self.guild.fetch_member.assert_awaited_once_with(12345)
        self.site.discord.assert_not_awaited()

    async def test_one_users_pending_membership_check_does_not_block_another_user(self):
        self.expire_membership()
        other = dict(user={'id':'67890', 'username':'other'}, csrf='other', checked=0, token={})
        self.store.db.execute('INSERT INTO sessions VALUES(?,?,?)', (digest('other-cookie'), json.dumps(other), time.time()+300))
        self.store.db.commit()
        started, release = asyncio.Event(), asyncio.Event()
        async def verify(session):
            if session['user']['id'] == '12345':
                started.set()
                await release.wait()
            session['checked'] = time.time()
        self.site.membership.side_effect = verify
        pending = asyncio.create_task(self.client.get('/api/state', headers=self.headers))
        await asyncio.wait_for(started.wait(), 2)
        try:
            other_response = await asyncio.wait_for(self.client.get('/api/state', headers={'Cookie':'kirbo_session=other-cookie'}), 2)
            self.assertEqual(other_response.status, 200)
        finally:
            release.set()
            self.assertEqual((await pending).status, 200)

    async def test_live_updates_resume_after_temporary_verification_failure(self):
        from aiohttp import web
        events = await self.client.get('/api/events', headers=self.headers)
        await events.content.readline()
        await events.content.readline()
        self.expire_membership()
        calls = 0
        async def verify(session):
            nonlocal calls
            calls += 1
            if calls == 1:
                raise web.HTTPServiceUnavailable(headers={'Retry-After':'0'})
            session['checked'] = time.time()
        self.site.membership.side_effect = verify
        self.music.changed('friend', 'updated the queue')
        self.assertEqual(await asyncio.wait_for(events.content.readline(), 2), b'event: verification\n')
        self.assertEqual(await events.content.readline(), b'data: {}\n')
        await events.content.readline()
        self.assertIn(b'updated the queue', await asyncio.wait_for(events.content.readline(), 3))
        self.assertEqual(calls, 2)
        events.close()

    async def test_removed_member_stream_stops_without_sending_more_private_data(self):
        from aiohttp import web
        events = await self.client.get('/api/events', headers=self.headers)
        await events.content.readline()
        await events.content.readline()
        self.expire_membership()
        self.site.membership.side_effect = web.HTTPForbidden(reason='Removed member')
        self.music.changed('friend', 'private event')
        self.assertEqual(await asyncio.wait_for(events.content.readline(), 2), b'event: signed-out\n')
        self.assertEqual(await events.content.readline(), b'data: {}\n')
        events.close()

    async def test_two_users_can_submit_songs_concurrently(self):
        other_session = dict(user={'id':'67890','username':'other friend','avatar':None}, csrf='other-csrf', checked=time.time(), token={})
        self.store.db.execute('INSERT INTO sessions VALUES(?,?,?)', (digest('other-cookie'), json.dumps(other_session), time.time()+300))
        self.store.db.commit()
        other_headers = dict(self.headers, Cookie='kirbo_session=other-cookie', **{'X-CSRF-Token':'other-csrf'})
        member = SimpleNamespace(voice=SimpleNamespace(channel=self.vc.channel))
        self.guild.get_member = lambda _: member
        both_started = asyncio.Event()
        actors = []
        async def add(query, member, actor, next_up):
            actors.append(actor)
            if len(actors) == 2:
                both_started.set()
            await asyncio.wait_for(both_started.wait(), 2)
        self.music.add = add
        responses = await asyncio.gather(*[self.client.post('/api/action', json={'action':'add','query':'song'}, headers=headers) for headers in (self.headers,other_headers)])
        self.assertEqual([r.status for r in responses], [200,200])
        self.assertEqual(set(actors), {'friend','other friend'})

    async def test_browser_sessions_share_verification_but_not_secrets(self):
        self.expire_membership()
        await self.client.get('/api/me', headers=self.headers)
        other = dict(user={'id':'12345','username':'friend','avatar':None}, csrf='other-csrf', checked=0, token={'access_token':'other-token'})
        self.store.db.execute('INSERT INTO sessions VALUES(?,?,?)', (digest('other-cookie'), json.dumps(other), time.time()+300))
        self.store.db.commit()
        response = await self.client.get('/api/me', headers={'Cookie':'kirbo_session=other-cookie'})
        self.assertEqual(response.status, 200)
        self.assertEqual((await response.json())['csrf'], 'other-csrf')
        self.site.membership.assert_awaited_once()
        saved = json.loads(self.store.db.execute('SELECT payload FROM sessions WHERE id=?', (digest('other-cookie'),)).fetchone()[0])
        self.assertEqual(saved['token']['access_token'], 'other-token')

    def discord_response(self, status, data, headers=None):
        response = Mock(status=status, headers=headers or {})
        response.json = AsyncMock(return_value=data)
        response.__aenter__ = AsyncMock(return_value=response)
        response.__aexit__ = AsyncMock(return_value=False)
        return response

    async def test_short_discord_rate_limit_retries_before_verifying(self):
        responses = [self.discord_response(429, {'retry_after':0}),
                     self.discord_response(200, {'user':{'id':'12345'}})]
        with patch.object(self.site.http, 'get', side_effect=responses) as get:
            result = await self.site.discord('/membership-test', 'private-token')
        self.assertEqual(result['user']['id'], '12345')
        self.assertEqual(get.call_count, 2)

    async def test_long_discord_rate_limit_blocks_without_repeating_requests(self):
        from aiohttp import web
        with patch.object(self.site.http, 'get', return_value=self.discord_response(429, {'retry_after':60})) as get:
            for _ in range(2):
                with self.assertRaises(web.HTTPServiceUnavailable) as caught:
                    await self.site.discord('/membership-test', 'private-token')
                self.assertIn('Retry-After', caught.exception.headers)
            self.assertEqual(get.call_count, 1)

    async def test_upstream_error_reports_status_without_token(self):
        from aiohttp import web
        with patch.object(self.site.http, 'get', return_value=self.discord_response(502, {})):
            with self.assertLogs(level='WARNING') as logs:
                with self.assertRaises(web.HTTPServiceUnavailable) as caught:
                    await self.site.discord('/membership-test', 'private-token')
        self.assertIn('502', caught.exception.reason)
        self.assertNotIn('private-token', ''.join(logs.output))

    async def test_failed_verification_never_modifies_queue_or_deletes_session(self):
        from aiohttp import web
        self.expire_membership()
        self.music.queue = [track('protected')]
        self.site.membership.side_effect = web.HTTPServiceUnavailable(reason='Discord rate-limited membership checks.')
        response = await self.client.post('/api/action', json={'action':'clear'}, headers=self.headers)
        self.assertEqual(response.status, 503)
        self.assertEqual(len(self.music.queue), 1)
        self.assertIsNotNone(self.store.db.execute('SELECT id FROM sessions WHERE id=?', (digest('test-cookie'),)).fetchone())


if __name__ == '__main__':
    unittest.main()
