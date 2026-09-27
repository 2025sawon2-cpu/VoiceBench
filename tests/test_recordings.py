import base64
import http.client
import io
import json
from pathlib import Path
import subprocess
import tempfile
import threading
import unittest
from unittest.mock import patch
import wave

from voicebench.api import VoiceBenchServer
from voicebench.config import Paths
from voicebench.recordings import ReferenceTranscriber, validate_voice_clip


def wav(seconds=4, width=2):
    output = io.BytesIO()
    with wave.open(output, 'wb') as audio:
        audio.setnchannels(1); audio.setsampwidth(width); audio.setframerate(16000)
        audio.writeframes(bytes(16000 * seconds * width))
    return output.getvalue()


class RecordingTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(); self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        with patch.dict('os.environ', {'VOICEBENCH_ROOT':str(self.root)}):
            self.paths = Paths.discover()
        self.paths.ensure()

    def test_incomplete_or_invalid_clips_are_rejected_before_asr(self):
        self.assertEqual(validate_voice_clip(wav(3)), 3)
        self.assertEqual(validate_voice_clip(wav(30)), 30)
        transcriber = ReferenceTranscriber(self.root)
        with patch('voicebench.recordings.subprocess.run') as run:
            for content in (wav(2), wav(31), wav(width=1), wav()[:100], b'not audio'):
                with self.subTest(size=len(content)), self.assertRaises(ValueError):
                    transcriber.transcribe(content)
            run.assert_not_called()

    def prepare_runtime(self):
        worker = self.root / 'adapters/whisper_quality_worker.py'
        worker.parent.mkdir(); worker.touch()
        python = self.root / 'python.exe'; python.touch()
        return python

    def test_transcription_cleans_clip_and_timeout_releases_admission_lock(self):
        python = self.prepare_runtime()
        transcriber = ReferenceTranscriber(self.root)
        def result(*args, **kwargs):
            payload = json.loads(kwargs['input'].splitlines()[0])
            self.assertEqual(Path(payload['audio']).read_bytes(), wav())
            self.assertEqual(kwargs['timeout'], 110)
            self.assertEqual(kwargs['env']['HF_HUB_OFFLINE'], '1')
            return subprocess.CompletedProcess(args, 0, '{"status":"ready"}\n{"status":"ok","transcription":"안녕하세요."}\n')
        with patch('voicebench.recordings.WhisperQualityVerifier._python_path',return_value=python), patch('voicebench.recordings.WhisperQualityVerifier._model_path',return_value=self.root), patch('voicebench.recordings.subprocess.run',side_effect=result) as run:
            self.assertEqual(transcriber.transcribe(wav()), '안녕하세요.')
            self.assertEqual(list((self.root/'.runtime/reference-transcription').iterdir()), [])
            run.side_effect = subprocess.TimeoutExpired('test',110)
            with self.assertRaisesRegex(ValueError,'시간이 초과'):
                transcriber.transcribe(wav())
            self.assertTrue(transcriber._lock.acquire(False));transcriber._lock.release()
            self.assertEqual(list((self.root/'.runtime/reference-transcription').iterdir()), [])

    def test_concurrent_transcription_is_rejected(self):
        transcriber=ReferenceTranscriber(self.root);transcriber._lock.acquire()
        try:
            with self.assertRaisesRegex(ValueError,'인식 중'):
                transcriber.transcribe(wav())
        finally: transcriber._lock.release()

    def test_http_auth_version_and_transcription_leave_tts_queue_unchanged(self):
        self.paths.static.mkdir(parents=True)
        (self.paths.static/'index.html').write_text('__VOICEBENCH_UI_VERSION__',encoding='utf-8')
        with patch.dict('os.environ',{'VOICEBENCH_ACCESS_KEY':'test-recording-only'}):
            server=VoiceBenchServer(('127.0.0.1',0),self.paths)
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        authorization='Basic '+base64.b64encode(b'voice:test-recording-only').decode()
        def request(method,path,headers=None,body=None):
            connection=http.client.HTTPConnection(*server.server_address,timeout=5)
            connection.request(method,path,headers=headers or {},body=body)
            response=connection.getresponse();content=response.read();connection.close()
            return response,content
        try:
            with patch.object(server.reference_transcriber,'transcribe',return_value='테스트입니다.') as transcribe:
                response,_=request('POST','/api/voices/transcribe',body=wav())
                self.assertEqual(response.status,401);transcribe.assert_not_called()
                response,body=request('POST','/api/voices/transcribe',{'Authorization':authorization},wav())
                self.assertEqual(response.status,200);self.assertEqual(json.loads(body)['transcript'],'테스트입니다.')
            response,version=request('GET','/',{'Authorization':authorization})
            self.assertEqual(response.getheader('Cache-Control'),'no-store')
            self.assertEqual(version.decode(),server.ui_version())
            (self.paths.static/'new.css').write_text('body{}')
            self.assertNotEqual(version.decode(),server.ui_version())
            self.assertEqual(server.database.fetch_one('SELECT COUNT(*) n FROM jobs')['n'],0)
        finally:
            server.shutdown();server.server_close();thread.join(timeout=5)
