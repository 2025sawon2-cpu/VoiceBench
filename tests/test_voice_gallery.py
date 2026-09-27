import hashlib,json,tempfile,threading,unittest
from pathlib import Path
from unittest.mock import MagicMock,patch
from voicebench.config import Paths
from voicebench.db import Database
from voicebench.service import Service,STUDIO_ENGINE_SLUG
from voicebench.runner import JobRunner
from voicebench.voices import QWEN_CUSTOM,catalog,config_hash,validate_generation
from adapters.qwen3_tts_worker import QwenWorkerServer

class VoiceGalleryTests(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup)
        with patch.dict('os.environ',{'VOICEBENCH_ROOT':tmp.name}): self.paths=Paths.discover()
        self.paths.ensure();self.db=Database(self.paths.database);self.service=Service(self.paths,self.db)
        for slug in (STUDIO_ENGINE_SLUG,QWEN_CUSTOM):
            self.service.upsert_engine(dict(slug=slug,name=slug,kind='mock',config={'generation':{'temperature':.7}}))
        self.ref=self.paths.root/'.runtime/engines/zonos2/python-source/default_voices/AmericanMale.mp3'
        self.ref.parent.mkdir(parents=True);self.ref.write_bytes(b'original reference')
        personal=self.paths.data/'prepared/ref_voice/selected_reference.wav'
        personal.parent.mkdir(parents=True);personal.write_bytes(b'personal reference')

    def test_native_speaker_and_custom_settings_survive_queue_without_mutating_preset(self):
        req=self.service.create_studio_request('Hello.',engine_slug=QWEN_CUSTOM,voice_id='qwen-sohee',generation={'temperature':.9,'instruct':'Naturally.'})
        self.assertEqual(req['voice_id'],'qwen-sohee')
        runner=JobRunner(self.paths,self.db);job=runner.claim();engine,meta=runner._synthesis_engine(job)
        self.assertEqual(meta['generation'],{'speaker':'Sohee','temperature':.9,'instruct':'Naturally.'})
        original=self.db.fetch_one('SELECT config_json FROM engines WHERE slug=?',(QWEN_CUSTOM,))
        self.assertEqual(json.loads(original['config_json'])['generation'],{'temperature':.7})
        with self.assertRaises(ValueError): self.service.create_studio_request('Hello.',engine_slug=QWEN_CUSTOM)
        with self.assertRaises(ValueError): self.service.create_studio_request('Hello.',voice_id='qwen-sohee')

    def test_official_reference_is_snapshotted_and_protected_for_queued_jobs(self):
        req=self.service.create_studio_request('Hello.',voice_id='zonos-americanmale')
        project=self.service.get_project(req['project_id']);snap=Path(project['reference_path'])
        self.assertNotEqual(snap,self.ref);self.ref.write_bytes(b'new recording')
        self.assertEqual(snap.read_bytes(),b'original reference')
        with self.assertRaises(ValueError): self.service.update_reference_text(project['id'],'changed')
        next_req=self.service.create_studio_request('Hello.',voice_id='zonos-americanmale')
        self.assertNotEqual(next_req['project_id'],req['project_id'])

    def test_preview_read_never_queues_work_and_invalidates_changed_reference(self):
        voice=self.service.catalog_voice('zonos-americanmale')
        self.assertEqual(voice['preview_kind'],'reference')
        preview=self.paths.data/'voice-gallery/previews/zonos-americanmale.wav'
        preview.parent.mkdir(parents=True);preview.write_bytes(b'cached audio')
        engine=self.db.fetch_one('SELECT * FROM engines WHERE slug=?',(STUDIO_ENGINE_SLUG,))
        preview.with_suffix('.json').write_text(json.dumps({'voice_id':voice['id'],'config_hash':config_hash(engine),'reference_sha256':hashlib.sha256(self.ref.read_bytes()).hexdigest(),'text':'Hello.'}))
        self.assertEqual(self.service.voice_preview_path(voice['id']),preview)
        self.assertEqual(self.db.fetch_one('SELECT COUNT(*) n FROM jobs')['n'],0)
        self.ref.write_bytes(b'changed')
        self.assertEqual(self.service.voice_preview_path(voice['id']),self.ref)
        with self.assertRaises(ValueError):self.service.voice_preview_path('../../secrets')
        with self.assertRaises(FileNotFoundError):self.service.voice_preview_path('qwen-sohee')

    def test_invalid_overrides_and_external_voice_selection_are_rejected(self):
        for params in ({'temperature':float('nan')},{'temperature':True},{'top_k':1.5},{'speaker':'Ryan'},{'temperature':3}):
            with self.subTest(params=params),self.assertRaises(ValueError):validate_generation(QWEN_CUSTOM,params)
        with self.assertRaises(ValueError):self.service.create_studio_request('Hello.',source='external_api',voice_id='zonos-americanmale')
        with self.assertRaises(ValueError):self.service.create_studio_request('Hello.',source='external_api',generation={'temperature':.9})

    def test_native_worker_uses_custom_voice_without_any_reference_or_clone_prompt(self):
        server=object.__new__(QwenWorkerServer);server.model_name='Qwen/CustomVoice'
        server.model=MagicMock();server.model.get_supported_speakers.return_value=['sohee','ryan']
        server.model.generate_custom_voice.return_value=([[.1]],24000)
        server.torch=MagicMock();server.prompt_cache={};server.generation_lock=threading.Lock()
        with patch.dict('sys.modules',{'numpy':MagicMock(),'soundfile':MagicMock()}):
            server.synthesize({'text':'Hello.','output':str(self.paths.data/'test.wav'),'generation':{'speaker':'Sohee','temperature':.8}})
        server.model.generate_custom_voice.assert_called_once_with(text='Hello.',language='Korean',speaker='Sohee',instruct='',temperature=.8)
        server.model.generate_voice_clone.assert_not_called();server.model.create_voice_clone_prompt.assert_not_called()
    def test_preview_http_auth_and_byte_ranges_without_gpu_or_jobs(self):
        import base64,http.client
        from voicebench.api import VoiceBenchServer
        with patch.dict('os.environ',{'VOICEBENCH_ACCESS_KEY':'test-preview-only'}):
            server=VoiceBenchServer(('127.0.0.1',0),self.paths)
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        try:
            token=base64.b64encode(b'voice:test-preview-only').decode()
            for headers,status,expected in [({},401,None),({'Authorization':'Basic '+token,'Range':'bytes=0-7'},206,b'original')]:
                conn=http.client.HTTPConnection(*server.server_address,timeout=5)
                conn.request('GET','/api/voice-catalog/zonos-americanmale/preview',headers=headers)
                response=conn.getresponse();body=response.read();conn.close()
                self.assertEqual(response.status,status)
                if expected is not None:
                    self.assertEqual(body,expected);self.assertEqual(response.getheader('Content-Type'),'audio/mpeg')
            self.assertEqual(self.db.fetch_one('SELECT COUNT(*) n FROM jobs')['n'],0)
        finally:
            server.shutdown();server.server_close();thread.join(timeout=5)
