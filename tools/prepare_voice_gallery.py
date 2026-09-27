"""Build short, audited preview files once. Never run inference on a preview GET."""
import argparse,hashlib,json,socket,sys,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from voicebench.config import Paths
from voicebench.db import Database
from voicebench.voices import catalog,config_hash,SAMPLE_TEXT
from voicebench.engines import run_engine,EngineRequest,shutdown_workers
from voicebench.quality import WhisperQualityVerifier
from voicebench.audio import inspect_wav

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--ids',nargs='*');args=parser.parse_args()
    paths=Paths.discover(); db=Database(paths.database)
    try:
        with socket.create_connection(('127.0.0.1',8765),timeout=.5): pass
    except OSError: pass
    else: raise SystemExit('Stop the VoiceBench HTTP server before preparing previews.')
    if db.fetch_one("SELECT id FROM jobs WHERE status IN ('queued','running')"):
        raise SystemExit('VoiceBench queue is busy. Wait before preparing previews.')
    folder=paths.data/'voice-gallery/previews';folder.mkdir(parents=True,exist_ok=True)
    verifier=WhisperQualityVerifier(paths.root)
    # Native speakers first, then references derived from two of those speakers.
    ordered=sorted(catalog(paths,db),key=lambda v:(0 if v.get('speaker') else 1 if v['kind']=='official' else 2,v['engine_slug'],v['id']))
    report=[]
    try:
        for original in ordered:
            id=original['id']
            if args.ids and id not in args.ids: continue
            if original['kind'] in ('personal','library'): continue
            voice=next(v for v in catalog(paths,db) if v['id']==id)
            if voice['preview_kind']=='generated':
                print('CACHED',id,flush=True);continue
            if not voice['available']: print('UNAVAILABLE',id,flush=True);continue
            if db.fetch_one("SELECT id FROM jobs WHERE status IN ('queued','running')"):
                raise RuntimeError('Queue became busy; stop preparing previews.')
            engine=db.fetch_one('SELECT * FROM engines WHERE slug=?',(voice['engine_slug'],))
            config=json.loads(engine['config_json']);generation=config.get('generation',{})
            if voice.get('speaker'): generation={**generation,'speaker':voice['speaker']}
            config['generation']=generation
            preview=folder/(id+'.wav');temporary=folder/(id+'.pending.wav')
            print('GENERATING',id,flush=True);start=time.perf_counter()
            passed=False
            for seed in (13,10):
                adapter=run_engine({**engine,'config_json':json.dumps(config)},EngineRequest(SAMPLE_TEXT,Path(voice['reference']),voice['reference_text'],temporary,seed))
                quality=verifier.inspect(SAMPLE_TEXT,temporary)
                if quality.get('transcription') and not quality.get('retry_recommended') and not inspect_wav(temporary)['hard_reject']:
                    passed=True;break
                print('RETRY',id,seed,quality.get('reasons'),flush=True)
            if not passed:
                report.append({'id':id,'status':'failed','quality':quality});print('FAILED',id,flush=True);continue
            temporary.replace(preview)
            reference=Path(voice['reference'])
            info={'voice_id':id,'engine_slug':engine['slug'],'config_hash':config_hash(engine),'seed':seed,'text':SAMPLE_TEXT,
                  'reference_sha256':hashlib.sha256(reference.read_bytes()).hexdigest() if reference.is_file() and not voice.get('speaker') else None,
                  'sha256':hashlib.sha256(preview.read_bytes()).hexdigest(),'quality':quality,'seconds':round(time.perf_counter()-start,2)}
            preview.with_suffix('.json').write_text(json.dumps(info,ensure_ascii=False,indent=2),encoding='utf-8')
            report.append({'id':id,'status':'ready','seconds':info['seconds']});print('READY',id,info['seconds'],flush=True)
            (folder.parent/'preparation-report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    finally:
        verifier.close();shutdown_workers()
if __name__=='__main__':main()
