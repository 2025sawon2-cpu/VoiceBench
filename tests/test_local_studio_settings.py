import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from voicebench.audio import make_mock_wav
from voicebench.config import Paths, local_studio_engine, DEFAULT_STUDIO_ENGINE_SLUG
from voicebench.db import Database
from voicebench.service import Service


class LocalStudioSettingsTests(unittest.TestCase):
    def make_service(self, root, slug=None):
        if slug:
            (root / '.runtime').mkdir()
            (root / '.runtime/studio-settings.json').write_text(
                json.dumps({'default_engine_slug': slug}), encoding='utf-8')
        with patch.dict('os.environ', {'VOICEBENCH_ROOT': str(root)}):
            paths = Paths.discover()
        paths.ensure()
        ref = paths.data / 'prepared/ref_voice/selected_reference.wav'
        ref.parent.mkdir(parents=True)
        make_mock_wav(ref, 'reference', 13)
        ref.with_suffix('.txt').write_text('reference', encoding='utf-8')
        service = Service(paths, Database(paths.database))
        service.upsert_engine({'slug': slug or DEFAULT_STUDIO_ENGINE_SLUG,
                               'name': 'Fixture', 'kind': 'mock', 'config': {}})
        return service

    def test_two_checkouts_keep_independent_defaults_and_external_contract(self):
        with tempfile.TemporaryDirectory() as first, tempfile.TemporaryDirectory() as second:
            local = self.make_service(Path(first), 'qwen3-tts-17b-pascal-expressive')
            other = self.make_service(Path(second))
            for service, slug in [(local, 'qwen3-tts-17b-pascal-expressive'),
                                  (other, DEFAULT_STUDIO_ENGINE_SLUG)]:
                text = '이 컴퓨터의 기존 목소리로 대본을 읽습니다.'
                web = service.create_studio_request(text)
                external = service.create_external_tts_request(text)
                self.assertEqual(web['engine_slug'], slug)
                self.assertEqual(external['engine_slug'], slug)
                self.assertEqual(external['seed'], 13)
                self.assertEqual(external['quality_mode'], 'long_form_hq')
                self.assertEqual(web['project_id'], external['project_id'])
                self.assertEqual(service.studio_state()['default_engine_slug'], slug)
                self.assertTrue(service.synthesis_readiness()['ready'])
                self.assertEqual(service.synthesis_readiness()['default_voice_kind'],'personal')

    def test_default_official_reference_is_not_reported_as_personal_clone(self):
        with tempfile.TemporaryDirectory() as folder:
            service=self.make_service(Path(folder))
            service.upsert_engine({'slug':service.default_engine_slug,'name':'Official fixture','kind':'mock',
                                   'config':{'reference_preset':'AmericanMale'}})
            self.assertEqual(service.synthesis_readiness()['default_voice_kind'],'official')

    def test_missing_configured_engine_is_not_reported_ready_or_replaced(self):
        with tempfile.TemporaryDirectory() as folder:
            service = self.make_service(Path(folder), 'qwen3-tts-17b-pascal-expressive')
            service.db.execute('UPDATE engines SET enabled=0 WHERE slug=?', (service.default_engine_slug,))
            self.assertFalse(service.synthesis_readiness()['ready'])
            with self.assertRaisesRegex(ValueError, 'TTS 엔진'):
                service.create_external_tts_request('기본 엔진이 필요합니다.')

    def test_explicit_regeneration_bypasses_cache_but_retries_reuse_request(self):
        with tempfile.TemporaryDirectory() as folder:
            service = self.make_service(Path(folder))
            text = '같은 대본으로 새 음성을 생성해요.'
            original = service.create_external_tts_request(text)
            self.assertIsNone(service.reusable_external_tts_request(text, 'run-new'))
            fresh = service.create_external_tts_request(text, 'run-new')
            self.assertNotEqual(original['id'], fresh['id'])
            self.assertEqual(service.reusable_external_tts_request(text, 'run-new')['id'], fresh['id'])
            self.assertEqual(service.reusable_external_tts_request(text)['id'], original['id'])
            self.assertEqual(fresh['seed'], 13)
            self.assertEqual(fresh['quality_mode'], 'long_form_hq')
            for key in (None, {}, ' ', 'x'*101):
                with self.assertRaises(ValueError):
                    service.create_external_tts_request(text, key)

    def test_invalid_local_setting_fails_explicitly(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / '.runtime').mkdir()
            path = root / '.runtime/studio-settings.json'
            for value in [[], {'default_engine_slug': None}, {'default_engine_slug': '../bad'}]:
                path.write_text(json.dumps(value), encoding='utf-8')
                with self.assertRaises(ValueError):
                    local_studio_engine(root)
