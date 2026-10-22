"""Download all inference assets in the appropriate isolated engine environment."""
import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CHATTER_REVISION = '5bb1f6ee58e50c3b8d408bc82a6d3740c2db6e18'


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('engine', choices=['qwen3-tts', 'chatterbox-v3'])
    args = parser.parse_args()
    from huggingface_hub import snapshot_download
    cache = str(ROOT / '.runtime/cache/huggingface/hub')
    if args.engine == 'qwen3-tts':
        for repo, revision in [
            ('Qwen/Qwen3-TTS-12Hz-1.7B-Base', 'fd4b254389122332181a7c3db7f27e918eec64e3'),
            ('Qwen/Qwen3-TTS-12Hz-0.6B-Base', '5d83992436eae1d760afd27aff78a71d676296fc'),
            ('Qwen/Qwen3-TTS-Tokenizer-12Hz', 'main'),
            ('openai/whisper-base', 'main'),
        ]:
            snapshot_download(repo, revision=revision, cache_dir=cache, max_workers=3)
    else:
        path = snapshot_download('ResembleAI/chatterbox', revision=CHATTER_REVISION,
            cache_dir=cache, max_workers=3,
            allow_patterns=['ve.pt', 't3_mtl23ls_v3.safetensors', 's3gen.pt',
                            'grapheme_mtl_merged_expanded_v1.json', 'conds.pt', 'Cangjie5_TC.json'])
        (ROOT / '.runtime/engines/chatterbox-v3/model.json').write_text(
            json.dumps({'path': path, 'revision': CHATTER_REVISION}), encoding='utf-8')


if __name__ == '__main__':
    main()
