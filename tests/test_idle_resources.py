from __future__ import annotations

import http.client
import json
import sqlite3
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from voicebench.api import VoiceBenchServer
from voicebench.config import Paths


class IdleResourcesTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        data = root / 'data'
        self.paths = Paths(root, data, data / 'voicebench.sqlite3', data / 'artifacts',
                           data / 'references', data / 'outputs', root / 'static')
        patcher = patch('voicebench.api.shutdown_workers')
        self.stop_workers = patcher.start()
        self.addCleanup(patcher.stop)
        self.server = VoiceBenchServer(('127.0.0.1', 0), self.paths)
        self.server.external_api_key = 'test-idle-resources-key'
        self.addCleanup(self.server.server_close)
        self.database = self.server.database
        self.project = self.server.service.create_project('Benchmark fixture')
        self.engine = self.server.service.upsert_engine({
            'slug': 'test-personal', 'name': 'Personal fixture', 'kind': 'mock',
            'config': {'seed': 13, 'quality_mode': 'hq', 'reference': 'selected.wav'}})

    def add_job(self, status):
        prompt = self.server.service.add_prompt(self.project['id'], 'A fixture script')
        return self.database.execute(
            'INSERT INTO jobs(project_id,prompt_id,engine_id,seed,status,created_at) VALUES(?,?,?,?,?,?)',
            (self.project['id'], prompt['id'], self.engine['id'], 13, status, 'test'))

    def add_request(self, status, source='studio'):
        return self.database.execute(
            'INSERT INTO studio_requests(project_id,original_text,seed,status,source,created_at) VALUES(?,?,?,?,?,?)',
            (self.project['id'], 'A fixture script', 13, status, source, 'test'))

    def test_idle_release_preserves_config_reference_and_database(self):
        settings = self.paths.root / '.runtime' / 'studio-settings.json'
        settings.parent.mkdir()
        settings.write_text('{"default_engine_slug":"test-personal"}', encoding='utf-8')
        reference = self.paths.references / 'selected.wav'
        reference.write_bytes(b'unchanged personal recording fixture')
        self.add_job('succeeded')
        self.add_request('succeeded')
        with self.database.connect() as connection:
            before = list(connection.iterdump())
        original_settings, original_reference = settings.read_bytes(), reference.read_bytes()
        result = self.server.release_idle_resources()
        self.assertEqual(result, {'status': 'released', 'released': True,
                                  'active_jobs': 0, 'active_requests': 0})
        self.stop_workers.assert_called_once_with()
        self.assertEqual(settings.read_bytes(), original_settings)
        self.assertEqual(reference.read_bytes(), original_reference)
        with self.database.connect() as connection:
            self.assertEqual(list(connection.iterdump()), before)

    def test_benchmark_jobs_are_protected_even_without_external_requests(self):
        for status in ('queued', 'running'):
            with self.subTest(status=status):
                job_id = self.add_job(status)
                self.assertEqual(self.server.service.active_external_tts_count(), 0)
                result = self.server.release_idle_resources()
                self.assertEqual(result['status'], 'busy')
                self.assertEqual(result['active_jobs'], 1)
                self.assertFalse(result['released'])
                self.stop_workers.assert_not_called()
                self.assertEqual(self.database.fetch_one('SELECT status FROM jobs WHERE id=?', (job_id,))['status'], status)
                self.database.execute('DELETE FROM jobs WHERE id=?', (job_id,))

    def test_partial_studio_or_external_request_prevents_release(self):
        # Creation inserts the request before its jobs; protect that gap too.
        for source in ('studio', 'external_api'):
            for status in ('queued', 'running'):
                with self.subTest(source=source, status=status):
                    request_id = self.add_request(status, source)
                    result = self.server.release_idle_resources()
                    self.assertEqual(result['status'], 'busy')
                    self.assertEqual(result['active_requests'], 1)
                    self.stop_workers.assert_not_called()
                    self.database.execute('DELETE FROM studio_requests WHERE id=?', (request_id,))

    def test_shutdown_holds_external_lock_and_database_write_reservation(self):
        def check_guards():
            self.assertTrue(self.server.external_request_lock.locked())
            with sqlite3.connect(self.paths.database, timeout=0) as other:
                with self.assertRaisesRegex(sqlite3.OperationalError, 'locked'):
                    other.execute('BEGIN IMMEDIATE')
        self.stop_workers.side_effect = check_guards
        self.server.release_idle_resources()
        self.stop_workers.side_effect = None
        self.assertFalse(self.server.external_request_lock.locked())
        with self.database.connect() as connection:
            connection.execute('BEGIN IMMEDIATE')
            connection.execute('ROLLBACK')

    def test_shutdown_error_also_releases_queue_guards(self):
        self.stop_workers.side_effect = RuntimeError('fixture shutdown error')
        with self.assertRaisesRegex(RuntimeError, 'fixture shutdown error'):
            self.server.release_idle_resources()
        self.stop_workers.side_effect = None
        self.assertFalse(self.server.external_request_lock.locked())
        self.assertEqual(self.server.release_idle_resources()['status'], 'released')

    def test_http_release_requires_external_bearer_authentication(self):
        thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        thread.start()
        try:
            for authorization, expected in (('', 401), ('Bearer wrong', 401),
                                           ('Bearer test-idle-resources-key', 200)):
                with self.subTest(authorization=authorization):
                    connection = http.client.HTTPConnection(*self.server.server_address, timeout=3)
                    connection.request('POST', '/v1/resources/release-idle', body='{}', headers={
                        'Content-Type': 'application/json', 'Authorization': authorization})
                    response = connection.getresponse()
                    body = json.loads(response.read())
                    connection.close()
                    self.assertEqual(response.status, expected)
                    if expected == 401:
                        self.stop_workers.assert_not_called()
                    else:
                        self.assertEqual(body['status'], 'released')
                        self.stop_workers.assert_called_once_with()
        finally:
            self.server.shutdown()
            thread.join(timeout=3)


if __name__ == '__main__':
    unittest.main()
