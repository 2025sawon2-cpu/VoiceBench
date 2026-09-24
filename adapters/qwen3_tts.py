from __future__ import annotations

import argparse
import os
import random
from pathlib import Path


DEFAULT_MODEL = "Qwen/Qwen3-TTS-12Hz-0.6B-Base"
LANGUAGES = {
    "ko": "Korean",
    "en": "English",
    "ja": "Japanese",
    "zh": "Chinese",
    "auto": "Auto",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="VoiceBench adapter for Qwen3-TTS voice cloning")
    parser.add_argument("--text")
    parser.add_argument("--reference")
    parser.add_argument("--ref-text", default="")
    parser.add_argument("--output")
    parser.add_argument("--seed", type=int, default=13)
    parser.add_argument("--language", default="ko")
    parser.add_argument("--model", default=os.environ.get("QWEN3_TTS_MODEL", DEFAULT_MODEL))
    parser.add_argument("--dtype", choices=("auto", "fp16", "fp32"), default="auto")
    parser.add_argument("--attention", choices=("eager", "sdpa"), default="eager")
    parser.add_argument("--download-only", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.download_only:
        from huggingface_hub import snapshot_download

        path = snapshot_download(args.model)
        print(f"Downloaded {args.model} to {path}")
        return

    if not all((args.text, args.reference, args.output)):
        raise SystemExit("--text, --reference, and --output are required")
    if not args.ref_text.strip():
        raise SystemExit(
            "--ref-text is required for the full-reference cloning path; "
            "x-vector-only mode is intentionally disabled because it reduces cloning quality"
        )

    os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
    import numpy as np
    import soundfile as sf
    import torch
    from qwen_tts import Qwen3TTSModel

    if not torch.cuda.is_available():
        raise RuntimeError("CUDA is unavailable in the Qwen3-TTS engine environment")

    random.seed(args.seed)
    np.random.seed(args.seed)
    torch.manual_seed(args.seed)
    torch.cuda.manual_seed_all(args.seed)

    capability = torch.cuda.get_device_capability(0)
    dtype_name = args.dtype
    if dtype_name == "auto":
        dtype_name = "fp32" if capability[0] < 7 else "fp16"
    dtype = torch.float32 if dtype_name == "fp32" else torch.float16
    print(
        f"Loading {args.model} on {torch.cuda.get_device_name(0)} "
        f"(compute capability {capability[0]}.{capability[1]}, {dtype_name.upper()}/{args.attention})"
    )
    model = Qwen3TTSModel.from_pretrained(
        args.model,
        device_map="cuda:0",
        dtype=dtype,
        attn_implementation=args.attention,
        low_cpu_mem_usage=True,
    )

    language = LANGUAGES.get(args.language.lower(), args.language)
    with torch.inference_mode():
        wavs, sample_rate = model.generate_voice_clone(
            text=args.text,
            language=language,
            ref_audio=str(Path(args.reference).resolve()),
            ref_text=args.ref_text.strip(),
            x_vector_only_mode=False,
        )

    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    sf.write(output, wavs[0], sample_rate, subtype="PCM_16")
    print(f"Wrote {output} ({sample_rate} Hz)")


if __name__ == "__main__":
    main()
