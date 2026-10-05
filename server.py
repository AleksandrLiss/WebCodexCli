import asyncio
import codecs
import fcntl
import hmac
import json
import os
from pathlib import Path
import pty
import secrets
import signal
import struct
import termios
import time
from urllib.parse import urlsplit

from aiohttp import web, WSMsgType

ROOT = Path(__file__).parent
WORKSPACE = Path(os.getenv('WORKSPACE', '/workspace')).resolve()
PASSWORD = os.environ.get('WEB_PASSWORD', '')
MAX_SESSIONS = 2
sessions = {}
logins = {}
attempts = {}


def authorized(request):
    token = request.cookies.get('codex_web', '')
    return token if logins.get(token, 0) > time.time() else None


def same_origin(request):
    origin = request.headers.get('Origin')
    return origin and urlsplit(origin).netloc == request.host and urlsplit(origin).scheme in ('http', 'https')


@web.middleware
async def security(request, handler):
    if request.path.startswith('/api/'):
        if request.method not in ('GET', 'HEAD') and not same_origin(request):
            raise web.HTTPForbidden(text='Недопустимый источник запроса')
        if request.path != '/api/login' and not authorized(request):
            raise web.HTTPUnauthorized()
    try:
        response = await handler(request)
    except web.HTTPException as exc:
        response = exc
    response.headers.update({
        'X-Content-Type-Options': 'nosniff',
        'X-Frame-Options': 'DENY',
        'Referrer-Policy': 'no-referrer',
        'Cache-Control': 'no-store',
        'Content-Security-Policy': "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; connect-src 'self'; img-src 'self' data:; frame-ancestors 'none'; base-uri 'none'; form-action 'self'",
    })
    return response


async def body(request):
    try:
        value = await request.json()
        if not isinstance(value, dict):
            raise ValueError()
        return value
    except (ValueError, UnicodeDecodeError):
        raise web.HTTPBadRequest(text='Ожидается JSON-объект')


async def login(request):
    now = time.time()
    # Bounded bookkeeping, even when exposed to many source addresses.
    for key in list(attempts):
        if now - attempts[key][0] > 300:
            del attempts[key]
    for key in list(logins):
        if logins[key] <= now:
            del logins[key]
    address = request.remote
    start, count = attempts.get(address, (now, 0))
    if count >= 10 or len(attempts) > 10000:
        raise web.HTTPTooManyRequests(text='Слишком много попыток. Подождите 5 минут.')
    attempts[address] = (start, count + 1)
    data = await body(request)
    supplied = data.get('password', '')
    if not isinstance(supplied, str) or not hmac.compare_digest(supplied.encode(), PASSWORD.encode()):
        raise web.HTTPUnauthorized(text='Неверный пароль')
    attempts.pop(address, None)
    token = secrets.token_urlsafe(32)
    logins[token] = now + 43200
    response = web.json_response({'ok': True})
    response.set_cookie('codex_web', token, httponly=True, samesite='Strict', secure=request.secure, max_age=43200)
    return response


async def logout(request):
    logins.pop(authorized(request), None)
    response = web.json_response({'ok': True})
    response.del_cookie('codex_web')
    return response


class Session:
    def __init__(self, kind, cwd, cols, rows):
        self.id = secrets.token_hex(12)
        self.kind = kind
        self.cwd = str(cwd)
        self.created = time.time()
        self.exited = False
        self.exit_code = None
        self.buffer = ''
        self.clients = set()
        self.decoder = codecs.getincrementaldecoder('utf-8')('replace')
        commands = {
            'codex': ['codex', '--no-alt-screen', '--no-daemon', '-s', 'workspace-write', '-a', 'on-request'],
            'resume': ['codex', '--no-alt-screen', '--no-daemon', '-s', 'workspace-write', '-a', 'on-request', 'resume'],
            'shell': ['/bin/bash', '--noprofile', '--norc', '-i'],
            'login': ['codex', 'login', '--device-auth'],
        }
        env = dict(os.environ, TERM='xterm-256color', COLORTERM='truecolor', LANG='C.UTF-8', PS1='\\[\\e[38;5;115m\\]\\w ▸ \\[\\e[0m\\]')
        env.pop('WEB_PASSWORD', None)
        self.pid, self.fd = pty.fork()
        if self.pid == 0:
            try:
                os.chdir(cwd)
                os.execvpe(commands[kind][0], commands[kind], env)
            except Exception as exc:
                os.write(2, f'Ошибка запуска: {exc}\r\n'.encode())
                os._exit(127)
        os.set_blocking(self.fd, False)
        self.resize(cols, rows)
        asyncio.get_running_loop().add_reader(self.fd, self.read)
        self.monitor = asyncio.create_task(self.reap())

    def info(self):
        return dict(id=self.id, kind=self.kind, cwd=self.cwd, created=self.created, exited=self.exited, exitCode=self.exit_code)

    def emit(self, event):
        for queue in list(self.clients):
            if queue.full():
                # Disconnect slow clients; a reconnect replays the bounded buffer.
                while not queue.empty():
                    queue.get_nowait()
                queue.put_nowait({'type': 'overflow'})
                self.clients.discard(queue)
            else:
                queue.put_nowait(event)

    def read(self):
        try:
            raw = os.read(self.fd, 65536)
            if not raw:
                asyncio.get_running_loop().remove_reader(self.fd)
                return
            text = self.decoder.decode(raw)
            self.buffer = (self.buffer + text)[-1048576:]
            self.emit({'type': 'output', 'data': text})
        except BlockingIOError:
            pass
        except OSError:
            asyncio.get_running_loop().remove_reader(self.fd)

    async def reap(self):
        while True:
            pid, status = os.waitpid(self.pid, os.WNOHANG)
            if pid:
                self.read()
                self.exited = True
                self.exit_code = os.waitstatus_to_exitcode(status)
                asyncio.get_running_loop().remove_reader(self.fd)
                os.close(self.fd)
                self.emit({'type': 'exit', 'code': self.exit_code})
                return
            await asyncio.sleep(.2)

    def resize(self, cols, rows):
        if not self.exited:
            fcntl.ioctl(self.fd, termios.TIOCSWINSZ, struct.pack('HHHH', rows, cols, 0, 0))

    async def write(self, text):
        data = text.encode()
        while data and not self.exited:
            try:
                written = os.write(self.fd, data)
                data = data[written:]
            except BlockingIOError:
                await asyncio.sleep(.01)
            except OSError:
                return

    async def stop(self):
        if not self.exited:
            try:
                os.killpg(self.pid, signal.SIGHUP)
            except ProcessLookupError:
                pass
            try:
                await asyncio.wait_for(asyncio.shield(self.monitor), 3)
            except asyncio.TimeoutError:
                try:
                    os.killpg(self.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                await self.monitor


def dimensions(data):
    try:
        return max(20, min(400, int(data.get('cols', 100)))), max(5, min(150, int(data.get('rows', 30))))
    except (ValueError, TypeError, OverflowError):
        raise web.HTTPBadRequest(text='Некорректный размер терминала')


async def list_sessions(request):
    return web.json_response([s.info() for s in sessions.values()])


async def create_session(request):
    data = await body(request)
    kind = data.get('kind', 'codex')
    if kind not in ('codex', 'shell', 'login', 'resume'):
        raise web.HTTPBadRequest(text='Неизвестный тип сессии')
    if sum(not s.exited for s in sessions.values()) >= MAX_SESSIONS:
        raise web.HTTPConflict(text='Закройте одну из 2 активных сессий')
    value = data.get('cwd', str(WORKSPACE))
    if not isinstance(value, str):
        raise web.HTTPBadRequest(text='Некорректная папка')
    try:
        cwd = Path(value).resolve()
        if not cwd.is_relative_to(WORKSPACE) or not cwd.is_dir():
            raise ValueError()
    except (ValueError, OSError):
        raise web.HTTPBadRequest(text='Выберите существующую папку внутри /workspace')
    cols, rows = dimensions(data)
    for key, item in list(sessions.items()):
        if item.exited and len(sessions) >= 20:
            del sessions[key]
    session = Session(kind, cwd, cols, rows)
    sessions[session.id] = session
    return web.json_response(session.info(), status=201)


def get_session(request):
    session = sessions.get(request.match_info['id'])
    if not session:
        raise web.HTTPNotFound(text='Сессия не найдена')
    return session


async def delete_session(request):
    session = get_session(request)
    await session.stop()
    sessions.pop(session.id, None)
    return web.json_response({'ok': True})


async def websocket(request):
    if not same_origin(request):
        raise web.HTTPForbidden()
    session = get_session(request)
    token = authorized(request)
    ws = web.WebSocketResponse(heartbeat=20, max_msg_size=65536)
    await ws.prepare(request)
    queue = asyncio.Queue(maxsize=128)
    session.clients.add(queue)
    # Queue replay before yielding so live output cannot overtake it.
    queue.put_nowait({'type': 'output', 'data': session.buffer})
    if session.exited:
        queue.put_nowait({'type': 'exit', 'code': session.exit_code})

    async def send():
        while not ws.closed:
            if logins.get(token, 0) <= time.time():
                await ws.close(code=4001)
                return
            try:
                event = await asyncio.wait_for(queue.get(), 2)
            except asyncio.TimeoutError:
                continue
            if event['type'] == 'overflow':
                await ws.close(code=4002)
                return
            await ws.send_json(event)

    sender = asyncio.create_task(send())
    try:
        async for message in ws:
            if logins.get(token, 0) <= time.time():
                await ws.close(code=4001)
                break
            if message.type != WSMsgType.TEXT:
                continue
            try:
                data = json.loads(message.data)
                if not isinstance(data, dict):
                    raise ValueError()
                if data.get('type') == 'input' and isinstance(data.get('data'), str):
                    await session.write(data['data'])
                elif data.get('type') == 'resize':
                    session.resize(*dimensions(data))
            except (ValueError, OSError, web.HTTPException):
                await ws.close(code=1008)
                break
    finally:
        session.clients.discard(queue)
        sender.cancel()
        await asyncio.gather(sender, return_exceptions=True)
    return ws


async def status(request):
    process = await asyncio.create_subprocess_exec('codex', 'login', 'status', stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    try:
        await asyncio.wait_for(process.communicate(), 5)
    except asyncio.TimeoutError:
        process.kill()
        await process.communicate()
    return web.json_response({'authenticated': process.returncode == 0, 'workspace': str(WORKSPACE), 'version': os.getenv('CODEX_VERSION', '0.160.0')})


async def cleanup(app):
    await asyncio.gather(*(s.stop() for s in sessions.values()))


def make_app():
    app = web.Application(middlewares=[security], client_max_size=65536)
    app.router.add_get('/healthz', lambda r: web.json_response({'ok': True}))
    app.router.add_get('/', lambda r: web.FileResponse(ROOT / 'static/index.html'))
    app.router.add_post('/api/login', login)
    app.router.add_post('/api/logout', logout)
    app.router.add_get('/api/status', status)
    app.router.add_get('/api/sessions', list_sessions)
    app.router.add_post('/api/sessions', create_session)
    app.router.add_delete('/api/sessions/{id}', delete_session)
    app.router.add_get('/api/sessions/{id}/ws', websocket)
    app.router.add_static('/static/', ROOT / 'static', show_index=False)
    app.on_cleanup.append(cleanup)
    return app


if __name__ == '__main__':
    if len(PASSWORD) < 16:
        raise SystemExit('WEB_PASSWORD must contain at least 16 characters')
    WORKSPACE.mkdir(parents=True, exist_ok=True)
    web.run_app(make_app(), host='0.0.0.0', port=8080, access_log=None)
