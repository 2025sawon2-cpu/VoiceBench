"""LAN Streamable HTTP MCP + authenticated external API gateway.

Install requirements-mcp.txt in a separate environment. The GPU/web service
stays on loopback; this gateway exposes only /mcp and approved /v1 routes.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import secrets
import sys
from urllib.parse import urlparse

import anyio
import httpx
import uvicorn
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import ToolAnnotations
from starlette.responses import JSONResponse, StreamingResponse
from starlette.routing import Route

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools import voicebench_mcp as bridge


READ = ToolAnnotations(readOnlyHint=True, destructiveHint=False, openWorldHint=False)
WRITE = ToolAnnotations(readOnlyHint=False, destructiveHint=False, openWorldHint=False)
CANCEL = ToolAnnotations(readOnlyHint=False, destructiveHint=True, idempotentHint=True, openWorldHint=False)


def create_mcp(allowed_hosts: list[str], allowed_origins: list[str]) -> FastMCP:
    mcp = FastMCP('VoiceBench LAN', instructions=(
        'Korean TTS with a shared serial GPU queue. Omit voice_id for the personal default. '
        'Keep returned job IDs and poll get_job; do not resubmit to wait. '
        'All result/preview URLs require the same Bearer key. Batch requests may partially succeed. '
        'Seed 13 and HQ are server-controlled.'),
        stateless_http=True, json_response=True,
        transport_security=TransportSecuritySettings(
            enable_dns_rebinding_protection=True, allowed_hosts=allowed_hosts, allowed_origins=allowed_origins))

    async def call(name, arguments=None):
        result = await anyio.to_thread.run_sync(bridge.handle_tool, name, arguments or {})
        return json.loads(result['content'][0]['text'])

    @mcp.tool(annotations=READ)
    async def list_voices() -> dict:
        """List installed Qwen/ZONOS2 voices and Korean preview URLs; omission selects the personal default."""
        return await call('list_voices')

    @mcp.tool(annotations=READ)
    async def get_voice_preview(voice_id: str) -> dict:
        """Get a saved voice preview URL without generating new speech."""
        return await call('get_voice_preview', {'voice_id': voice_id})

    @mcp.tool(annotations=WRITE)
    async def generate_speech(text: str, voice_id: str | None = None, generation_key: str = '') -> dict:
        """Submit speech asynchronously. Save the returned id and poll get_job; never resubmit to wait."""
        return await call('generate_speech', dict(text=text, voice_id=voice_id, generation_key=generation_key))

    @mcp.tool(annotations=READ)
    async def get_job(request_id: int) -> dict:
        """Read existing job progress, chapter availability and speech quality checks."""
        return await call('get_job', {'request_id': request_id})

    @mcp.tool(annotations=READ)
    async def get_audio(request_id: int, chapter: int | None = None) -> dict:
        """Get full/chapter WAV URL and duration. Download it on the client using Bearer authentication."""
        arguments = {'request_id': request_id}
        if chapter is not None:
            arguments['chapter'] = chapter
        return await call('get_audio', arguments)

    @mcp.tool(annotations=READ)
    async def get_server_status() -> dict:
        """Check generation readiness and active external queue usage."""
        return await call('get_server_status')

    @mcp.tool(annotations=WRITE)
    async def generate_batch(items: list[dict]) -> dict:
        """Submit 1–20 text/voice_id/generation_key items. Returns per-item jobs/errors; retry only indices without IDs."""
        return await call('generate_batch', {'items': items})

    @mcp.tool(annotations=WRITE)
    async def compare_voices(text: str, voice_ids: list[str], generation_key: str = '') -> dict:
        """Submit the same text with 2–4 distinct official voices; inspect each returned job ID."""
        return await call('compare_voices', dict(text=text, voice_ids=voice_ids, generation_key=generation_key))

    @mcp.tool(annotations=CANCEL)
    async def cancel_job(request_id: int) -> dict:
        """Cancel an external job. In-flight GPU computation may finish before cancellation takes effect."""
        return await call('cancel_job', {'request_id': request_id})

    @mcp.tool(annotations=READ)
    async def list_jobs(limit: int = 20, before_id: int = 0, status: str = '') -> dict:
        """List shared external history, excluding Studio jobs. next_before_id is the pagination cursor."""
        return await call('list_jobs', dict(limit=limit, before_id=before_id, status=status))

    @mcp.tool(annotations=READ)
    async def export_for_editing(request_id: int) -> dict:
        """Get original script, spoken segments, WAV/chapter URLs, audio format, and whole-script SRT; not word-aligned."""
        return await call('export_for_editing', {'request_id': request_id})

    return mcp


class BearerAuth:
    """Shared LAN key only; per-PC permissions are intentionally deferred."""
    def __init__(self, app, key: str):
        self.app = app
        self.key = key

    async def __call__(self, scope, receive, send):
        if scope['type'] == 'http':
            headers = dict(scope.get('headers', []))
            supplied = headers.get(b'authorization', b'').decode('latin1')
            if not secrets.compare_digest(supplied, 'Bearer ' + self.key):
                response = JSONResponse({'error': 'A valid Bearer API key is required.'}, status_code=401,
                                        headers={'WWW-Authenticate': 'Bearer realm="VoiceBench LAN"'})
                return await response(scope, receive, send)
        await self.app(scope, receive, send)


GET_ROUTES = re.compile(r'/v1/(?:health|voices|voices/[a-z0-9_-]+/preview|tts|tts/\d+(?:/audio|/export|/chapters/\d+/audio)?)')
POST_ROUTES = re.compile(r'/v1/(?:tts|tts/\d+/cancel|resources/release-idle)')


def create_app(key: str, backend_url: str, allowed_hosts: list[str], allowed_origins: list[str]):
    mcp = create_mcp(allowed_hosts, allowed_origins)
    app = mcp.streamable_http_app()

    async def proxy(request):
        path = request.url.path
        pattern = GET_ROUTES if request.method in {'GET', 'HEAD'} else POST_ROUTES
        if not pattern.fullmatch(path):
            return JSONResponse({'error': 'Not found'}, status_code=404)
        # Do not expose Studio, arbitrary URLs, paths or administrator endpoints.
        body = bytearray()
        async for chunk in request.stream():
            body.extend(chunk)
            if len(body) > 2 * 1024 * 1024:
                return JSONResponse({'error': 'Request body exceeds 2 MB'}, status_code=413)
        headers = {'Authorization': 'Bearer ' + key, 'Content-Type': 'application/json'}
        if request.headers.get('range'):
            headers['Range'] = request.headers['range']
        client = httpx.AsyncClient(timeout=httpx.Timeout(120, connect=10), trust_env=False)
        try:
            upstream_request = client.build_request(request.method,
                backend_url.rstrip('/') + path + ('?' + request.url.query if request.url.query else ''),
                headers=headers, content=bytes(body))
            upstream = await client.send(upstream_request, stream=True)
        except httpx.HTTPError:
            await client.aclose()
            return JSONResponse({'error': 'VoiceBench backend is unavailable'}, status_code=502)
        forwarded = {name: value for name, value in upstream.headers.items()
                     if name.lower() in {'content-type', 'content-length', 'content-range', 'accept-ranges', 'cache-control'}}
        from starlette.background import BackgroundTask
        async def close():
            await upstream.aclose()
            await client.aclose()
        return StreamingResponse(upstream.aiter_raw(), status_code=upstream.status_code,
                                 headers=forwarded, background=BackgroundTask(close))

    app.routes.append(Route('/v1/{path:path}', proxy, methods=['GET', 'HEAD', 'POST']))
    # SDK validates MCP Host/Origin; protect the API gateway using the same guard.
    from mcp.server.transport_security import TransportSecurityMiddleware
    security = TransportSecurityMiddleware(TransportSecuritySettings(
        enable_dns_rebinding_protection=True, allowed_hosts=allowed_hosts, allowed_origins=allowed_origins))

    async def guarded(scope, receive, send):
        if scope['type'] == 'http' and scope['path'].startswith('/v1/'):
            from starlette.requests import Request
            rejection = await security.validate_request(Request(scope, receive), is_post=scope['method'] == 'POST')
            if rejection:
                return await rejection(scope, receive, send)
        await app(scope, receive, send)
    return BearerAuth(guarded, key)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--port', type=int, default=8766)
    parser.add_argument('--backend-url', default='http://127.0.0.1:8765')
    parser.add_argument('--public-url', required=True, help='LAN URL used in returned download links')
    parser.add_argument('--allowed-host', action='append', default=[])
    args = parser.parse_args()
    key = os.environ.get('VOICEBENCH_EXTERNAL_API_KEY', '').strip()
    if not key:
        raise SystemExit('VOICEBENCH_EXTERNAL_API_KEY is required; refusing to expose an unauthenticated gateway.')
    os.environ['VOICEBENCH_URL'] = args.backend_url.rstrip('/')
    os.environ['VOICEBENCH_API_KEY'] = key
    os.environ['VOICEBENCH_PUBLIC_URL'] = args.public_url.rstrip('/')
    public = urlparse(args.public_url)
    if public.scheme not in {'http', 'https'} or not public.hostname or public.username or public.password:
        raise SystemExit('public-url must be an HTTP(S) URL without credentials.')
    hosts = ['127.0.0.1:*', 'localhost:*', public.hostname + ':*'] + [host + ':*' for host in args.allowed_host]
    origins = [args.public_url.rstrip('/'), f'http://127.0.0.1:{args.port}', f'http://localhost:{args.port}']
    uvicorn.run(create_app(key, args.backend_url, hosts, origins), host=args.host, port=args.port,
                access_log=False, log_level='info')


if __name__ == '__main__':
    main()
