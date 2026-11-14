"""Local aiohttp server. Only Discord OAuth and static assets are public."""
import asyncio
import hashlib
import html
import io
import ipaddress
import json
import logging
import os
import re
import secrets
import time
from pathlib import Path
from collections import OrderedDict
from urllib.parse import urlencode

import aiohttp
import discord
from aiohttp import web
from PIL import Image, UnidentifiedImageError
import private
from music_service import spotify_track_id

STATIC = Path(__file__).parent / 'static'
API = 'https://discord.com/api/v10'
COOKIE = 'kirbo_session'
SESSION_AGE = 90 * 86400
MEMBERSHIP_TTL = 30
MAX_COVER_BYTES = 5 * 1024 * 1024
CREATOR_ID = 227126966287859713
LOGIN_WINDOW = 60
LOGIN_ATTEMPTS = 10
MAX_PENDING_LOGINS = 2000


def login_address(request):
    """Only the loopback Cloudflare connector may supply the visitor address."""
    try:
        peer = ipaddress.ip_address(request.remote or '')
    except ValueError:
        return 'unknown'
    peer = getattr(peer, 'ipv4_mapped', None) or peer
    if peer.is_loopback and request.headers.get('CF-Connecting-IP'):
        try:
            peer = ipaddress.ip_address(request.headers['CF-Connecting-IP'])
        except ValueError:
            pass
    peer = getattr(peer, 'ipv4_mapped', None) or peer
    if peer.version == 6 and not peer.is_loopback:
        return str(ipaddress.ip_network((peer, 64), strict=False))
    return str(peer)


def cover_image(data):
    try:
        with Image.open(io.BytesIO(data)) as image:
            if image.format not in {'JPEG', 'PNG', 'WEBP'} or image.width * image.height > 25_000_000:
                raise ValueError('Choose a JPG, PNG or WebP image up to 25 megapixels.')
            image.thumbnail((640, 640))
            output = io.BytesIO()
            image = image.convert('RGBA')
            image.info.clear()
            image.save(output, format='PNG')
            return output.getvalue()
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as error:
        raise ValueError('Choose a valid JPG, PNG or WebP image.') from error


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def remove_cover(path):
    try:
        path.unlink(missing_ok=True)
    except OSError:
        # Windows can keep an old photo open while another browser downloads it.
        logging.warning('Could not remove unused playlist photo %s', path.name)


class Website:
    def __init__(self, controller):
        self.music = controller
        self.db = controller.store.db
        self.origin = os.getenv('WEB_PUBLIC_URL', 'https://kirbo.bot').rstrip('/')
        self.secure = self.origin.startswith('https://')
        if not self.secure and self.origin not in {'http://127.0.0.1:8080', 'http://localhost:8080'}:
            raise ValueError('WEB_PUBLIC_URL must use HTTPS, except on localhost:8080.')
        self.client_id = os.getenv('DISCORD_CLIENT_ID', '')
        self.secret = os.getenv('DISCORD_CLIENT_SECRET', '')
        self.redirect = self.origin + '/auth/callback'
        self.states = {}
        self.login_attempts = OrderedDict()
        self.streams = set()
        self.inflight = set()
        self.session_locks = {}
        self.discord_limits = {}
        self.discord_global_limit = 0
        self.membership_cache = {}
        self.voice_members = {}
        self.creator_avatar_url = '/static/icon.svg'
        self.creator_avatar_expires = 0
        self.creator_avatar_lock = asyncio.Lock()
        self.artwork_tasks = set()
        self.app = web.Application(middlewares=[self.guard], client_max_size=MAX_COVER_BYTES,
                                   handler_args={'auto_decompress': False})
        self.app.add_routes([
            web.get('/', self.index), web.get('/auth/login', self.login),
            web.get('/auth/callback', self.callback), web.get('/api/me', self.me),
            web.get('/api/state', self.state), web.get('/api/events', self.events),
            web.post('/api/logout', self.logout), web.post('/api/action', self.action),
            web.get('/api/playlists/{ident:[a-f0-9]{32}}/cover', self.get_cover),
            web.post('/api/playlists/{ident:[a-f0-9]{32}}/cover', self.upload_cover),
        ])
        self.app.router.add_static('/static/', STATIC, show_index=False)
        self.app.on_startup.append(self.open)
        self.app.on_cleanup.append(self.close)
        self.app.on_shutdown.append(self.shutdown)
        self.app.on_response_prepare.append(self.security_headers)

    async def open(self, app):
        self.http = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=20))
        self.refresh_artwork()

    def refresh_artwork(self):
        task = asyncio.create_task(self.music.enrich_playlists())
        self.artwork_tasks.add(task)
        task.add_done_callback(self.artwork_tasks.discard)

    async def close(self, app):
        for task in tuple(self.artwork_tasks):
            task.cancel()
        if self.artwork_tasks:
            await asyncio.gather(*self.artwork_tasks, return_exceptions=True)
        await self.http.close()

    async def get_cover(self, request):
        playlist = self.music.store.playlist(request.match_info['ident'])
        if not playlist or not re.fullmatch(r'[a-f0-9]{32}\.png', playlist['cover'] or ''):
            raise web.HTTPNotFound(reason='Playlist photo not found.')
        path = self.music.store.covers / playlist['cover']
        if not path.is_file():
            raise web.HTTPNotFound(reason='Playlist photo not found.')
        return web.FileResponse(path)

    async def upload_cover(self, request):
        playlist = self.music.store.playlist(request.match_info['ident'])
        if not playlist:
            raise ValueError('Playlist no longer exists.')
        revision = int(request.query.get('revision', '0'))
        if revision != playlist['revision']:
            raise ValueError('Playlist changed. Refresh and try again.')
        data = await asyncio.to_thread(cover_image, await request.read())
        latest = self.music.store.playlist(playlist['id'])
        if not latest or latest['revision'] != revision:
            raise ValueError('Playlist changed during the upload. Try again.')
        covers = self.music.store.covers
        covers.mkdir(parents=True, exist_ok=True)
        filename = secrets.token_hex(16) + '.png'
        path = covers / filename
        try:
            path.write_bytes(data)
            self.db.execute('UPDATE playlists SET cover=?, revision=revision+1 WHERE id=?', (filename, playlist['id']))
            self.db.commit()
        except Exception:
            self.db.rollback()
            remove_cover(path)
            raise
        if latest['cover']:
            remove_cover(covers / latest['cover'])
        self.music.changed(request['session']['user']['username'], f"changed the photo for {latest['name']}")
        return web.json_response(self.music.snapshot())

    async def shutdown(self, app):
        for task in tuple(self.streams):
            task.cancel()
        if self.streams:
            await asyncio.gather(*self.streams, return_exceptions=True)

    @web.middleware
    async def guard(self, request, handler):
        try:
            if request.headers.get('Content-Encoding', 'identity').lower() != 'identity':
                raise web.HTTPUnsupportedMediaType(reason='Compressed request bodies are not supported.')
            if request.path.startswith('/api/'):
                if request.method != 'GET' and request.headers.get('Origin') != self.origin:
                    raise web.HTTPForbidden(reason='Invalid request origin')
                session = await self.session(request)
                request['session'] = session
                if request.method != 'GET' and not secrets.compare_digest(request.headers.get('X-CSRF-Token', ''), session['csrf']):
                    raise web.HTTPForbidden(reason='Invalid request token')
            response = await handler(request)
        except web.HTTPException as error:
            response = web.json_response({'error': error.reason}, status=error.status) if request.path.startswith('/api/') else web.Response(
                text='<!doctype html><html lang="en"><head><meta name="viewport" content="width=device-width,initial-scale=1"><title>Kirbo · Sign in</title><link rel="stylesheet" href="/static/app.css"></head><body><div class="login-screen"><div class="login-card"><img src="/static/icon.svg" width="72" height="72" alt="Kirbo"><h1>Couldn’t open Kirbo.</h1><p>' + html.escape(error.reason) + '</p><a class="button primary discord-login" href="/">Back to Kirbo</a></div></div></body></html>',
                content_type='text/html', status=error.status)
            if 'Retry-After' in error.headers:
                response.headers['Retry-After'] = error.headers['Retry-After']
        except (ValueError, KeyError, TypeError) as error:
            response = web.json_response({'error': str(error)}, status=409)
        except Exception:
            logging.exception('Website request failed')
            response = web.json_response({'error': 'Request failed. Check the bot console or try again.'}, status=500)
        if request.path == '/api/me' and response.status == 200 and request.cookies.get(COOKIE):
            response.set_cookie(COOKIE, request.cookies[COOKIE], max_age=SESSION_AGE, secure=self.secure, httponly=True, samesite='Lax')
        return response

    async def security_headers(self, request, response):
        # Run before headers are sent, including on the live event stream.
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['Referrer-Policy'] = 'no-referrer'
        response.headers['X-Frame-Options'] = 'DENY'
        response.headers['Content-Security-Policy'] = "default-src 'self'; img-src 'self' https: data:; style-src 'self'; script-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
        response.headers['Cache-Control'] = 'no-store' if not request.path.startswith('/static/') else 'no-cache'
        if self.secure:
            response.headers['Strict-Transport-Security'] = 'max-age=15552000'

    async def discord(self, path, token):
        key = (digest(token), path)
        self.discord_limits = {k: until for k, until in self.discord_limits.items() if until > time.monotonic()}
        for attempt in range(2):
            delay = max(self.discord_limits.get(key, 0), self.discord_global_limit) - time.monotonic()
            if delay > 2:
                raise web.HTTPServiceUnavailable(reason='Discord rate-limited membership checks. Please try again.',
                                                 headers={'Retry-After': str(max(1, int(delay) + 1))})
            if delay > 0:
                await asyncio.sleep(delay)
            try:
                async with self.http.get(API + path, headers={'Authorization': 'Bearer ' + token}) as response:
                    if response.status == 429:
                        try:
                            data = await response.json(content_type=None)
                        except ValueError:
                            data = {}
                        retry = max(0, float(data.get('retry_after', response.headers.get('Retry-After', 1))))
                        self.discord_limits[key] = time.monotonic() + retry
                        if data.get('global'):
                            self.discord_global_limit = self.discord_limits[key]
                        logging.warning('Discord membership verification HTTP 429; retry_after=%s', retry)
                        if attempt == 0 and retry <= 2:
                            continue
                        raise web.HTTPServiceUnavailable(reason='Discord rate-limited membership checks. Please try again.',
                                                         headers={'Retry-After': str(max(1, int(retry) + 1))})
                    if response.headers.get('X-RateLimit-Remaining') == '0':
                        reset = float(response.headers.get('X-RateLimit-Reset-After', 0))
                        self.discord_limits[key] = time.monotonic() + max(0, reset)
                    if response.status in (401, 403, 404):
                        logging.warning('Discord membership verification HTTP %s', response.status)
                        raise web.HTTPForbidden(reason='Discord access or server membership could not be verified. Sign in again.')
                    if response.status != 200:
                        logging.warning('Discord membership verification HTTP %s', response.status)
                        raise web.HTTPServiceUnavailable(reason=f'Discord membership verification returned HTTP {response.status}. Please try again.')
                    return await response.json()
            except (aiohttp.ClientError, asyncio.TimeoutError) as error:
                logging.warning('Discord membership verification connection failed: %s', type(error).__name__)
                raise web.HTTPServiceUnavailable(reason='Could not connect to Discord to verify membership. Please try again.') from None

    async def token(self, data):
        async with self.http.post(API + '/oauth2/token', data=dict(data, client_id=self.client_id, client_secret=self.secret)) as response:
            if response.status != 200:
                raise web.HTTPUnauthorized(reason='Discord sign-in expired. Please sign in again.')
            result = await response.json()
            result['expires_at'] = time.time() + result['expires_in']
            return result

    async def membership(self, session):
        if session['token']['expires_at'] < time.time() + 60:
            session['token'] = await self.token({'grant_type': 'refresh_token', 'refresh_token': session['token']['refresh_token']})
        if 'user' not in session:
            member = await self.discord(f"/users/@me/guilds/{self.music.config.GUILD_ID}/member", session['token']['access_token'])
            user = member['user']
        else:
            if not self.music.guild:
                raise web.HTTPServiceUnavailable(reason='Kirbo is connecting to Discord.')
            try:
                # discord.py queues requests and honours Discord's bot rate limits.
                member = await self.music.guild.fetch_member(int(session['user']['id']))
            except discord.NotFound:
                raise web.HTTPForbidden(reason='You must be a member of Team Rocket 2.0 to use Kirbo.') from None
            except (discord.HTTPException, aiohttp.ClientError, asyncio.TimeoutError):
                raise web.HTTPServiceUnavailable(reason='Discord verification is temporarily unavailable. Please try again.') from None
            user = dict(id=str(member.id), username=member.name, avatar=member.avatar.key if member.avatar else None)
            self.voice_members[member.id] = (time.monotonic() + MEMBERSHIP_TTL, member)
        if int(user['id']) in private.BLACKLISTED_USERS:
            raise web.HTTPForbidden(reason='Access denied.')
        session['user'] = user
        session['checked'] = time.time()

    async def session(self, request):
        row = self.db.execute('SELECT payload FROM sessions WHERE id=?', (digest(request.cookies.get(COOKIE, '')),)).fetchone()
        if not row:
            raise web.HTTPUnauthorized(reason='Sign in with Discord to continue.')
        user_id = json.loads(row[0])['user']['id']
        lock = self.session_locks.setdefault(user_id, asyncio.Lock())
        async with lock:
            return await self.read_session(request)

    async def read_session(self, request):
        key = digest(request.cookies.get(COOKIE, ''))
        row = self.db.execute('SELECT payload,expires FROM sessions WHERE id=?', (key,)).fetchone()
        if not row or row[1] < time.time():
            raise web.HTTPUnauthorized(reason='Sign in with Discord to continue.')
        session = json.loads(row[0])
        user_id = session['user']['id']
        if int(user_id) in private.BLACKLISTED_USERS:
            raise web.HTTPForbidden(reason='Access denied.')
        cached = self.membership_cache.get(user_id)
        if cached and cached['checked'] > session.get('checked', 0):
            session.update(cached)
        try:
            if session.get('checked', 0) < time.time() - MEMBERSHIP_TTL:
                await self.membership(session)
            # Share only verification results, never OAuth tokens or CSRF tokens.
            self.membership_cache[user_id] = dict(user=session['user'], checked=session['checked'])
        except (web.HTTPForbidden, web.HTTPUnauthorized):
            self.db.execute('DELETE FROM sessions WHERE id=?', (key,))
            self.db.commit()
            raise
        except web.HTTPServiceUnavailable:
            # A successful token refresh must survive a later membership-service failure.
            self.db.execute('UPDATE sessions SET payload=? WHERE id=?', (json.dumps(session), key))
            self.db.commit()
            raise
        self.db.execute('UPDATE sessions SET payload=?, expires=? WHERE id=?', (json.dumps(session), time.time() + SESSION_AGE, key))
        self.db.commit()
        return session

    async def index(self, request):
        return web.FileResponse(STATIC / 'index.html')

    async def login(self, request):
        if not self.client_id or not self.secret:
            raise web.HTTPServiceUnavailable(reason='Discord website sign-in has not been configured. See WEBSITE_SETUP.md.')
        now = time.monotonic()
        while self.login_attempts and now - next(iter(self.login_attempts.values()))[0] >= LOGIN_WINDOW:
            self.login_attempts.popitem(last=False)
        address = login_address(request)
        started, attempts = self.login_attempts.get(address, (now, 0))
        if attempts >= LOGIN_ATTEMPTS:
            raise web.HTTPTooManyRequests(reason='Too many sign-in attempts. Please wait before trying again.',
                headers={'Retry-After': str(max(1, int(started + LOGIN_WINDOW - now) + 1))})
        self.login_attempts[address] = (started, attempts + 1)
        if len(self.login_attempts) > 4096:
            self.login_attempts.popitem(last=False)
        self.states = {key: expiry for key, expiry in self.states.items() if expiry > time.time()}
        state = request.cookies.get('kirbo_oauth', '')
        if not state or digest(state) not in self.states:
            if len(self.states) >= MAX_PENDING_LOGINS:
                self.states.pop(next(iter(self.states)))
            state = secrets.token_urlsafe(32)
        self.states[digest(state)] = time.time() + 600
        response = web.HTTPFound('https://discord.com/oauth2/authorize?' + urlencode(dict(
            client_id=self.client_id, redirect_uri=self.redirect, response_type='code',
            scope='identify guilds.members.read', state=state)))
        response.set_cookie('kirbo_oauth', state, secure=self.secure, httponly=True, samesite='Lax', max_age=600)
        return response

    async def callback(self, request):
        state = request.query.get('state', '')
        if not state or not secrets.compare_digest(state, request.cookies.get('kirbo_oauth', '')) or self.states.pop(digest(state), 0) < time.time():
            raise web.HTTPForbidden(reason='Invalid sign-in state. Start sign-in again.')
        if 'code' not in request.query:
            raise web.HTTPUnauthorized(reason='Discord sign-in was cancelled.')
        token = await self.token(dict(grant_type='authorization_code', code=request.query['code'], redirect_uri=self.redirect))
        session = dict(token=token, csrf=secrets.token_urlsafe(32))
        await self.membership(session)
        value = secrets.token_urlsafe(48)
        self.db.execute('DELETE FROM sessions WHERE expires < ?', (time.time(),))
        self.db.execute('INSERT INTO sessions VALUES(?,?,?)', (digest(value), json.dumps(session), time.time() + SESSION_AGE))
        self.db.commit()
        response = web.HTTPFound('/')
        response.set_cookie(COOKIE, value, max_age=SESSION_AGE, secure=self.secure, httponly=True, samesite='Lax')
        response.del_cookie('kirbo_oauth')
        return response

    async def creator_avatar(self):
        async with self.creator_avatar_lock:
            if self.music.guild and time.monotonic() >= self.creator_avatar_expires:
                self.creator_avatar_expires = time.monotonic() + 60
                try:
                    user = await asyncio.wait_for(self.music.bot.fetch_user(CREATOR_ID), timeout=5)
                except (discord.HTTPException, aiohttp.ClientError, asyncio.TimeoutError):
                    logging.warning('Could not update the creator avatar; keeping the cached image')
                else:
                    self.creator_avatar_url = user.display_avatar.with_size(64).url
                    self.creator_avatar_expires = time.monotonic() + 3600
            return self.creator_avatar_url

    async def me(self, request):
        session = request['session']
        user = session['user']
        avatar = f"https://cdn.discordapp.com/avatars/{user['id']}/{user['avatar']}.png?size=64" if user.get('avatar') else f"https://cdn.discordapp.com/embed/avatars/{(int(user['id']) >> 22) % 6}.png"
        return web.json_response(dict(id=user['id'], username=user['username'], avatar=avatar,
                                      creator_avatar=await self.creator_avatar(), csrf=session['csrf']))

    async def logout(self, request):
        self.db.execute('DELETE FROM sessions WHERE id=?', (digest(request.cookies.get(COOKIE, '')),))
        self.db.commit()
        response = web.json_response({'ok': True})
        response.del_cookie(COOKIE)
        return response

    async def state(self, request):
        return web.json_response(self.music.snapshot())

    async def events(self, request):
        task = asyncio.current_task()
        self.streams.add(task)
        response = web.StreamResponse(headers={'Content-Type': 'text/event-stream', 'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'})
        await response.prepare(request)
        event = asyncio.Event()
        self.music.listeners.add(event)
        try:
            while True:
                event.clear()
                try:
                    await self.session(request)
                except web.HTTPServiceUnavailable as error:
                    await response.write(b'event: verification\ndata: {}\n\n')
                    await asyncio.sleep(min(20, max(1, float(error.headers.get('Retry-After', 5)))))
                    continue
                except (web.HTTPUnauthorized, web.HTTPForbidden):
                    await response.write(b'event: signed-out\ndata: {}\n\n')
                    break
                await response.write(('data: ' + json.dumps(self.music.snapshot()) + '\n\n').encode())
                try:
                    await asyncio.wait_for(event.wait(), timeout=20)
                except asyncio.TimeoutError:
                    await response.write(b': heartbeat\n\n')
        except (ConnectionResetError, asyncio.CancelledError, web.HTTPException):
            pass
        finally:
            self.music.listeners.discard(event)
            self.streams.discard(task)
        return response

    async def action(self, request):
        ident = request['session']['user']['id']
        data = await request.json()
        expensive = data.get('action') in {'add', 'import_spotify', 'queue_playlist', 'playlist_song'}
        if expensive and ident in self.inflight:
            raise web.HTTPTooManyRequests(reason='Your previous action is still running.')
        if expensive:
            self.inflight.add(ident)
        try:
            return await self.perform_action(request)
        finally:
            if expensive:
                self.inflight.discard(ident)

    async def perform_action(self, request):
        session = request['session']
        data = await request.json()
        actor = session['user']['username']
        action = data.get('action')
        music = self.music
        # All bot controls have a server-side membership check, independent of the UI.
        if not music.guild:
            raise web.HTTPServiceUnavailable(reason='Kirbo is connecting to Discord.')
        if action in {'add', 'queue_playlist', 'pause', 'resume', 'skip', 'stop'}:
            user_id = int(session['user']['id'])
            cached = self.voice_members.get(user_id)
            if cached and cached[0] > time.monotonic():
                member = cached[1]
            else:
                member = music.guild.get_member(user_id) or await music.guild.fetch_member(user_id)
                self.voice_members[user_id] = (time.monotonic() + MEMBERSHIP_TTL, member)
        if action == 'add':
            await music.add(data['query'], member, actor, bool(data.get('next')))
        elif action in {'pause', 'resume', 'skip', 'stop', 'shuffle', 'clear'}:
            await music.control(action, actor, member if action in {'pause', 'resume', 'skip', 'stop'} else None)
        elif action == 'remove':
            music.remove(data['id'], actor)
        elif action == 'reorder':
            music.reorder(data['ids'], data['revision'], actor)
        elif action == 'save_playlist':
            playlist = music.store.save_playlist(data.get('id'), data['name'], data['tracks'], data.get('revision'))
            music.changed(actor, f"saved playlist {playlist['name']}")
        elif action == 'playlist_song':
            playlist = music.store.playlist(data['id'])
            if not playlist:
                raise ValueError('Playlist no longer exists.')
            resolved = await music.resolve(data['query'])
            rows = list(playlist['tracks'])
            if data.get('song_id'):
                index = next((i for i, row in enumerate(rows) if row['id'] == data['song_id']), None)
                if index is None:
                    raise ValueError('Song no longer exists in this playlist.')
                resolved['id'] = data['song_id']
                resolved['title'] = data.get('title') or resolved['title']
                rows[index] = resolved
            else:
                rows.append(resolved)
            music.store.save_playlist(playlist['id'], playlist['name'], rows, data['revision'])
            music.changed(actor, f"edited songs in {playlist['name']}")
        elif action == 'delete_playlist':
            playlist = music.store.playlist(data['id'])
            if not playlist:
                raise ValueError('Playlist no longer exists.')
            if data.get('revision') != playlist['revision']:
                raise ValueError('Playlist changed. Refresh and try again.')
            self.db.execute('DELETE FROM playlists WHERE id=?', (playlist['id'],))
            self.db.commit()
            if playlist['cover']:
                remove_cover(music.store.covers / playlist['cover'])
            music.changed(actor, f"deleted playlist {playlist['name']}")
        elif action == 'import_spotify':
            rows = await music.spotify_rows(data['url'])
            if not rows:
                raise ValueError('Spotify returned no playable songs.')
            music.store.save_playlist(None, data['name'], rows)
            self.refresh_artwork()
            music.changed(actor, f"imported playlist {data['name']}")
        elif action == 'queue_playlist' and spotify_track_id(data.get('url')):
            await music.add(data['url'], member, actor)
        elif action == 'queue_playlist':
            if data.get('url'):
                rows = await music.spotify_rows(data['url'])
            else:
                playlist = music.store.playlist(data['id'])
                if not playlist:
                    raise ValueError('Playlist no longer exists.')
                rows = playlist['tracks']
            await music.enqueue_playlist(rows, member, actor, bool(data.get('shuffle')))
            music.changed(actor, 'queued a playlist')
        else:
            raise ValueError('Unknown action.')
        return web.json_response(music.snapshot())


async def start(controller):
    bind = os.getenv('WEB_BIND', '127.0.0.1')
    if bind not in {'127.0.0.1', '::1', 'localhost'}:
        raise ValueError('WEB_BIND must be a loopback address. Publish Kirbo through Cloudflare Tunnel.')
    site = Website(controller)
    runner = web.AppRunner(site.app)
    await runner.setup()
    await web.TCPSite(runner, bind, int(os.getenv('WEB_PORT', '8080'))).start()
    logging.getLogger(__name__).info('Kirbo website listening on %s:%s', bind, os.getenv('WEB_PORT', '8080'))
    return runner
