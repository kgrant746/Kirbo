"""Local aiohttp server. Only Discord OAuth and static assets are public."""
import asyncio
import hashlib
import html
import json
import logging
import os
import secrets
import time
from pathlib import Path
from urllib.parse import urlencode

import aiohttp
from aiohttp import web
import private

STATIC = Path(__file__).parent / 'static'
API = 'https://discord.com/api/v10'
COOKIE = 'kirbo_session'
SESSION_AGE = 90 * 86400


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


class Website:
    def __init__(self, controller):
        self.music = controller
        self.db = controller.store.db
        self.origin = os.getenv('WEB_PUBLIC_URL', 'https://bigboicucks.org').rstrip('/')
        self.secure = self.origin.startswith('https://')
        if not self.secure and self.origin not in {'http://127.0.0.1:8080', 'http://localhost:8080'}:
            raise ValueError('WEB_PUBLIC_URL must use HTTPS, except on localhost:8080.')
        self.client_id = os.getenv('DISCORD_CLIENT_ID', '')
        self.secret = os.getenv('DISCORD_CLIENT_SECRET', '')
        self.redirect = self.origin + '/auth/callback'
        self.states = {}
        self.streams = set()
        self.inflight = set()
        self.session_lock = asyncio.Lock()
        self.discord_limits = {}
        self.discord_global_limit = 0
        self.app = web.Application(middlewares=[self.guard], client_max_size=1024 * 1024)
        self.app.add_routes([
            web.get('/', self.index), web.get('/auth/login', self.login),
            web.get('/auth/callback', self.callback), web.get('/api/me', self.me),
            web.get('/api/state', self.state), web.get('/api/events', self.events),
            web.post('/api/logout', self.logout), web.post('/api/action', self.action),
        ])
        self.app.router.add_static('/static/', STATIC, show_index=False)
        self.app.on_startup.append(self.open)
        self.app.on_cleanup.append(self.close)
        self.app.on_shutdown.append(self.shutdown)

    async def open(self, app):
        self.http = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=20))

    async def close(self, app):
        await self.http.close()

    async def shutdown(self, app):
        for task in tuple(self.streams):
            task.cancel()
        if self.streams:
            await asyncio.gather(*self.streams, return_exceptions=True)

    @web.middleware
    async def guard(self, request, handler):
        try:
            if request.path.startswith('/api/'):
                if request.method != 'GET' and request.headers.get('Origin') != self.origin:
                    raise web.HTTPForbidden(reason='Invalid request origin')
                session = await self.session(request, force=request.method != 'GET')
                request['session'] = session
                if request.method != 'GET' and not secrets.compare_digest(request.headers.get('X-CSRF-Token', ''), session['csrf']):
                    raise web.HTTPForbidden(reason='Invalid request token')
            response = await handler(request)
        except web.HTTPException as error:
            response = web.json_response({'error': error.reason}, status=error.status) if request.path.startswith('/api/') else web.Response(
                text='<!doctype html><html lang="en"><head><meta name="viewport" content="width=device-width,initial-scale=1"><title>Kirbo · Sign in</title><link rel="stylesheet" href="/static/app.css"></head><body><div class="login-screen"><div class="login-card"><img src="/static/icon.svg" width="72" height="72" alt="Kirbo"><h1>Not quite there.</h1><p>' + html.escape(error.reason) + '</p><a class="button primary discord-login" href="/">Back to Kirbo</a></div></div></body></html>',
                content_type='text/html', status=error.status)
            if 'Retry-After' in error.headers:
                response.headers['Retry-After'] = error.headers['Retry-After']
        except (ValueError, KeyError, TypeError) as error:
            response = web.json_response({'error': str(error)}, status=409)
        except Exception:
            logging.exception('Website request failed')
            response = web.json_response({'error': 'Request failed. Check the bot console or try again.'}, status=500)
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['Referrer-Policy'] = 'no-referrer'
        response.headers['X-Frame-Options'] = 'DENY'
        response.headers['Content-Security-Policy'] = "default-src 'self'; img-src 'self' https: data:; style-src 'self'; script-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
        response.headers['Cache-Control'] = 'no-store' if not request.path.startswith('/static/') else 'no-cache'
        if request.path == '/api/me' and response.status == 200 and request.cookies.get(COOKIE):
            response.set_cookie(COOKIE, request.cookies[COOKIE], max_age=SESSION_AGE, secure=self.secure, httponly=True, samesite='Lax')
        return response

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
        member = await self.discord(f"/users/@me/guilds/{self.music.config.GUILD_ID}/member", session['token']['access_token'])
        user = member['user']
        if int(user['id']) in private.BLACKLISTED_USERS:
            raise web.HTTPForbidden(reason='Access denied.')
        session['user'] = user
        session['checked'] = time.time()

    async def session(self, request, force=False):
        async with self.session_lock:
            return await self.read_session(request, force)

    async def read_session(self, request, force=False):
        key = digest(request.cookies.get(COOKIE, ''))
        row = self.db.execute('SELECT payload,expires FROM sessions WHERE id=?', (key,)).fetchone()
        if not row or row[1] < time.time():
            raise web.HTTPUnauthorized(reason='Sign in with Discord to continue.')
        session = json.loads(row[0])
        try:
            if force or session.get('checked', 0) < time.time() - 30:
                await self.membership(session)
        except (web.HTTPForbidden, web.HTTPUnauthorized):
            self.db.execute('DELETE FROM sessions WHERE id=?', (key,))
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
        self.states = {key: expiry for key, expiry in self.states.items() if expiry > time.time()}
        if len(self.states) > 2000:
            raise web.HTTPTooManyRequests()
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

    async def me(self, request):
        session = request['session']
        user = session['user']
        avatar = f"https://cdn.discordapp.com/avatars/{user['id']}/{user['avatar']}.png?size=64" if user.get('avatar') else f"https://cdn.discordapp.com/embed/avatars/{(int(user['id']) >> 22) % 6}.png"
        return web.json_response(dict(id=user['id'], username=user['username'], avatar=avatar, csrf=session['csrf']))

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
            checked = time.monotonic()
            while True:
                if time.monotonic() - checked > 20:
                    await self.session(request, force=True)
                    checked = time.monotonic()
                event.clear()
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
            member = await music.guild.fetch_member(int(session['user']['id']))
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
            music.changed(actor, f"deleted playlist {playlist['name']}")
        elif action == 'import_spotify':
            rows = await music.spotify_rows(data['url'])
            if not rows:
                raise ValueError('Spotify returned no playable songs.')
            music.store.save_playlist(None, data['name'], rows)
            music.changed(actor, f"imported playlist {data['name']}")
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
    site = Website(controller)
    runner = web.AppRunner(site.app)
    await runner.setup()
    await web.TCPSite(runner, os.getenv('WEB_BIND', '127.0.0.1'), int(os.getenv('WEB_PORT', '8080'))).start()
    logging.getLogger(__name__).info('Kirbo website listening on 127.0.0.1:%s', os.getenv('WEB_PORT', '8080'))
    return runner
