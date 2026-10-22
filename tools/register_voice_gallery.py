"""Register the official nine-speaker Qwen model without modifying Base presets."""
import argparse,json,subprocess,sys,os
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from voicebench.config import Paths
from voicebench.db import Database
from tools.setup_registration import RegistrationService as Service
from voicebench.voices import QWEN_CUSTOM

def register():
    paths=Paths.discover();db=Database(paths.database)
    manifest=json.loads((ROOT/'.runtime/engines/qwen3-tts/custom-voice.json').read_text(encoding='utf-8'))
    base=db.fetch_one("SELECT * FROM engines WHERE slug='qwen3-tts-17b-pascal'")
    config=json.loads(base['config_json'])
    config.pop('command',None)
    config['requires_reference_text']=False
    command=config['worker']['command']
    command[command.index('--model')+1]=manifest['path']
    config['worker'].update(model=manifest['path'],runtime_version=3)
    config['generation']['non_streaming_mode']=True
    config['environment']['HF_HUB_OFFLINE']='1'
    config['environment']['PYTHONUTF8']='1'
    return Service(paths,db).upsert_engine({'slug':QWEN_CUSTOM,'name':'Qwen · 기본 목소리 9종','kind':'command','version':manifest['revision'],'enabled':True,'config':config})
def download():
    # Run with the already installed Qwen environment; keep all files in VoiceBench.
    import huggingface_hub.file_download as fd
    from huggingface_hub import snapshot_download
    fd.are_symlinks_supported=lambda cache_dir=None: False
    model='Qwen/Qwen3-TTS-12Hz-1.7B-CustomVoice'
    revision='0c0e3051f131929182e2c023b9537f8b1c68adfe'
    path=snapshot_download(model,revision=revision,cache_dir=os.environ.get('VOICEBENCH_MODEL_CACHE',str(ROOT/'.runtime/cache/huggingface/hub')),
        allow_patterns=['*.json','*.safetensors','*.txt','*.model','*.tiktoken','LICENSE','README.md'],max_workers=4)
    manifest=ROOT/'.runtime/engines/qwen3-tts/custom-voice.json'
    manifest.write_text(json.dumps(dict(model=model,revision=revision,path=path)),encoding='utf-8')

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--download',action='store_true');parser.add_argument('--download-only',action='store_true',help=argparse.SUPPRESS);args=parser.parse_args()
    if args.download_only: download()
    else:
        if args.download:
            subprocess.run([str(ROOT/'.runtime/engines/qwen3-tts/venv/Scripts/python.exe'),str(Path(__file__).resolve()),'--download-only'],cwd=ROOT,check=True)
        print(register()['slug'])
