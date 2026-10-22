"""Open the local standalone Studio, reusing an existing VoiceBench server."""
import json
import argparse
import os
from pathlib import Path
import subprocess
import sys
import time
from urllib.request import urlopen
import webbrowser

ROOT = Path(__file__).resolve().parents[1]
URL = 'http://127.0.0.1:8765'

def ready():
    try:
        with urlopen(URL + '/api/studio', timeout=2) as response:
            return 'studio_engines' in json.load(response)
    except Exception:
        return False

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--welcome', action='store_true')
    args = parser.parse_args()
    target = URL + ('/welcome.html' if args.welcome else '')
    if not ready():
        # Never replace a server already bound to this port, including protected ones.
        import socket
        with socket.socket() as probe:
            occupied = probe.connect_ex(('127.0.0.1', 8765)) == 0
        if occupied:
            if args.welcome:
                raise SystemExit('Port 8765 is occupied by an unverified or protected server. Start Studio separately after checking it.')
            webbrowser.open(target)
            return
        runtime = ROOT / '.runtime'
        runtime.mkdir(exist_ok=True)
        environment = os.environ.copy()
        key = runtime / 'external-api-key.txt'
        if key.exists():
            environment['VOICEBENCH_EXTERNAL_API_KEY'] = key.read_text(encoding='utf-8').strip()
        with (runtime / 'studio-launch.log').open('ab') as log:
            process = subprocess.Popen([sys.executable, '-m', 'voicebench', '--host', '127.0.0.1', '--port', '8765'],
                cwd=ROOT, env=environment, stdout=log, stderr=log,
                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        for _ in range(30):
            if ready():
                break
            if process.poll() is not None:
                raise SystemExit('VoiceBench did not start. Check .runtime/studio-launch.log.')
            time.sleep(0.5)
        else:
            raise SystemExit('Startup timed out. Check .runtime/studio-launch.log.')
    if not webbrowser.open(target):
        raise SystemExit('Could not open the default browser. Open ' + target)
    print(target, flush=True)

if __name__ == '__main__':
    main()
