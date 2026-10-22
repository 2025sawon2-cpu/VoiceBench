from __future__ import annotations

import argparse
import json
import random
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description="VoiceBench adapter for Chatterbox Multilingual V3")
    parser.add_argument("--text", required=True)
    parser.add_argument("--reference", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--seed", type=int, default=13)
    parser.add_argument("--language", default="ko")
    args = parser.parse_args()

    if not args.reference.is_file():
        parser.error(f"Reference audio is missing: {args.reference}")
    language = {"korean": "ko", "english": "en"}.get(args.language.lower(), args.language.lower())

    import numpy as np
    import soundfile as sf
    import torch
    from chatterbox.mtl_tts import ChatterboxMultilingualTTS

    if not torch.cuda.is_available():
        raise RuntimeError("CUDA is unavailable in the Chatterbox V3 engine environment")
    random.seed(args.seed)
    np.random.seed(args.seed)
    torch.manual_seed(args.seed)
    torch.cuda.manual_seed_all(args.seed)

    manifest = Path(__file__).resolve().parents[1] / '.runtime/engines/chatterbox-v3/model.json'
    if manifest.is_file():
        model = ChatterboxMultilingualTTS.from_local(
            json.loads(manifest.read_text(encoding='utf-8'))['path'], device='cuda', t3_model='v3')
    else:
        model = ChatterboxMultilingualTTS.from_pretrained(device="cuda", t3_model="v3")
    with torch.inference_mode():
        audio = model.generate(
            args.text,
            language_id=language,
            audio_prompt_path=str(args.reference.resolve()),
        )
    waveform = audio.detach().float().cpu().squeeze().numpy()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    sf.write(args.output, waveform, model.sr, subtype="PCM_16")
    print(f"Wrote {args.output} ({model.sr} Hz)")


if __name__ == "__main__":
    main()
