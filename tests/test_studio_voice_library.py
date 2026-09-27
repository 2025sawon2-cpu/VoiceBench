import io
import tempfile
import unittest
import wave
from pathlib import Path
from unittest.mock import patch
from voicebench.config import Paths
from voicebench.db import Database
from voicebench.service import Service, STUDIO_ENGINE_SLUG

class StudioVoiceLibraryTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        with patch.dict('os.environ', {'VOICEBENCH_ROOT': tmp.name}):
            self.paths = Paths.discover()
        self.paths.ensure()
        self.db = Database(self.paths.database)
        self.service = Service(self.paths, self.db)
        self.service.upsert_engine({'slug': STUDIO_ENGINE_SLUG, 'name':'ZONOS2', 'kind':'mock', 'config':{}})
        self.audio = io.BytesIO()
        with wave.open(self.audio, 'wb') as out:
            out.setnchannels(1); out.setsampwidth(2); out.setframerate(16000)
            out.writeframes(b'\0\0' * 16000 * 4)
        ref = self.paths.data / 'prepared/ref_voice/selected_reference.wav'
        ref.parent.mkdir(parents=True)
        ref.write_bytes(self.audio.getvalue())

    def test_voice_selection_reaches_jobs_and_external_keeps_default(self):
        voice = self.service.create_library_voice('Test voice', 'Reference transcript', self.audio.getvalue())
        request = self.service.create_studio_request('Custom request.', voice_project_id=voice['id'])
        job = self.db.fetch_one('SELECT project_id FROM jobs ORDER BY id DESC LIMIT 1')
        self.assertEqual(job['project_id'], voice['id'])
        self.assertEqual(request['project_id'], voice['id'])
        external = self.service.create_external_tts_request('External request.')
        self.assertNotEqual(external['project_id'], voice['id'])
        self.assertEqual(external['seed'], 13)
        self.assertEqual(self.service.studio_state()['voices'][0]['name'], 'Test voice')
        with self.assertRaises(ValueError):
            self.service.create_studio_request('Forbidden.', source='external_api', voice_project_id=voice['id'])
        with self.assertRaises(ValueError):
            self.service.update_reference_text(voice['id'], 'changed')
        with self.assertRaises(ValueError):
            self.service.upload_reference(voice['id'], 'new.wav', self.audio.getvalue())

    def test_invalid_uploads_do_not_create_projects(self):
        for content in (b'not wav', self.audio.getvalue()[:100]):
            with self.assertRaises(ValueError):
                self.service.create_library_voice('Test', 'transcript', content)
        self.assertEqual(self.db.fetch_one('SELECT COUNT(*) AS n FROM projects')['n'], 0)
        with self.assertRaises(ValueError):
            self.service.create_library_voice('', 'transcript', self.audio.getvalue())
        with self.assertRaises(ValueError):
            self.service.create_library_voice('Test', '', self.audio.getvalue())

    def test_preset_and_arbitrary_project_cannot_override_voice(self):
        voice = self.service.create_library_voice('Test voice', 'Reference transcript', self.audio.getvalue())
        self.service.upsert_engine({'slug':'male', 'name':'Male', 'kind':'mock', 'config':{'reference_preset':'AmericanMale'}})
        with self.assertRaises(ValueError):
            self.service.create_studio_request('Test.', engine_slug='male', voice_project_id=voice['id'])
        project = self.service.create_project('Benchmark', '')
        with self.assertRaises(ValueError):
            self.service.create_studio_request('Test.', voice_project_id=project['id'])
        with self.assertRaises(ValueError):
            self.service.create_studio_request('Test.', seed=-1)
