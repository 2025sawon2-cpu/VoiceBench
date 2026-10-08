"""Real SDK-over-HTTP tests using an in-memory backend, never production GPU."""
import asyncio
import http.client
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
import socket
import threading
import time
import unittest
from unittest.mock import patch

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
import uvicorn

from tools.voicebench_mcp_server import create_app

KEY = 'fixture-only-key'
AUDIO = b'RIFF' + b'\x00' * 4 + b'WAVE' + b'\x01' * 244


class Backend(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def reply(self, value, status=200):
        content = json.dumps(value).encode()
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(content)))
        self.end_headers()
        self.wfile.write(content)

    def do_GET(self):
        if self.headers.get('Authorization') != 'Bearer ' + KEY:
            return self.reply({'error': 'bad backend auth'}, 401)
        if self.path == '/v1/health':
            return self.reply({'status': 'ok', 'ready': True, 'active_requests': 0})
        if self.path == '/v1/voices':
            return self.reply({'voices': [{'id': 'qwen-sohee', 'preview_url': '/v1/voices/qwen-sohee/preview'}]})
        if self.path.endswith('/preview') or self.path.endswith('/audio'):
            ranged = self.headers.get('Range') == 'bytes=0-127'
            data = AUDIO[:128] if ranged else AUDIO
            self.send_response(206 if ranged else 200)
            self.send_header('Content-Type', 'audio/wav')
            self.send_header('Content-Length', str(len(data)))
            self.send_header('Accept-Ranges', 'bytes')
            if ranged:
                self.send_header('Content-Range', f'bytes 0-127/{len(AUDIO)}')
            self.end_headers()
            self.wfile.write(data)
            return
        if self.path == '/v1/tts/1':
            return self.reply({'id': 1, 'status': 'succeeded', 'audio_url': '/v1/tts/1/audio', 'chapters': []})
        if self.path.startswith('/v1/tts?'):
            return self.reply({'jobs': [{'id': 1}], 'next_before_id': None})
        if self.path == '/v1/tts/1/export':
            return self.reply({'request_id': 1, 'audio_url': '/v1/tts/1/audio', 'word_aligned': False})
        self.reply({'error': 'missing'}, 404)

    def do_POST(self):
        value = json.loads(self.rfile.read(int(self.headers.get('Content-Length', '0'))))
        if self.headers.get('Authorization') != 'Bearer ' + KEY:
            return self.reply({'error': 'bad backend auth'}, 401)
        if self.path == '/v1/tts':
            if value['text'] == 'busy':
                return self.reply({'error': 'queue full'}, 429)
            return self.reply({'id': 1, 'status': 'queued', 'voice_id': value.get('voice_id')}, 202)
        if self.path == '/v1/tts/1/cancel':
            return self.reply({'id': 1, 'status': 'cancelled'})
        self.reply({'error': 'missing'}, 404)


class GatewayTests(unittest.TestCase):
    def setUp(self):
        self.backend = ThreadingHTTPServer(('127.0.0.1', 0), Backend)
        self.backend_thread = threading.Thread(target=self.backend.serve_forever, daemon=True)
        self.backend_thread.start()
        backend_url = 'http://127.0.0.1:' + str(self.backend.server_port)
        sock = socket.socket()
        sock.bind(('127.0.0.1', 0))
        self.port = sock.getsockname()[1]
        self.url = 'http://127.0.0.1:' + str(self.port)
        self.env = patch.dict(os.environ, {'VOICEBENCH_URL': backend_url, 'VOICEBENCH_API_KEY': KEY,
                                         'VOICEBENCH_PUBLIC_URL': self.url})
        self.env.start()
        app = create_app(KEY, backend_url, ['127.0.0.1:*'], [self.url])
        self.server = uvicorn.Server(uvicorn.Config(app, log_level='error', access_log=False))
        self.thread = threading.Thread(target=self.server.run, kwargs={'sockets': [sock]}, daemon=True)
        self.thread.start()
        deadline = time.monotonic() + 5
        while not self.server.started and time.monotonic() < deadline:
            time.sleep(.01)
        self.assertTrue(self.server.started)

    def tearDown(self):
        self.server.should_exit = True
        self.thread.join(5)
        self.backend.shutdown()
        self.backend.server_close()
        self.backend_thread.join(5)
        self.env.stop()

    def http(self, path, headers):
        conn = http.client.HTTPConnection('127.0.0.1', self.port, timeout=5)
        conn.request('GET', path, headers=headers)
        response = conn.getresponse()
        result = response.status, dict(response.getheaders()), response.read()
        conn.close()
        return result

    def test_auth_origin_host_and_admin_boundary(self):
        self.assertEqual(self.http('/v1/health', {})[0], 401)
        auth = {'Authorization': 'Bearer ' + KEY}
        self.assertEqual(self.http('/v1/health', auth)[0], 200)
        self.assertEqual(self.http('/v1/health', {**auth, 'Origin': 'https://evil.example'})[0], 403)
        self.assertEqual(self.http('/v1/health', {**auth, 'Host': 'evil.example'})[0], 421)
        self.assertEqual(self.http('/api/studio', auth)[0], 404)
        self.assertEqual(self.http('/v1/not-allowed', auth)[0], 404)

    def test_binary_range_proxy_keeps_exact_bytes(self):
        status, headers, data = self.http('/v1/voices/qwen-sohee/preview',
            {'Authorization': 'Bearer ' + KEY, 'Range': 'bytes=0-127'})
        self.assertEqual(status, 206)
        self.assertEqual(data, AUDIO[:128])
        self.assertEqual(headers['content-range'], f'bytes 0-127/{len(AUDIO)}')
        self.assertEqual(headers['content-length'], '128')

    def test_sdk_handshake_all_tools_errors_and_partial_batch(self):
        async def run():
            async with httpx.AsyncClient(headers={'Authorization': 'Bearer ' + KEY}) as client:
                async with streamable_http_client(self.url + '/mcp', http_client=client) as (read, write, _):
                    async with ClientSession(read, write) as session:
                        await session.initialize()
                        names = {tool.name for tool in (await session.list_tools()).tools}
                        self.assertEqual(len(names), 11)
                        cases = [('list_voices', {}), ('get_voice_preview', {'voice_id': 'qwen-sohee'}),
                            ('generate_speech', {'text': 'hello', 'voice_id': 'qwen-sohee'}),
                            ('get_job', {'request_id': 1}), ('get_audio', {'request_id': 1}),
                            ('get_server_status', {}), ('generate_batch', {'items': [{'text': 'hello'}, {'text': 'busy'}]}),
                            ('compare_voices', {'text': 'hello', 'voice_ids': ['qwen-sohee', 'zonos-americanmale']}),
                            ('cancel_job', {'request_id': 1}), ('list_jobs', {}), ('export_for_editing', {'request_id': 1})]
                        for name, arguments in cases:
                            result = await session.call_tool(name, arguments)
                            self.assertFalse(result.isError, (name, result))
                            value = json.loads(result.content[0].text)
                            if name == 'generate_batch':
                                self.assertEqual(value['accepted_count'], 1)
                                self.assertEqual(value['retry_indices'], [1])
                            if name == 'get_audio':
                                self.assertEqual(value['audio_url'], self.url + '/v1/tts/1/audio')
                        result = await session.call_tool('get_job', {'request_id': -1})
                        self.assertTrue(result.isError)
        asyncio.run(run())


if __name__ == '__main__':
    unittest.main()
