"""External workflow isolation and portable editing output (no real GPU jobs)."""
import http.client
import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from tests.test_harness import test_paths
from voicebench.api import VoiceBenchServer
from voicebench.audio import make_mock_wav
from voicebench.service import STUDIO_ENGINE_SLUG
from tools import voicebench_mcp as bridge


class ExternalWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        paths = test_paths(Path(self.temp.name))
        paths.ensure()
        ref = paths.data / 'prepared/ref_voice/selected_reference.wav'
        make_mock_wav(ref, 'reference', 13)
        ref.with_suffix('.txt').write_text('reference', encoding='utf-8')
        with patch.dict('os.environ', {'VOICEBENCH_EXTERNAL_API_KEY': 'test-workflow-key'}):
            self.server = VoiceBenchServer(('127.0.0.1', 0), paths)
        self.service = self.server.service
        self.service.upsert_engine(dict(slug=STUDIO_ENGINE_SLUG, name='test', kind='mock', version='test', config={}))
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(5)
        self.temp.cleanup()

    def http(self, method, route, payload=None, key='test-workflow-key'):
        connection = http.client.HTTPConnection(*self.server.server_address, timeout=5)
        connection.request(method, route, body=json.dumps(payload) if payload is not None else None,
                           headers={'Authorization': 'Bearer ' + key, 'Content-Type': 'application/json'})
        response = connection.getresponse()
        result = (response.status, json.loads(response.read()))
        connection.close()
        return result

    def test_history_pagination_and_status_filter_exclude_studio(self):
        self.service.create_studio_request('Studio private', 13)
        ids = [self.service.create_external_tts_request('external ' + str(i))['id'] for i in range(3)]
        status, page = self.http('GET', '/v1/tts?limit=2')
        self.assertEqual(status, 200)
        self.assertEqual([j['id'] for j in page['jobs']], ids[::-1][:2])
        _, older = self.http('GET', '/v1/tts?limit=2&before_id=' + str(page['next_before_id']))
        self.assertEqual([j['id'] for j in older['jobs']], ids[:1])
        self.assertIsNone(older['next_before_id'])
        self.service.cancel_studio_request(ids[1])
        _, cancelled = self.http('GET', '/v1/tts?status=cancelled')
        self.assertEqual([j['id'] for j in cancelled['jobs']], [ids[1]])

    def test_cancel_requires_auth_and_external_source_and_cancels_queued_segments(self):
        studio = self.service.create_studio_request('Studio private', 13)['id']
        external = self.service.create_external_tts_request('External queue')['id']
        self.assertEqual(self.http('POST', f'/v1/tts/{external}/cancel', {}, key='wrong')[0], 401)
        self.assertEqual(self.http('POST', f'/v1/tts/{studio}/cancel', {})[0], 404)
        self.assertEqual(self.service.studio_request(studio)['status'], 'queued')
        status, result = self.http('POST', f'/v1/tts/{external}/cancel', {})
        self.assertEqual((status, result['status']), (200, 'cancelled'))
        self.assertEqual(result['queued_segments'], 0)
        self.assertEqual(self.http('POST', f'/v1/tts/{external}/cancel', {})[1]['status'], 'cancelled')

    def test_export_keeps_original_numbers_and_does_not_invent_word_alignment(self):
        text = '3초면 펼쳐지고, 4인 가족이 쓸 수 있어요.'
        request = self.service.create_external_tts_request(text)
        self.assertEqual(self.http('GET', f"/v1/tts/{request['id']}/export")[0], 400)
        output = self.server.paths.artifacts / 'studio/export.wav'
        make_mock_wav(output, text, 13)
        self.server.database.execute('UPDATE studio_requests SET status=?,output_path=? WHERE id=?',
                                     ('succeeded', str(output), request['id']))
        status, result = self.http('GET', f"/v1/tts/{request['id']}/export")
        self.assertEqual(status, 200)
        self.assertEqual(result['original_text'], text)
        self.assertIn(text, result['srt'])
        self.assertEqual(result['subtitle_timing'], 'whole_script')
        self.assertFalse(result['word_aligned'])
        self.assertGreater(result['duration_seconds'], 0)
        self.assertEqual(result['audio_format']['sample_rate'], 24000)
        self.assertNotIn(str(self.server.paths.root), json.dumps(result))
        studio = self.service.create_studio_request('private', 13)['id']
        self.assertEqual(self.http('GET', f'/v1/tts/{studio}/export')[0], 404)

    def test_invalid_history_bounds_are_rejected(self):
        for query in ('limit=0', 'limit=101', 'before_id=-1', 'status=bogus'):
            self.assertEqual(self.http('GET', '/v1/tts?' + query)[0], 400)


class BridgeWorkflowTests(unittest.TestCase):
    def test_batch_partial_acceptance_keeps_ids_and_retry_indices(self):
        with patch.object(bridge, 'submit_tts', side_effect=[{'id': 91}, RuntimeError('queue full'), {'id': 92}]):
            result = bridge.submit_batch([{'text': 'a'}, {'text': 'b'}, {'text': 'c'}])
        self.assertEqual(result['accepted_count'], 2)
        self.assertEqual(result['retry_indices'], [1])
        self.assertEqual(result['items'][2]['job']['id'], 92)

    def test_batch_invalid_item_is_rejected_before_any_submission(self):
        with patch.object(bridge, 'submit_tts') as submit:
            for items in ([{'text': 'valid'}, {'text': ''}], [{'text': 'a', 'seed': 999}], [{'text': 'a', 'generation_key': 'bad key'}]):
                with self.assertRaises(RuntimeError):
                    bridge.submit_batch(items)
            submit.assert_not_called()

    def test_nested_result_urls_use_client_accessible_base_without_key(self):
        with patch.dict('os.environ', {'VOICEBENCH_URL': 'http://127.0.0.1:8765',
                                      'VOICEBENCH_PUBLIC_URL': 'http://192.168.0.58:8766'}):
            result = bridge.public_links({'audio_url': '/v1/tts/91/audio',
                                          'chapters': [{'audio_url': '/v1/tts/91/chapters/1/audio'}]})
        self.assertEqual(result['audio_url'], 'http://192.168.0.58:8766/v1/tts/91/audio')
        self.assertNotIn('127.0.0.1', str(result))
        self.assertEqual(len(bridge.tool_definitions()), 14)


if __name__ == '__main__':
    unittest.main()
