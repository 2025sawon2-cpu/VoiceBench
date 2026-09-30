"""Voice identities and request-scoped controls, separate from inference engines."""
from __future__ import annotations
import hashlib
import json
import math
from pathlib import Path

SAMPLE_TEXT = '안녕하세요. 오늘은 일상 속 작은 이야기를 자연스럽게 들려드릴게요.'
QWEN_CUSTOM = 'qwen3-tts-17b-custom-voice'
FAMILIES = {
    'zonos2': ('ZONOS2', 'zonos2-q8-experimental'),
    'qwen3': ('Qwen', 'qwen3-tts-17b-pascal-expressive'),
    'voxcpm2': ('VoxCPM2', 'voxcpm2-hq-experimental'),
    'fish': ('Fish', 'fish-s2-pro-experimental'),
    'chatterbox': ('Chatterbox', 'chatterbox-multilingual-v3-experimental'),
}
# Descriptions derive from Qwen's official speaker descriptions, not sampling values.
QWEN_VOICES = [
    ('Sohee','소희 · 따뜻한 목소리','female','한국어','풍부한 표현의 한국어 여성 음색'),
    ('Aiden','에이든 · 밝은 목소리','male','영어','맑은 중음의 미국 남성 음색'),
    ('Ryan','라이언 · 활기찬 목소리','male','영어','리듬감 있는 남성 음색'),
    ('Serena','세레나 · 부드러운 목소리','female','중국어','따뜻하고 부드러운 여성 음색'),
    ('Vivian','비비안 · 선명한 목소리','female','중국어','밝고 또렷한 젊은 여성 음색'),
    ('Uncle_Fu','푸 · 중후한 목소리','male','중국어','낮고 부드러운 중년 남성 음색'),
    ('Dylan','딜런 · 담백한 목소리','male','중국어','맑고 자연스러운 베이징 남성 음색'),
    ('Eric','에릭 · 경쾌한 목소리','male','중국어','약간 허스키하고 밝은 남성 음색'),
    ('Ono_Anna','안나 · 발랄한 목소리','female','일본어','가볍고 발랄한 일본어 여성 음색'),
]
ZONOS_VOICES = [
    ('AmericanMale','아메리칸 · 남성','male','미국 영어'),
    ('AmericanFemale','아메리칸 · 여성','female','미국 영어'),
    ('BritishFemale','브리티시 · 여성','female','영국 영어'),
]

def family(slug):
    return next((k for k in FAMILIES if slug.startswith(k)), '')

def controls(slug):
    f = family(slug)
    # label, min, max, step; only controls accepted by the installed adapter.
    common = {'temperature': ('변화 폭 · Temperature', .1, 2., .05), 'top_p': ('표현 후보 범위 · Top P', .05, 1., .05)}
    if f == 'zonos2': return common | {'min_p': ('최소 후보 확률 · Min P', .01, 1., .01)}
    if f == 'qwen3': return common | {'top_k': ('표현 후보 수 · Top K', 1, 100, 1), 'repetition_penalty': ('반복 억제', 1., 2., .05), 'subtalker_temperature': ('음향 변화 폭', .1, 2., .05)}
    if f == 'fish': return {'temperature': ('변화 폭 · Temperature', .1, 1., .05), 'top_p':common['top_p'], 'top_k': ('표현 후보 수 · Top K', 1, 100, 1)}
    if f == 'voxcpm2': return {'cfg_value': ('대본·참조 반영 강도', .5, 5., .1), 'inference_timesteps': ('추론 단계', 5, 30, 1)}
    return {}

def validate_generation(slug, supplied):
    if supplied is None: return {}
    if not isinstance(supplied, dict): raise ValueError('고급 설정은 객체여야 합니다.')
    allowed = controls(slug)
    result = {}
    for key, value in supplied.items():
        if key == 'instruct' and slug == QWEN_CUSTOM:
            if not isinstance(value,str) or len(value)>300: raise ValueError('낭독 지시는 300자 이하로 입력하세요.')
            result[key] = value.strip(); continue
        if key not in allowed: raise ValueError(f'이 모델에서 지원하지 않는 설정입니다: {key}')
        _, lower, upper, step = allowed[key]
        if isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value) or not lower <= value <= upper:
            raise ValueError(f'{key}: {lower}~{upper} 범위로 입력하세요.')
        if step == 1 and int(value)!=value: raise ValueError(f'{key}는 정수여야 합니다.')
        result[key] = int(value) if step == 1 else value
    return result

def config_hash(engine):
    return hashlib.sha256(engine['config_json'].encode()).hexdigest()

def catalog(paths, db):
    engines = {e['slug']:e for e in db.fetch_all('SELECT * FROM engines WHERE enabled=1')}
    families = dict(FAMILIES)
    if families['zonos2'][1] not in engines:
        alternative = next((slug for slug in ('zonos2-q6-experimental','zonos2-q5-experimental','zonos2-q4-experimental') if slug in engines),None)
        if alternative: families['zonos2'] = ('ZONOS2',alternative)
    folder = paths.data / 'voice-gallery'
    selected = paths.data / 'prepared/ref_voice/selected_reference.wav'
    transcript = selected.with_suffix('.txt')
    personal_text = transcript.read_text(encoding='utf-8').strip() if transcript.is_file() else ''
    rows = []
    def add(id, name, slug, gender, native, description, kind, reference, text='', **extra):
        engine = engines.get(slug)
        if not engine: return
        ref = Path(reference)
        row = dict(id=id,name=name,engine_slug=slug,model=FAMILIES[family(slug)][0],gender=gender,native_language=native,
                   description=description,kind=kind,reference=str(ref),reference_text=text,
                   available=bool(extra.get('speaker')) or ref.is_file(),**extra)
        preview = folder / 'previews' / (id+'.wav')
        meta = preview.with_suffix('.json')
        cache_valid = False
        if preview.is_file() and meta.is_file():
            try:
                info=json.loads(meta.read_text(encoding='utf-8'))
                refhash=hashlib.sha256(ref.read_bytes()).hexdigest() if ref.is_file() and not extra.get('speaker') else None
                cache_valid = info.get('config_hash')==config_hash(engine) and info.get('reference_sha256')==refhash and info.get('voice_id')==id
            except (OSError,ValueError): pass
        row['preview_kind'] = 'generated' if cache_valid else 'reference' if ref.is_file() and not extra.get('speaker') else 'missing'
        row['preview_url'] = f'/api/voice-catalog/{id}/preview' if row['preview_kind']!='missing' else None
        row['preview_text'] = info.get('text','') if cache_valid else text
        row['preview_path'] = str(preview if cache_valid else ref)
        row['preview_format'] = Path(row['preview_path']).suffix
        rows.append(row)
    for key,(title,slug) in families.items():
        add('personal-'+key,'내 목소리',slug,'personal','한국어','내 녹음을 바탕으로 만든 목소리','personal',selected,personal_text)
    for speaker,name,gender,native in ZONOS_VOICES:
        add('zonos-'+speaker.lower(),name,families['zonos2'][1],gender,native,'ZONOS2 공식 제공 참조 음성','official',paths.root/'.runtime/engines/zonos2/python-source/default_voices'/(speaker+'.mp3'))
    for speaker,name,gender,native,description in QWEN_VOICES:
        add('qwen-'+speaker.lower(),name,QWEN_CUSTOM,gender,native,description,'official',folder/'previews'/('qwen-'+speaker.lower()+'.wav'),speaker=speaker)
    # Shared artificial references are explicitly not marketed as native speakers of these engines.
    for key in ('qwen3','voxcpm2','fish','chatterbox'):
        for speaker,name,gender in [('sohee','부드러운 여성','female'),('aiden','밝은 남성','male')]:
            add('shared-'+key+'-'+speaker,name,FAMILIES[key][1],gender,'한국어 샘플',('소희를 바탕으로 만든 공용 여성 목소리' if speaker=='sohee' else '에이든을 바탕으로 만든 공용 남성 목소리'),'shared',folder/'previews'/('qwen-'+speaker+'.wav'),SAMPLE_TEXT)
    for p in db.fetch_all("SELECT * FROM projects WHERE description='voice-library:v1' ORDER BY id DESC"):
        add('library-'+str(p['id']),p['name'],FAMILIES['zonos2'][1],'personal','사용자 녹음','등록한 원본 녹음','library',p['reference_path'] or '',p['reference_text'],voice_project_id=p['id'])
    return rows

def public_catalog(paths, db):
    return [{k:v for k,v in row.items() if k not in {'reference','reference_text','preview_path','speaker'}} for row in catalog(paths,db)]
