from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def emit(payload: dict) -> None:
    sys.stdout.write(json.dumps(payload, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def main() -> int:
    parser = argparse.ArgumentParser(description="Persistent local Whisper quality worker")
    parser.add_argument("--model", required=True)
    args = parser.parse_args()

    import soundfile as sf
    import torch
    from scipy.signal import resample_poly
    from transformers import WhisperForConditionalGeneration, WhisperProcessor

    torch.set_num_threads(max(1, min(6, torch.get_num_threads())))
    model_path = Path(args.model).resolve()
    processor = WhisperProcessor.from_pretrained(str(model_path), local_files_only=True)
    model = WhisperForConditionalGeneration.from_pretrained(
        str(model_path), local_files_only=True
    ).to("cpu").eval()
    emit({"status": "ready", "model": model_path.name, "device": "cpu"})

    for line in sys.stdin:
        try:
            payload = json.loads(line)
            if payload.get("command") == "shutdown":
                emit({"status": "stopped"})
                return 0
            audio, sample_rate = sf.read(str(Path(payload["audio"])), dtype="float32")
            if getattr(audio, "ndim", 1) > 1:
                audio = audio.mean(axis=1)
            if sample_rate != 16000:
                audio = resample_poly(audio, 16000, sample_rate)
            inputs = processor(
                audio,
                sampling_rate=16000,
                return_tensors="pt",
                return_attention_mask=True,
            )
            with torch.inference_mode():
                generated = model.generate(
                    inputs.input_features,
                    attention_mask=inputs.attention_mask,
                    language=payload.get("language", "ko"),
                    task="transcribe",
                    max_new_tokens=256,
                )
            transcription = processor.batch_decode(
                generated, skip_special_tokens=True
            )[0].strip()
            emit({"status": "ok", "transcription": transcription})
        except Exception as error:
            emit({"status": "error", "error": f"{type(error).__name__}: {error}"})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
