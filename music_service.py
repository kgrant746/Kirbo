"""The single music player used by slash commands and the website."""
from __future__ import annotations

import asyncio
import json
import logging
import random
import re
import sqlite3
import tempfile
import time
import uuid
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import discord
import spotipy
import yt_dlp

ROOT = Path(__file__).parent
log = logging.getLogger(__name__)


def validate_query(value):
    value = str(value).strip()
    if not value or len(value) > 500:
        raise ValueError("Enter a song name or YouTube URL (up to 500 characters).")
    if "://" in value:
        parsed = urlparse(value)
        if parsed.scheme != "https" or parsed.hostname not in {"youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com", "youtu.be"}:
            raise ValueError("Only HTTPS YouTube links and song searches are supported.")
        video = parsed.path.strip('/') if parsed.hostname == 'youtu.be' else (
            parse_qs(parsed.query).get('v', [''])[0] if parsed.path == '/watch' else
            parsed.path.strip('/').split('/')[-1] if parsed.path.startswith(('/shorts/', '/live/')) else '')
        if not re.fullmatch(r'[A-Za-z0-9_-]{11}', video):
            raise ValueError('Use a direct YouTube video link, or enter a song search.')
    return value


def track(query, title=None, duration=None, thumbnail=None):
    return dict(id=uuid.uuid4().hex, query=validate_query(query), title=title or query,
                duration=duration, thumbnail=thumbnail, artist="", added_by="")


class Store:
    def __init__(self, path=ROOT / "data" / "controller.sqlite3", playlists=ROOT / "playlists"):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.covers = Path(path).parent / 'playlist-covers'
        self.db = sqlite3.connect(path)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS playlists(id TEXT PRIMARY KEY, name TEXT, tracks TEXT, revision INTEGER);
            CREATE TABLE IF NOT EXISTS activity(id INTEGER PRIMARY KEY, at REAL, actor TEXT, message TEXT);
            CREATE TABLE IF NOT EXISTS sessions(id TEXT PRIMARY KEY, payload TEXT, expires REAL);
            CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT);
        """)
        if 'cover' not in {row[1] for row in self.db.execute('PRAGMA table_info(playlists)')}:
            self.db.execute('ALTER TABLE playlists ADD COLUMN cover TEXT')
            self.db.commit()
        if not self.db.execute("SELECT 1 FROM settings WHERE key='txt_imported'").fetchone():
            for file in sorted(Path(playlists).glob("*.txt")):
                rows = []
                for line in file.read_text(encoding="utf-8-sig").splitlines():
                    if line.strip() and not line.lstrip().startswith("#"):
                        try:
                            rows.append(track(line.strip()))
                        except ValueError:
                            log.warning("Skipped unsupported entry in %s", file.name)
                self.save_playlist(None, file.stem, rows)
            self.db.execute("INSERT INTO settings VALUES('txt_imported','1')")
            self.db.commit()

    def playlists(self):
        return [dict(id=r[0], name=r[1], tracks=json.loads(r[2]), revision=r[3], cover=r[4])
                for r in self.db.execute("SELECT id,name,tracks,revision,cover FROM playlists ORDER BY name COLLATE NOCASE")]

    def playlist(self, ident):
        return next((p for p in self.playlists() if p['id'] == ident or p['name'].casefold() == ident.removesuffix('.txt').casefold()), None)

    def save_playlist(self, ident, name, rows, revision=None):
        name = str(name).strip()
        if not name or len(name) > 100 or len(rows) > 1000:
            raise ValueError("Use a playlist name up to 100 characters and at most 1,000 songs.")
        old = self.playlist(ident) if ident else None
        if ident and not old:
            raise ValueError("Playlist no longer exists.")
        if old and revision != old['revision']:
            raise ValueError("Someone else edited this playlist. Your view has been refreshed; try again.")
        ident = old['id'] if old else uuid.uuid4().hex
        clean = []
        ids = set()
        for item in rows:
            row = track(item['query'], str(item.get('title') or item['query'])[:500], item.get('duration'), item.get('thumbnail'))
            row['id'] = str(item.get('id') or row['id'])[:64]
            if row['id'] in ids:
                row['id'] = uuid.uuid4().hex
            ids.add(row['id'])
            row['artist'] = str(item.get('artist', ''))[:200]
            clean.append(row)
        self.db.execute("INSERT OR REPLACE INTO playlists(id,name,tracks,revision,cover) VALUES(?,?,?,?,?)",
                        (ident, name, json.dumps(clean), (old['revision'] + 1) if old else 1, old['cover'] if old else None))
        self.db.commit()
        return self.playlist(ident)

    def merge_metadata(self, ident, original, resolved):
        playlist = self.playlist(ident)
        if not playlist:
            return False
        row = next((r for r in playlist['tracks'] if r['id'] == original['id']), None)
        if not row or row['query'] != original['query'] or row.get('thumbnail'):
            return False
        for key in ('thumbnail', 'duration', 'artist'):
            row[key] = resolved.get(key)
        if row['title'] == row['query']:
            row['title'] = resolved['title']
        self.save_playlist(ident, playlist['name'], playlist['tracks'], playlist['revision'])
        return True

    def activity(self, actor, message):
        self.db.execute("INSERT INTO activity(at,actor,message) VALUES(?,?,?)", (time.time(), actor, message))
        self.db.execute("DELETE FROM activity WHERE id NOT IN (SELECT id FROM activity ORDER BY id DESC LIMIT 500)")
        self.db.commit()

    def logs(self):
        return [dict(id=r[0], at=r[1], actor=r[2], message=r[3]) for r in self.db.execute("SELECT * FROM activity ORDER BY id DESC LIMIT 200")]


class MusicController:
    def __init__(self, bot, config, store=None):
        self.bot, self.config = bot, config
        self.store = store or Store()
        self.queue, self.current = [], None
        self.loading, self.paused, self.stopping = False, False, False
        self.revision, self.generation = 0, 0
        self.batch_epoch = 0
        self.search_slots = asyncio.Semaphore(3)
        self.listeners = set()
        self.worker = None
        self.connect_lock = asyncio.Lock()
        self.tasks = set()
        self.spotify = spotipy.Spotify(auth_manager=spotipy.SpotifyClientCredentials(
            client_id=config.SPOTIFY_CLIENT_ID, client_secret=config.SPOTIFY_CLIENT_SECRET), requests_timeout=20)

    @property
    def guild(self):
        return self.bot.get_guild(self.config.GUILD_ID)

    def snapshot(self):
        vc = self.guild.voice_client if self.guild else None
        return dict(queue=self.queue, current=self.current, loading=self.loading, paused=self.paused,
                    channel=vc.channel.name if vc and vc.is_connected() else None,
                    revision=self.revision, playlists=self.store.playlists(), logs=self.store.logs())

    def changed(self, actor=None, message=None):
        if actor and message:
            self.store.activity(str(actor), message)
        self.revision += 1
        for event in tuple(self.listeners):
            event.set()

    def background(self, coro):
        task = asyncio.create_task(coro)
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)
        return task

    async def connect(self, member):
        if not getattr(member, 'voice', None) or not member.voice.channel:
            raise ValueError("Join a Team Rocket 2.0 voice channel in Discord first.")
        async with self.connect_lock:
            vc = self.guild.voice_client
            if not vc:
                vc = await member.voice.channel.connect()
            elif vc.channel.id != member.voice.channel.id:
                if vc.is_playing() or vc.is_paused() or self.loading:
                    raise ValueError("Join Kirbo’s current voice channel to control playback.")
                await vc.move_to(member.voice.channel)
            self.stopping = False
            return vc

    async def resolve(self, query):
        query = validate_query(query)
        def extract():
            with yt_dlp.YoutubeDL(dict(format="bestaudio/best", noplaylist=True, quiet=True, socket_timeout=20)) as ydl:
                info = ydl.extract_info(query if query.startswith('https://') else f'ytsearch1:{query}', download=False)
                info = next((x for x in info.get('entries', []) if x), None) if 'entries' in info else info
                if not info:
                    raise ValueError("No matching YouTube video found.")
                if info.get('is_live'):
                    raise ValueError("Live streams are not supported by the download player.")
                row = track(info['webpage_url'], info.get('title'), info.get('duration'), info.get('thumbnail'))
                row['artist'] = info.get('uploader') or info.get('channel') or ''
                return row
        async with self.search_slots:
            return await asyncio.to_thread(extract)

    async def enrich_playlists(self):
        # Old text playlists have queries only; publish each result without blocking the UI.
        resolved_queries = {}
        for playlist in self.store.playlists():
            for row in playlist['tracks']:
                if row.get('thumbnail'):
                    continue
                try:
                    if row['query'] not in resolved_queries:
                        resolved_queries[row['query']] = None
                        resolved_queries[row['query']] = await self.resolve(row['query'])
                    resolved = resolved_queries[row['query']]
                    if resolved and self.store.merge_metadata(playlist['id'], row, resolved):
                        self.changed()
                except Exception:
                    log.warning('Could not retrieve artwork for a song in playlist %s', playlist['id'])

    async def add(self, query, member, actor, next_up=False):
        await self.connect(member)
        epoch = self.batch_epoch
        row = await self.resolve(query)
        if self.stopping or epoch != self.batch_epoch:
            raise ValueError("Playback was stopped while the song was being searched. Try again.")
        if len(self.queue) >= 1000:
            raise ValueError("The queue is full (1,000 songs).")
        row['added_by'] = actor
        self.queue.insert(0 if next_up else len(self.queue), row)
        self.changed(actor, f"added {row['title']}" + (" to play next" if next_up else " to the queue"))
        self.kick()
        return row

    def kick(self):
        vc = self.guild.voice_client if self.guild else None
        if vc and vc.is_connected() and not self.stopping and not self.current and self.queue and (not self.worker or self.worker.done()):
            self.worker = asyncio.create_task(self.advance())

    async def advance(self):
        try:
            while self.queue and not self.stopping:
                vc = self.guild.voice_client if self.guild else None
                if not vc or not vc.is_connected():
                    return
                row = self.queue.pop(0)
                generation = self.generation
                self.current, self.loading = row, True
                self.changed()
                directory = tempfile.TemporaryDirectory(prefix='kirbo-')
                stderr = tempfile.TemporaryFile()
                try:
                    def download():
                        with yt_dlp.YoutubeDL(dict(format='bestaudio/best', noplaylist=True, quiet=True,
                                                  socket_timeout=30, outtmpl=str(Path(directory.name) / '%(id)s.%(ext)s'))) as ydl:
                            info = ydl.extract_info(row['query'], download=True)
                            return ydl.prepare_filename(info)
                    filename = await asyncio.to_thread(download)
                    if generation != self.generation or not vc.is_connected():
                        continue
                    source = discord.FFmpegOpusAudio(filename, options='-vn -b:a 96k', executable=self.config.FFMPEG_PATH, stderr=stderr)
                    source.title = row['title']
                    done = asyncio.get_running_loop().create_future()
                    loop = asyncio.get_running_loop()
                    def after(error):
                        def finish():
                            if not done.done():
                                done.set_result(error)
                        loop.call_soon_threadsafe(finish)
                    try:
                        vc.play(source, after=after)
                        self.loading = False
                        self.changed('Kirbo', f"started playing {row['title']}")
                        error = await done
                        if error:
                            raise error
                    finally:
                        source.cleanup()
                    stderr.seek(0)
                    detail = stderr.read().decode(errors='replace')
                    if 'Error' in detail or '403' in detail:
                        log.warning('FFmpeg: %s', re.sub(r'https?://\S+', '[stream URL]', detail)[-2000:])
                except Exception:
                    log.exception('Track playback failed')
                    self.changed('Kirbo', f"could not play {row['title']}; skipped it")
                finally:
                    stderr.close()
                    directory.cleanup()
                    self.current, self.loading, self.paused = None, False, False
                    self.changed()
        finally:
            self.worker = None
            if self.queue and not self.stopping and self.guild and self.guild.voice_client:
                self.kick()

    async def control(self, action, actor, member=None):
        vc = self.guild.voice_client if self.guild else None
        if member and vc and (not member.voice or not member.voice.channel or member.voice.channel.id != vc.channel.id):
            raise ValueError("Join Kirbo’s voice channel to control playback.")
        if action == 'stop':
            self.stopping = True
            self.batch_epoch += 1
            self.generation += 1
            self.queue.clear()
            if vc:
                vc.stop()
                await vc.disconnect()
            self.current, self.paused = None, False
        elif action == 'skip':
            self.generation += 1
            if vc:
                vc.stop()
        elif action == 'pause' and vc and vc.is_playing():
            vc.pause()
            self.paused = True
        elif action == 'resume' and vc and vc.is_paused():
            vc.resume()
            self.paused = False
        elif action == 'shuffle':
            random.shuffle(self.queue)
        elif action == 'clear':
            self.batch_epoch += 1
            self.queue.clear()
        else:
            raise ValueError("That playback action is unavailable right now.")
        self.changed(actor, f"used {action}")

    def remove(self, ident, actor):
        row = next((r for r in self.queue if r['id'] == ident), None)
        if not row:
            raise ValueError("That song has already left the queue.")
        self.queue.remove(row)
        self.changed(actor, f"removed {row['title']} from the queue")

    def reorder(self, ids, revision, actor):
        if revision != self.revision or len(ids) != len(self.queue) or set(ids) != {r['id'] for r in self.queue}:
            raise ValueError("The queue changed while you were dragging. Try again on the updated queue.")
        by_id = {r['id']: r for r in self.queue}
        self.queue = [by_id[i] for i in ids]
        self.changed(actor, 'reordered the queue')

    async def spotify_rows(self, url):
        parsed = urlparse(url)
        if parsed.scheme != 'https' or parsed.hostname != 'open.spotify.com' or not re.fullmatch(r'/playlist/[A-Za-z0-9]+/?', parsed.path):
            raise ValueError('Enter an HTTPS Spotify playlist link.')
        def fetch():
            rows = []
            page = self.spotify.playlist_items(parsed.path.strip('/').split('/')[-1], additional_types=['track'])
            while page and len(rows) < 1000:
                for item in page.get('items', []):
                    song = item.get('track') or {}
                    if not song.get('name') or song.get('is_local'):
                        continue
                    artist = ', '.join(a['name'] for a in song.get('artists', []))
                    images = (song.get('album') or {}).get('images') or []
                    row = track(f"{song['name']} {artist}", song['name'], (song.get('duration_ms') or 0) / 1000, images[-1]['url'] if images else None)
                    row['artist'] = artist
                    rows.append(row)
                page = self.spotify.next(page) if page.get('next') and len(rows) < 1000 else None
            return rows[:1000]
        return await asyncio.to_thread(fetch)

    async def enqueue_playlist(self, rows, member, actor, shuffle=False):
        if shuffle:
            rows = random.sample(rows, len(rows))
        await self.connect(member)
        epoch = self.batch_epoch
        async def fill():
            for row in rows:
                if self.stopping or epoch != self.batch_epoch:
                    break
                if not member.voice or not member.voice.channel:
                    self.changed('Kirbo', 'stopped adding playlist songs because the requester left voice')
                    break
                try:
                    if len(self.queue) >= 1000:
                        break
                    await self.add(row['query'], member, actor)
                except Exception:
                    self.changed('Kirbo', f"could not queue {row['title']}; skipped it")
            self.changed('Kirbo', 'finished adding playlist songs')
        self.background(fill())

    async def close(self):
        await self.control('stop', 'Kirbo')
        if self.tasks:
            await asyncio.gather(*self.tasks, return_exceptions=True)
        if self.worker:
            await self.worker
        self.store.db.close()
