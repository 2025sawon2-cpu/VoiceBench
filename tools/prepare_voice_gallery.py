"""Build audited Korean previews serially, recording every failure and cache hit."""
import argparse
import hashlib
import json
import sys
import time
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from voicebench.config import Paths
from voicebench.db import Database
from voicebench.voices import catalog, config_hash, SAMPLE_TEXT, QWEN_VOICES, ZONOS_VOICES
from voicebench.engines import run_engine, EngineRequest, shutdown_workers
from voicebench.quality import WhisperQualityVerifier
from voicebench.audio import inspect_wav
from tools.setup_voicebench import assert_idle

REQUIRED_IDS = (['qwen-' + v[0].lower() for v in QWEN_VOICES]
                + ['zonos-' + v[0].lower() for v in ZONOS_VOICES]
                + ['shared-' + family + '-' + speaker for family in
                   ('qwen3', 'qwen06', 'voxcpm2', 'fish', 'chatterbox') for speaker in ('sohee', 'aiden')])


def valid_cached_preview(voice):
    if voice['preview_kind'] != 'generated':
        return False
    try:
        path = Path(voice['preview_path'])
        info = json.loads(path.with_suffix('.json').read_text(encoding='utf-8'))
        quality = info.get('quality', {})
        return (info.get('sha256') == hashlib.sha256(path.read_bytes()).hexdigest()
                and info.get('text') == SAMPLE_TEXT and bool(quality.get('transcription'))
                and not quality.get('retry_recommended') and not inspect_wav(path)['hard_reject'])
    except (OSError, ValueError, EOFError, wave.Error):
        return False


def prepare(paths, db, ids=None, strict=False):
    assert_idle(paths.root)
    folder = paths.data / 'voice-gallery/previews'
    folder.mkdir(parents=True, exist_ok=True)
    report = []

    def record(item):
        report.append(item)
        target = folder.parent / 'preparation-report.json'
        temporary = target.with_suffix('.pending.json')
        temporary.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        temporary.replace(target)

    voices = catalog(paths, db)
    selected = set(ids or (REQUIRED_IDS if strict else [v['id'] for v in voices if v['kind'] not in ('personal', 'library')]))
    for missing in sorted(selected - {v['id'] for v in voices}):
        record(dict(id=missing, status='unavailable', reason='모델이 등록되지 않았거나 비활성화되어 있습니다.'))
    ordered = sorted((v for v in voices if v['id'] in selected), key=lambda v:
                     (0 if v.get('speaker') else 1 if v['kind'] == 'official' else 2, v['engine_slug'], v['id']))
    verifier = WhisperQualityVerifier(paths.root)
    previous_engine = None
    try:
        for original in ordered:
            voice_id = original['id']
            voice = next(v for v in catalog(paths, db) if v['id'] == voice_id)
            if valid_cached_preview(voice):
                record(dict(id=voice_id, status='ready', cached=True))
                print('CACHED', voice_id, flush=True)
                continue
            if not voice['available']:
                record(dict(id=voice_id, status='unavailable', reason='참조 음성이 아직 준비되지 않았습니다.'))
                continue
            try:
                # Clear only our workers before switching, including one-shot Chatterbox.
                if previous_engine != voice['engine_slug']:
                    shutdown_workers()
                    previous_engine = voice['engine_slug']
                if any(db.fetch_one(f"SELECT id FROM {table} WHERE status IN ('queued','running')")
                       for table in ('jobs', 'studio_requests')):
                    raise RuntimeError('Queue became busy; stop preparing previews.')
                engine = db.fetch_one('SELECT * FROM engines WHERE slug=?', (voice['engine_slug'],))
                config = json.loads(engine['config_json'])
                if voice.get('speaker'):
                    config['generation'] = {**config.get('generation', {}), 'speaker': voice['speaker']}
                preview = folder / (voice_id + '.wav')
                temporary = folder / (voice_id + '.pending.wav')
                print('GENERATING', voice_id, flush=True)
                started = time.perf_counter()
                quality = {}
                for seed in (13, 10):
                    run_engine({**engine, 'config_json': json.dumps(config)},
                               EngineRequest(SAMPLE_TEXT, Path(voice['reference']), voice['reference_text'], temporary, seed))
                    quality = verifier.inspect(SAMPLE_TEXT, temporary)
                    if quality.get('transcription') and not quality.get('retry_recommended') and not inspect_wav(temporary)['hard_reject']:
                        break
                else:
                    record(dict(id=voice_id, status='failed', reason='한국어 대본/파형 검사를 통과하지 못했습니다.'))
                    continue
                temporary.replace(preview)
                reference = Path(voice['reference'])
                info = dict(voice_id=voice_id, engine_slug=engine['slug'], config_hash=config_hash(engine),
                            seed=seed, text=SAMPLE_TEXT, quality=quality,
                            reference_sha256=hashlib.sha256(reference.read_bytes()).hexdigest() if reference.is_file() and not voice.get('speaker') else None,
                            sha256=hashlib.sha256(preview.read_bytes()).hexdigest(), seconds=round(time.perf_counter() - started, 2))
                preview.with_suffix('.json').write_text(json.dumps(info, ensure_ascii=False, indent=2), encoding='utf-8')
                record(dict(id=voice_id, status='ready', cached=False, seconds=info['seconds']))
            except Exception as error:
                shutdown_workers()
                record(dict(id=voice_id, status='failed', reason='합성 실패: ' + type(error).__name__))
                print('FAILED', voice_id, str(error), flush=True)
    finally:
        verifier.close()
        shutdown_workers()
    return bool(selected) and len(report) == len(selected) and all(item['status'] == 'ready' for item in report)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--ids', nargs='+')
    parser.add_argument('--strict', action='store_true', help='전체 22개 샘플이 없거나 실패하면 종료 코드 1')
    args = parser.parse_args()
    paths = Paths.discover()
    paths.ensure()
    return 0 if prepare(paths, Database(paths.database), args.ids, args.strict) else 1


if __name__ == '__main__':
    raise SystemExit(main())
