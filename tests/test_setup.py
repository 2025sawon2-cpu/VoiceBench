import hashlib
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch, MagicMock
from contextlib import nullcontext
import threading
import urllib.request
import urllib.error
import wave

from tools.setup_hardware import make_plan, parse_gpus
from tools.setup_voicebench import assert_idle, install_reference, Installer
from tools.setup_registration import RegistrationService
from tools.prepare_voice_gallery import prepare, REQUIRED_IDS, valid_cached_preview
from voicebench.config import Paths
from voicebench.db import Database
from voicebench.voices import QWEN_CUSTOM, SAMPLE_TEXT
from voicebench.api import VoiceBenchServer


def clip():
    buffer = io.BytesIO()
    with wave.open(buffer, 'wb') as audio:
        audio.setnchannels(1); audio.setsampwidth(2); audio.setframerate(24000)
        audio.writeframes(b'\x00\x01' * 24000 * 4)
    return buffer.getvalue()


class SetupTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory(); self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        with patch.dict(os.environ, VOICEBENCH_ROOT=str(self.root)):
            self.paths = Paths.discover()
        self.paths.ensure(); self.db = Database(self.paths.database)

    def gpu(self, memory=16384, capability=(12, 0), index=0):
        return dict(index=index, name='GPU', memory_mb=memory, capability=list(capability), driver='610.88')

    def test_hardware_uses_capability_and_memory_not_product_name(self):
        gpus = parse_gpus('0, Example Pascal, 8192, 6.1, 610.88\n1, Example New, 16384, 12.0, 610.88\n')
        plan = make_plan(gpus, 100, 32)
        self.assertEqual((plan['gpu']['index'], plan['architecture'], plan['cuda'], plan['torch'], plan['zonos_quant']),
                         (1, '120', 'cu128', '2.8.0', 'q8_0'))
        small = make_plan(gpus, 100, 16, 0)
        self.assertEqual((small['architecture'], small['cuda'], small['zonos_quant']), ('61', 'cu126', 'q4_k'))
        self.assertTrue(small['warnings'])
        with self.assertRaises(ValueError): make_plan([], 100, 32)
        with self.assertRaises(ValueError): make_plan(gpus, 100, 32, 9)
        with self.assertRaises(ValueError): make_plan([self.gpu(capability=(13,0))], 100, 32)

    def test_existing_registration_and_disabled_presets_are_preserved(self):
        service = RegistrationService(self.paths, self.db)
        payload = dict(slug='custom', name='Personal preset', kind='mock', enabled=False,
                       config={'generation': {'temperature': .42}})
        service.upsert_engine(payload)
        with patch.dict(os.environ, VOICEBENCH_PRESERVE_ENGINES='1', VOICEBENCH_SETUP_GPU='2'):
            actual = service.upsert_engine({**payload, 'name':'replacement','enabled':True,'config':{}})
            self.assertEqual(actual['name'], 'Personal preset')
            self.assertFalse(actual['enabled'])
            self.assertEqual(actual['config'], payload['config'])
            new = service.upsert_engine({**payload, 'slug':'new'})
            self.assertEqual(new['config']['environment']['CUDA_VISIBLE_DEVICES'], '2')

    def test_reference_validation_precedes_writes_and_never_overwrites(self):
        wav, transcript = self.root/'input.wav', self.root/'input.txt'
        wav.write_bytes(clip()); transcript.write_text('안녕하세요.', encoding='utf-8')
        install_reference(self.root, wav, transcript)
        target = self.root/'data/prepared/ref_voice/selected_reference.wav'
        original = target.read_bytes()
        install_reference(self.root, wav, transcript)
        transcript.write_text('다른 대본', encoding='utf-8')
        with self.assertRaises(ValueError): install_reference(self.root, wav, transcript)
        self.assertEqual(target.read_bytes(), original)
        with self.assertRaises(ValueError): install_reference(self.root, wav, None)

    def test_live_port_and_queued_request_stop_install_before_mutation(self):
        with patch('tools.setup_voicebench.socket.socket') as sock:
            sock.return_value.__enter__.return_value.connect_ex.return_value = 0
            with self.assertRaisesRegex(RuntimeError, '8765'): assert_idle(self.root)
        with patch('tools.setup_voicebench.socket.socket') as sock:
            sock.return_value.__enter__.return_value.connect_ex.return_value = 1
            assert_idle(self.root)
            # A benchmark job is enough to block, even without a Studio request.
            self.db.execute("INSERT INTO projects(name,created_at,updated_at) VALUES('test','now','now')")
            self.db.execute("INSERT INTO prompts(project_id,position,original_text,normalized_text,created_at) VALUES(1,1,'test','test','now')")
            self.db.execute("INSERT INTO engines(slug,name,kind,created_at,updated_at) VALUES('test','test','mock','now','now')")
            self.db.execute("INSERT INTO jobs(project_id,prompt_id,engine_id,seed,status,created_at) VALUES(1,1,1,13,'queued','now')")
            with self.assertRaisesRegex(RuntimeError, '대기'): assert_idle(self.root)

    def test_installer_passes_detected_quant_architecture_and_gpu(self):
        plan = make_plan([self.gpu(memory=8192, capability=(6,1))], 100, 32)
        installer = Installer(self.root, plan)
        with patch.object(installer, 'run') as run, patch.object(installer, 'packages'):
            installer.install('zonos2')
        commands = [[str(x) for x in c.args] for c in run.call_args_list]
        build = next(c for c in commands if '-Architecture' in c)
        self.assertEqual(build[build.index('-Architecture')+1], '61')
        download = next(c for c in commands if any('download_fish_zonos.py' in x for x in c))
        self.assertIn('q4_k', download); self.assertIn('--skip-prebuilt', download)
        self.assertEqual(installer.env['CUDA_VISIBLE_DEVICES'], '0')

    def test_strict_previews_cannot_succeed_with_an_empty_registry(self):
        with patch('tools.prepare_voice_gallery.assert_idle'), patch('tools.prepare_voice_gallery.WhisperQualityVerifier'):
            self.assertFalse(prepare(self.paths, self.db, strict=True))
        report = json.loads((self.paths.data/'voice-gallery/preparation-report.json').read_text(encoding='utf-8'))
        self.assertEqual(len(report), 22)
        self.assertEqual({r['id'] for r in report}, set(REQUIRED_IDS))
        self.assertTrue(all(r['status']=='unavailable' for r in report))

    def test_orchestrator_continues_after_failure_preserves_settings_and_returns_incomplete(self):
        from tools import setup_voicebench as setup
        settings = self.root/'.runtime/studio-settings.json'
        settings.parent.mkdir(parents=True)
        settings.write_text('{"default_engine_slug":"custom-default"}')
        before = settings.read_bytes()
        plan = make_plan([self.gpu()], 100, 32)
        installed = []
        def install(engine):
            installed.append(engine)
            if engine == 'zonos2': raise RuntimeError('simulated download failure')
        installer = MagicMock(); installer.install.side_effect = install
        previous_cwd = Path.cwd()
        try:
            with patch.object(setup, 'ROOT', self.root), patch.object(setup, 'detect', return_value=plan), \
                 patch.object(setup, 'install_lock', return_value=nullcontext()), patch.object(setup, 'assert_idle'), \
                 patch.object(setup, 'Installer', return_value=installer), patch.object(setup.shutil,'which',return_value='git'):
                self.assertEqual(setup.main(['--no-browser']), 1)
        finally:
            os.chdir(previous_cwd)
        self.assertEqual(installed, list(setup.ENGINES))
        self.assertEqual(settings.read_bytes(), before)
        report = json.loads((self.root/'.runtime/setup-report.json').read_text(encoding='utf-8'))
        self.assertEqual(report['status'], 'incomplete')
        self.assertEqual(next(s for s in report['steps'] if s['name']=='zonos2')['status'], 'failed')
        self.assertTrue(any('prepare_voice_gallery.py' in c.args for c in installer.script.call_args_list))

    def test_setup_report_http_requires_web_auth_and_does_not_create_jobs(self):
        report = self.root/'.runtime/setup-report.json'
        report.parent.mkdir(parents=True)
        report.write_text('{"status":"incomplete","steps":[],"samples":[]}')
        with patch.dict(os.environ, VOICEBENCH_ACCESS_KEY='test-password'):
            server = VoiceBenchServer(('127.0.0.1', 0), self.paths)
        thread = threading.Thread(target=server.serve_forever,daemon=True); thread.start()
        try:
            url = f'http://127.0.0.1:{server.server_port}/api/setup'
            with self.assertRaises(urllib.error.HTTPError) as error: urllib.request.urlopen(url)
            self.assertEqual(error.exception.code, 401)
            import base64
            request = urllib.request.Request(url, headers={'Authorization':'Basic '+base64.b64encode(b'voice:test-password').decode()})
            with urllib.request.urlopen(request) as response:
                self.assertEqual(json.load(response)['status'], 'incomplete')
            self.assertEqual(self.db.fetch_one('SELECT count(*) n FROM jobs')['n'], 0)
        finally:
            server.shutdown(); server.server_close(); thread.join(timeout=2)

    def test_preview_failure_continues_other_voices_and_cache_hash_is_checked(self):
        RegistrationService(self.paths,self.db).upsert_engine(dict(slug=QWEN_CUSTOM,name='Qwen',kind='mock',config={}))
        def generate(engine, request):
            if request.output.name.startswith('qwen-aiden'):
                raise RuntimeError('GPU failure')
            request.output.write_bytes(clip())
        quality = {'transcription': SAMPLE_TEXT, 'retry_recommended':False}
        with patch('tools.prepare_voice_gallery.assert_idle'), patch('tools.prepare_voice_gallery.run_engine',side_effect=generate), \
             patch('tools.prepare_voice_gallery.inspect_wav',return_value={'hard_reject':False}), \
             patch('tools.prepare_voice_gallery.WhisperQualityVerifier') as verifier:
            verifier.return_value.inspect.return_value = quality
            self.assertFalse(prepare(self.paths,self.db,['qwen-aiden','qwen-sohee']))
        report = json.loads((self.paths.data/'voice-gallery/preparation-report.json').read_text(encoding='utf-8'))
        self.assertEqual([r['status'] for r in report], ['failed','ready'])
        path = self.paths.data/'voice-gallery/previews/qwen-sohee.wav'
        voice = dict(preview_kind='generated',preview_path=str(path))
        with patch('tools.prepare_voice_gallery.inspect_wav',return_value={'hard_reject':False}):
            self.assertTrue(valid_cached_preview(voice))
            path.write_bytes(b'corrupted')
            self.assertFalse(valid_cached_preview(voice))


if __name__ == '__main__':
    unittest.main()
