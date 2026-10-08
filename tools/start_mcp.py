"""Start/reuse the isolated LAN MCP gateway without changing Studio defaults."""
import argparse
import ipaddress
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--public-url')
    parser.add_argument('--port', type=int, default=8766)
    parser.add_argument('--host', default='0.0.0.0')
    args = parser.parse_args()
    runtime = ROOT / '.runtime'
    key_path = runtime / 'external-api-key.txt'
    key = os.environ.get('VOICEBENCH_EXTERNAL_API_KEY', '').strip()
    if not key and key_path.exists():
        key = key_path.read_text(encoding='utf-8').strip()
    if not key:
        raise SystemExit('Missing external API key. Configure VoiceBench first.')
    def ready(url):
        try:
            request = Request(url + '/v1/health', headers={'Authorization': 'Bearer ' + key})
            with urlopen(request, timeout=3) as response:
                return json.load(response).get('status') == 'ok'
        except Exception:
            return False
    if not ready('http://127.0.0.1:8765'):
        raise SystemExit('Start VoiceBench Studio first (VoiceBench.cmd); no service was restarted.')
    addresses = sorted({info[4][0] for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET)
                        if ipaddress.ip_address(info[4][0]).is_private and not info[4][0].startswith('127.')},
                       key=lambda address: (not address.startswith('192.168.'), address))
    if not args.public_url and not addresses:
        raise SystemExit('Cannot detect LAN IPv4. Supply --public-url http://LAN-IP:8766.')
    public = args.public_url or f'http://{addresses[0]}:{args.port}'
    local = f'http://127.0.0.1:{args.port}'
    if ready(local):
        session = runtime / 'mcp-session.json'
        if session.exists():
            public = json.loads(session.read_text(encoding='utf-8'))['public_url']
        print('VoiceBench LAN gateway already running.')
        print('MCP: ' + public + '/mcp')
        print('API: ' + public + '/v1')
        return
    with socket.socket() as probe:
        if probe.connect_ex(('127.0.0.1', args.port)) == 0:
            raise SystemExit('Gateway port is occupied; existing process preserved.')
    python = runtime / 'mcp/venv/Scripts/python.exe'
    if not python.exists():
        raise SystemExit('Run setup-mcp.ps1 first to install the isolated MCP SDK.')
    environment = os.environ.copy()
    environment['VOICEBENCH_EXTERNAL_API_KEY'] = key
    command = [str(python), str(ROOT / 'tools/voicebench_mcp_server.py'), '--host', args.host,
               '--port', str(args.port), '--public-url', public]
    for address in addresses:
        command += ['--allowed-host', address]
    with (runtime / 'mcp-launch.log').open('ab') as log:
        process = subprocess.Popen(command, cwd=ROOT, env=environment, stdout=log, stderr=log,
                                   creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    for _ in range(40):
        if ready(local):
            break
        if process.poll() is not None:
            raise SystemExit('MCP launch failed. Inspect .runtime/mcp-launch.log.')
        time.sleep(.5)
    else:
        raise SystemExit('MCP startup timed out. Inspect .runtime/mcp-launch.log.')
    (runtime / 'mcp-session.json').write_text(json.dumps({
        'pid': process.pid, 'public_url': public, 'port': args.port, 'host': args.host,
        'command_path': str(ROOT / 'tools/voicebench_mcp_server.py')}, indent=2), encoding='utf-8')
    print('MCP: ' + public + '/mcp')
    print('API: ' + public + '/v1')
    print('Use the existing external API key as Bearer authentication.')


if __name__ == '__main__':
    main()
