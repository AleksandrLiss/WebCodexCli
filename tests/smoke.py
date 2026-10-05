"""Run inside the container: python /tmp/smoke.py. Does not call the model."""
import asyncio
import json
import os
import uuid

from aiohttp import ClientSession, CookieJar, WSServerHandshakeError

BASE = 'http://127.0.0.1:8080'


async def until(ws, needle):
    result = ''
    async with asyncio.timeout(12):
        while needle not in result:
            message = await ws.receive_json()
            result += message.get('data', '')
    return result


async def main():
    async with ClientSession(cookie_jar=CookieJar(unsafe=True), headers={'Origin': BASE}) as client:
        response = await client.get(BASE + '/healthz')
        assert response.status == 200
        for asset in ['/', '/static/app.js', '/static/style.css', '/static/vendor/xterm.js', '/static/vendor/xterm.css', '/static/vendor/addon-fit.js']:
            response = await client.get(BASE + asset)
            assert response.status == 200, asset
            assert len(await response.read()) > 100, asset
        assert (await client.get(BASE + '/api/sessions')).status == 401
        assert (await client.post(BASE + '/api/login', json={'password': 'wrong'})).status == 401
        assert (await client.post(BASE + '/api/login', json={'password': os.environ['WEB_PASSWORD']}, headers={'Origin': 'https://evil.example'})).status == 403
        response = await client.post(BASE + '/api/login', json={'password': os.environ['WEB_PASSWORD']})
        assert response.status == 200
        assert response.cookies['codex_web']['httponly']
        assert response.cookies['codex_web']['samesite'] == 'Strict'
        assert (await client.post(BASE + '/api/sessions', json={'kind': 'shell', 'cwd': '/etc'})).status == 400
        assert (await client.post(BASE + '/api/sessions', json={'kind': 'invalid'})).status == 400
        response = await client.post(BASE + '/api/sessions', json={'kind': 'shell', 'cols': 91, 'rows': 27})
        assert response.status == 201
        session = await response.json()
        path = BASE + '/api/sessions/' + session['id']
        try:
            try:
                await client.ws_connect(path + '/ws', headers={'Origin': 'https://evil.example'})
                raise AssertionError('Cross-origin WebSocket accepted')
            except WSServerHandshakeError as error:
                assert error.status == 403
            async with client.ws_connect(path + '/ws') as ws:
                await until(ws, '▸')
                await ws.send_json({'type': 'input', 'data': 'stty -echo\r'})
                await until(ws, '▸')
                marker = uuid.uuid4().hex
                await ws.send_json({'type': 'input', 'data': f"printf '{marker}'; pwd; test -z \"$WEB_PASSWORD\" && printf 'ENV_CLEAN\\n'\r"})
                output = await until(ws, 'ENV_CLEAN')
                assert marker in output and '/workspace' in output
                await ws.send_json({'type': 'resize', 'cols': 110, 'rows': 35})
                await ws.send_json({'type': 'input', 'data': 'stty size\r'})
                await until(ws, '35 110')
                await ws.send_json({'type': 'input', 'data': 'sleep 30\r'})
                await asyncio.sleep(.3)
                await ws.send_json({'type': 'input', 'data': '\x03'})
                await ws.send_json({'type': 'input', 'data': "printf 'INTERRUPT_OK\\n'\r"})
                await until(ws, 'INTERRUPT_OK')
            async with client.ws_connect(path + '/ws') as ws:
                output = await until(ws, 'INTERRUPT_OK')
                assert marker in output, 'Reconnect must replay the terminal buffer'
                assert (await client.post(BASE + '/api/logout', json={})).status == 200
                async with asyncio.timeout(5):
                    while not ws.closed:
                        await ws.receive()
                assert ws.close_code == 4001
            assert (await client.get(BASE + '/api/sessions')).status == 401
        finally:
            await client.post(BASE + '/api/login', json={'password': os.environ['WEB_PASSWORD']})
            assert (await client.delete(path)).status == 200
            assert not any(s['id'] == session['id'] for s in await (await client.get(BASE + '/api/sessions')).json())
    print('PASS: authentication, CSRF, WebSocket origin, PTY I/O, resize, interrupt, reconnect, logout, cleanup')


asyncio.run(main())
