import json
import tempfile
import unittest
from collections import OrderedDict
from pathlib import Path
from unittest.mock import Mock, patch

from voicebench.audio import inspect_wav, make_mock_wav
from voicebench.config import Paths
from voicebench.db import Database
from voicebench.engines import _worker_matches
from voicebench.runner import JobRunner
from voicebench.service import Service, STUDIO_ENGINE_SLUG, plan_studio_script


class ZonosProductionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        with patch.dict('os.environ', {'VOICEBENCH_ROOT': str(root)}):
            self.paths = Paths.discover()
        self.paths.ensure()
        self.db = Database(self.paths.database)
        self.service = Service(self.paths, self.db)
        ref = self.paths.data / 'prepared/ref_voice/selected_reference.wav'
        ref.parent.mkdir(parents=True)
        make_mock_wav(ref, 'original reference', 13)
        ref.with_suffix('.txt').write_text('original reference', encoding='utf-8')
        self.reference = ref
        self.service.upsert_engine({'slug':STUDIO_ENGINE_SLUG, 'name':'ZONOS2',
                                    'kind':'command', 'config':{'command':['unused']}})
        self.runner = JobRunner(self.paths, self.db)
        self.addCleanup(self.runner.quality_verifier.close)

    def fake_engine(self, engine, request):
        self.seeds.append(request.seed)
        make_mock_wav(request.output, request.text, request.seed)
        return {'adapter':'test'}

    def quality(self, text, path):
        bad = self.reject_all or len(self.seeds) == 1
        return {'verifier':'whisper-base-cpu', 'transcription':text,
                'retry_recommended':bad, 'reasons':['likely_omission'] if bad else [],
                'score':0.5 if bad else 0, 'wav':inspect_wav(path)}

    def test_default_web_external_and_preview_use_same_whole_short(self):
        text = '정말 이 이야기가 궁금하지 않으세요? ' * 3 + '이제 끝까지 한 번에 읽어봅니다.'
        preview = self.service.preview_studio_script(text)
        web = self.service.create_studio_request(text)
        external = self.service.create_external_tts_request(text)
        self.assertEqual(STUDIO_ENGINE_SLUG, 'zonos2-q8-experimental')
        self.assertEqual(preview['segment_count'], 1)
        for req in (web, external):
            self.assertEqual(req['engine_slug'], STUDIO_ENGINE_SLUG)
            self.assertEqual(req['segment_count'], 1)
            self.assertEqual(req['seed'], 13)
            self.assertTrue(req['quality_control']['enabled'])
            segment = self.db.fetch_one('SELECT text FROM studio_segments WHERE request_id=?', (req['id'],))
            self.assertEqual(segment['text'], preview['segments'][0]['text'])
        self.assertEqual(web['project_id'], external['project_id'])
        job = self.runner.claim()
        config, synthesis = self.runner._synthesis_engine(job)
        self.assertNotIn('non_streaming_mode', json.loads(config['config_json'])['generation'] if 'generation' in json.loads(config['config_json']) else {})
        self.assertEqual(synthesis['input_policy'], 'zonos_shorts_whole_240_v1')

    def test_long_script_stays_bounded_and_preserves_paragraphs(self):
        paragraph = '오늘은 길게 이어지는 이야기를 자연스러운 목소리로 읽어 보겠습니다. ' * 9
        text = paragraph + '\n\n' + paragraph
        plan = plan_studio_script(text, STUDIO_ENGINE_SLUG)
        self.assertGreater(len(plan), 2)
        self.assertTrue(all(len(s.text) <= 240 for s in plan))
        self.assertEqual(max(s.paragraph_index for s in plan), 2)
        self.assertEqual(' '.join(s.text for s in plan), ' '.join(text.split()))

    def run_quality_request(self, reject_all):
        self.seeds, self.reject_all = [], reject_all
        req = self.service.create_studio_request('대본을 빠뜨리지 않고 마지막까지 읽어주세요.')
        with patch('voicebench.runner.run_engine', side_effect=self.fake_engine), \
             patch.object(self.runner.quality_verifier, 'inspect', side_effect=self.quality):
            self.runner.execute_job(self.runner.claim())
        return self.service.studio_request(req['id'])

    def test_zonos_retries_then_records_actual_selected_seed(self):
        req = self.run_quality_request(False)
        self.assertEqual(self.seeds, [13, 10])
        self.assertEqual(req['status'], 'succeeded')
        self.assertEqual(req['quality_control']['selected_seed_counts'], {'10':1})

    def test_all_failed_candidates_are_not_delivered_as_success(self):
        req = self.run_quality_request(True)
        self.assertEqual(self.seeds, [13, 10, 42])
        self.assertEqual(req['status'], 'failed')
        self.assertIsNone(req['audio_url'])
        self.assertTrue(list(self.paths.outputs.rglob('*quality-failed.json')))

    def test_default_male_is_separate_from_personal_reference(self):
        male = self.paths.root / '.runtime/engines/zonos2/python-source/default_voices/AmericanMale.mp3'
        male.parent.mkdir(parents=True)
        male.write_bytes(b'official male test fixture')
        self.service.upsert_engine({'slug':'zonos2-q8-american-male', 'name':'Male', 'kind':'command',
                                    'config':{'command':['unused'], 'reference_preset':'AmericanMale'}})
        personal = self.service.ensure_studio_project()
        preset = self.service.ensure_studio_project('zonos2-q8-american-male')
        self.assertNotEqual(personal['id'], preset['id'])
        self.assertEqual(Path(personal['reference_path']), self.reference)
        self.assertEqual(Path(preset['reference_path']), male)
        self.assertEqual(self.service.ensure_studio_project()['reference_sha256'], personal['reference_sha256'])
        self.service.add_prompt(personal['id'], 'benchmark text')
        engine = self.db.fetch_one("SELECT id FROM engines WHERE slug='zonos2-q8-american-male'")
        with self.assertRaisesRegex(ValueError, 'Studio'):
            self.service.queue_benchmark(personal['id'], [engine['id']], [13])

    def test_old_worker_runtime_cannot_be_reused(self):
        health = {'status':'ready','model':'Zyphra/ZONOS2-Q8_0','attention':'cuda'}
        self.assertFalse(_worker_matches(health, health['model'], 'cuda', 2))
        self.assertTrue(_worker_matches({**health, 'runtime_version':2}, health['model'], 'cuda', 2))

    def test_speaker_cache_uses_content_hash_and_is_bounded(self):
        from adapters.zonos2_worker import ZonosBackend
        backend = ZonosBackend.__new__(ZonosBackend)
        backend.speaker_cache = OrderedDict()
        backend.speaker = Mock()
        backend.speaker.encode.side_effect = lambda p: p.read_bytes()
        path = self.paths.data / 'speaker.wav'
        path.write_bytes(b'a')
        self.assertEqual(backend.speaker_embedding(path), b'a')
        backend.speaker_embedding(path)
        self.assertEqual(backend.speaker.encode.call_count, 1)
        for content in (b'b', b'c', b'd', b'e'):
            path.write_bytes(content)
            self.assertEqual(backend.speaker_embedding(path), content)
        self.assertEqual(len(backend.speaker_cache), 4)

    def test_volume_copies_match_levels_without_modifying_sources(self):
        import audioop
        import hashlib
        import wave
        from tools.match_comparison_volume import match_volume
        loud = self.paths.data / 'loud.wav'
        soft = self.paths.data / 'soft.wav'
        make_mock_wav(loud, 'comparison', 13)
        with wave.open(str(loud), 'rb') as wav:
            params, pcm = wav.getparams(), wav.readframes(wav.getnframes())
        with wave.open(str(soft), 'wb') as wav:
            wav.setparams(params)
            wav.writeframes(audioop.mul(pcm, 2, 0.25))
        hashes = [hashlib.sha256(p.read_bytes()).hexdigest() for p in (loud, soft)]
        rows = match_volume([loud, soft], self.paths.data / 'matched')
        self.assertLessEqual(abs(rows[0]['output_rms'] - rows[1]['output_rms']), 1)
        self.assertEqual(hashes, [hashlib.sha256(p.read_bytes()).hexdigest() for p in (loud, soft)])
        for row in rows:
            self.assertFalse(inspect_wav(Path(row['output']))['hard_reject'])
        with self.assertRaises(ValueError):
            match_volume([loud], loud.parent)
