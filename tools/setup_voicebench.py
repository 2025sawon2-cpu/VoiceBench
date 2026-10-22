"""Windows first-run orchestrator. Installs are resumable; success requires real previews."""
from __future__ import annotations

import argparse
import ctypes
from contextlib import contextmanager
import json
import os
from pathlib import Path
import shutil
import socket
import sqlite3
import subprocess
import sys
import struct

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools.setup_hardware import detect

ENGINES = ('qwen3-tts', 'zonos2', 'voxcpm2', 'fish-speech', 'chatterbox-v3')
FISH_REV = '214da3cd841bda85da2496b96cd3c4d7edb1337e'
CHATTER_REV = '5de7a54aa4e5e2baadb0182dde554908b48b85c2'


def free_build_drive():
    # Query the drive-letter bitmap, never probe disconnected network drives.
    occupied = ctypes.windll.kernel32.GetLogicalDrives()
    if not occupied:
        raise OSError('사용 중인 드라이브 문자를 확인하지 못했습니다.')
    for index in range(25, 2, -1):
        if not occupied & (1 << index):
            return chr(ord('A') + index)
    raise RuntimeError('ZONOS2 빌드에 사용할 빈 드라이브 문자가 없습니다.')


def assert_idle(root):
    # Do not race a live server or take over any process using the worker ports.
    for port in (8765, 8790, 8791):
        with socket.socket() as probe:
            probe.settimeout(.3)
            if probe.connect_ex(('127.0.0.1', port)) == 0:
                raise RuntimeError(f'포트 {port}가 사용 중입니다. 작업이 없는지 확인하고 VoiceBench를 정상 종료한 뒤 다시 설치하세요.')
    path = root / 'data/voicebench.sqlite3'
    if path.exists():
        with sqlite3.connect(path.as_uri() + '?mode=ro', uri=True) as db:
            for table in ('jobs', 'studio_requests'):
                if db.execute(f"SELECT count(*) FROM {table} WHERE status IN ('queued','running')").fetchone()[0]:
                    raise RuntimeError('대기/실행 중인 음성 작업이 있습니다. 완료 또는 정상 취소 후 다시 실행하세요.')


@contextmanager
def install_lock(root):
    import msvcrt
    path = root / '.runtime/setup.lock'
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a+b') as stream:
        if path.stat().st_size == 0:
            stream.write(b'0'); stream.flush()
        stream.seek(0)
        try:
            msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError as error:
            raise RuntimeError('다른 설치가 진행 중입니다. 해당 설치가 끝난 뒤 다시 실행하세요.') from error
        try:
            yield
        finally:
            stream.seek(0)
            msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)


def save_report(root, report):
    path = root / '.runtime/setup-report.json'
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix('.pending.json')
    temporary.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    temporary.replace(path)


def install_reference(root, wav, transcript):
    if bool(wav) != bool(transcript):
        raise ValueError('ReferenceWav와 ReferenceText를 함께 지정하세요.')
    if not wav:
        return
    from voicebench.recordings import validate_voice_clip
    content = Path(wav).read_bytes()
    validate_voice_clip(content)
    text = Path(transcript).read_text(encoding='utf-8-sig').strip()
    if not text:
        raise ValueError('참조 대본이 비어 있습니다.')
    folder = root / 'data/prepared/ref_voice'
    targets = [(folder / 'selected_reference.wav', content),
               (folder / 'selected_reference.txt', text.encode('utf-8'))]
    for path, data in targets:
        if path.exists() and path.read_bytes() != data:
            raise ValueError('기존 개인 목소리를 덮어쓰지 않습니다. 웹 목소리 보관함에 새 목소리로 등록하세요.')
    folder.mkdir(parents=True, exist_ok=True)
    for path, data in targets:
        if not path.exists():
            path.write_bytes(data)


class Installer:
    def __init__(self, root, plan):
        self.root, self.plan = root, plan
        self.env = dict(os.environ, PYTHONUTF8='1', HF_HUB_DISABLE_TELEMETRY='1',
                        HF_HOME=str(root / '.runtime/cache/huggingface'),
                        HF_HUB_DISABLE_SYMLINKS_WARNING='1', VOICEBENCH_ROOT=str(root),
                        CUDA_DEVICE_ORDER='PCI_BUS_ID', CUDA_VISIBLE_DEVICES=str(plan['gpu']['index']),
                        VOICEBENCH_PRESERVE_ENGINES='1',
                        VOICEBENCH_SETUP_GPU=str(plan['gpu']['index']))

    def run(self, *command):
        print('>', ' '.join(map(str, command)), flush=True)
        subprocess.run([str(part) for part in command], cwd=self.root, env=self.env, check=True)

    def script(self, python, name, *args):
        self.run(python, self.root / 'tools' / name, *args)

    def environment(self, engine, torch_version):
        runtime = self.root / '.runtime/engines' / engine
        runtime.mkdir(parents=True, exist_ok=True)
        python = runtime / 'venv/Scripts/python.exe'
        if not python.exists():
            self.run(sys.executable, '-m', 'venv', runtime / 'venv')
        self.run(python, '-m', 'pip', 'install', 'uv==0.12.18')
        overrides = runtime / 'setup-overrides.txt'
        # uv overrides explicitly reconcile upstream Torch pins with Blackwell.
        overrides.write_text(f'torch=={torch_version}+{self.plan["cuda"]}\n'
                             f'torchaudio=={torch_version}+{self.plan["cuda"]}\n'
                             'protobuf>=3.20.0,<6.0.0\n', encoding='utf-8')
        return runtime, python, overrides

    def packages(self, python, overrides, *packages):
        self.run(python, '-m', 'uv', 'pip', 'install', '--python', python,
                 '--override', overrides, '--extra-index-url',
                 'https://download.pytorch.org/whl/' + self.plan['cuda'],
                 '--index-strategy', 'unsafe-best-match', *packages)

    def checkout(self, url, revision, path):
        if not path.exists():
            self.run('git', 'clone', url, path)
            self.run('git', '-C', path, 'checkout', revision)
        head = subprocess.check_output(['git', '-C', str(path), 'rev-parse', 'HEAD'], text=True).strip()
        if head != revision:
            raise RuntimeError('기존 엔진 소스 리비전이 다릅니다. 변경 내용을 보존했으므로 별도 검토가 필요합니다.')

    def install(self, engine):
        version = '2.8.0' if engine in ('zonos2', 'fish-speech') else self.plan['torch']
        runtime, python, overrides = self.environment(engine, version)
        packages = ['torch', 'torchaudio']
        if engine == 'qwen3-tts':
            packages += ['qwen-tts==0.1.1']
        elif engine == 'voxcpm2':
            packages += ['voxcpm==2.0.3', 'transformers==4.57.6', 'numpy==1.26.4', 'gradio==6.17.3']
        elif engine == 'zonos2':
            packages += ['numpy==2.2.6', 'soundfile==0.14.0', 'huggingface-hub==0.36.2', 'transformers==4.57.3']
        elif engine == 'fish-speech':
            self.checkout('https://github.com/fishaudio/fish-speech.git', FISH_REV, runtime / 'source')
            packages += ['-e', str(runtime / 'source'), 'tensorboard==2.18.0', 'googleapis-common-protos==1.70.0']
        else:
            packages += ['git+https://github.com/resemble-ai/chatterbox.git@' + CHATTER_REV]
        self.packages(python, overrides, *packages)
        # A real CUDA kernel detects unsupported wheel architectures and old drivers.
        self.run(python, '-c', "import torch; assert torch.cuda.is_available(), 'CUDA unavailable'; "
                 "x=torch.ones((16,16),device='cuda'); y=x@x; torch.cuda.synchronize(); "
                 "assert y[0,0].item()==16; print(torch.__version__,torch.cuda.get_device_name(0))")
        if engine == 'qwen3-tts':
            self.script(python, 'setup_models.py', engine)
            self.script(sys.executable, 'register_qwen3_tts.py')
            self.script(sys.executable, 'register_voice_gallery.py', '--download')
        elif engine == 'voxcpm2':
            self.script(python, 'download_voxcpm2.py')
            self.script(sys.executable, 'register_voxcpm2.py')
        elif engine == 'chatterbox-v3':
            self.script(python, 'setup_models.py', engine)
            self.script(sys.executable, 'register_chatterbox_v3.py')
        else:
            args = ['--quant', self.plan['zonos_quant'], '--skip-prebuilt'] if engine == 'zonos2' else []
            self.script(python, 'download_fish_zonos.py', engine, *args)
            if engine == 'zonos2':
                self.run('powershell.exe', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File',
                         self.root / 'build-zonos2-cuda.ps1', '-Architecture', self.plan['architecture'],
                         '-BuildDrive', free_build_drive(), '-Jobs', str(max(1, min(4, int(self.plan['ram_gb'] // 8)))))
            self.script(sys.executable, 'register_fish_zonos.py', engine,
                        *(['--quant', self.plan['zonos_quant']] if engine == 'zonos2' else []))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--plan', action='store_true', help='PC 진단만 출력. 다운로드/설정 변경 없음')
    parser.add_argument('--gpu-index', type=int)
    parser.add_argument('--no-browser', action='store_true')
    parser.add_argument('--reference-wav')
    parser.add_argument('--reference-text')
    args = parser.parse_args(argv)
    if struct.calcsize('P') != 8:
        raise RuntimeError('64비트 Python이 필요합니다. 32비트 환경으로는 CUDA 모델을 설치할 수 없습니다.')
    os.chdir(ROOT)
    plan = detect(ROOT, args.gpu_index)
    print(json.dumps(plan, ensure_ascii=False, indent=2), flush=True)
    if args.plan:
        return 0
    if not shutil.which('git'):
        raise RuntimeError('Git for Windows 설치 후 다시 실행하세요.')
    with install_lock(ROOT):
        assert_idle(ROOT)
        # A fresh full install includes multiple CUDA environments and native build tools.
        existing = (ROOT / '.runtime/setup-report.json').exists()
        if plan['free_gb'] < (10 if existing else 80):
            raise RuntimeError('첫 전체 설치에는 여유 공간 80GB, 재시도에는 최소 10GB를 확보하세요.')
        install_reference(ROOT, args.reference_wav, args.reference_text)
        fresh = not (ROOT / 'data/voicebench.sqlite3').exists()
        report = dict(status='running', plan=plan, steps=[], samples=[], url='http://127.0.0.1:8765/welcome.html')
        save_report(ROOT, report)
        installer = Installer(ROOT, plan)

        def step(name, action):
            item = dict(name=name, status='running')
            report['steps'].append(item); save_report(ROOT, report)
            print(f'\n[{name}] 시작', flush=True)
            try:
                assert_idle(ROOT)
                action()
                item['status'] = 'ready'
            except Exception as error:
                item.update(status='failed', error=type(error).__name__)
                print(f'[{name}] 실패: {error}', flush=True)
            save_report(ROOT, report)

        for engine in ENGINES:
            step(engine, lambda engine=engine: installer.install(engine))
        step('regression-tests', lambda: installer.run(sys.executable, '-m', 'unittest', 'discover', '-s', 'tests', '-v'))
        step('korean-previews', lambda: installer.script(sys.executable, 'prepare_voice_gallery.py', '--strict'))
        preview_report = ROOT / 'data/voice-gallery/preparation-report.json'
        if preview_report.exists():
            report['samples'] = json.loads(preview_report.read_text(encoding='utf-8'))
        success = all(item['status'] == 'ready' for item in report['steps'])
        report['status'] = 'ready' if success else 'incomplete'
        settings = ROOT / '.runtime/studio-settings.json'
        if fresh and not settings.exists():
            # Keep personal cloning semantics; the welcome page selects official voices explicitly.
            slug = 'zonos2-q8-experimental' if plan['zonos_quant'] == 'q8_0' else 'zonos2-q4-experimental'
            settings.write_text(json.dumps({'default_engine_slug': slug}), encoding='utf-8')
        save_report(ROOT, report)
        # Release installer workers before starting the HTTP server.
        if not args.no_browser:
            try:
                installer.script(sys.executable, 'start_studio.py', '--welcome')
            except Exception as error:
                success = False
                report['status'] = 'incomplete'
                report['steps'].append(dict(name='open-studio', status='failed', error=type(error).__name__))
                save_report(ROOT, report)
                print('웹 시작 실패. .runtime/studio-launch.log를 확인하세요.', flush=True)
        print('\n' + ('설치 및 한국어 샘플 검증 완료' if success else '일부 준비 실패 — 시작 화면에서 결과를 확인하고 같은 설치 명령으로 재시도하세요.'))
        print(report['url'], flush=True)
        return 0 if success else 1


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (ValueError, RuntimeError, OSError) as error:
        print(f'설치 중단: {error}', file=sys.stderr)
        raise SystemExit(1)
