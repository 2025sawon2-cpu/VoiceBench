"""S2-Pro full BF16 weights with a bounded cache, using official inference code."""
from __future__ import annotations

import argparse
import math
import random
import time
from pathlib import Path

from local_tts_worker import serve, validate_payload

MODEL = "fishaudio/s2-pro"


def generation_options(payload):
    validate_payload(payload, transcript=True)
    supplied = payload.get("generation", {})
    defaults = {"temperature": 0.8, "top_p": 0.8, "top_k": 30, "max_new_tokens": 2048}
    if set(supplied) - defaults.keys():
        raise ValueError("Unsupported Fish generation option")
    options = defaults | supplied
    for key in ("temperature", "top_p"):
        if not math.isfinite(float(options[key])) or not 0 < options[key] <= 1:
            raise ValueError(f"{key} must be in (0, 1]")
    for key, maximum in (("top_k", 1000), ("max_new_tokens", 2048)):
        if type(options[key]) is not int or not 1 <= options[key] <= maximum:
            raise ValueError(f"Invalid {key}")
    return options


class FishBackend:
    def __init__(self, model_path):
        import torch
        from fish_speech.models.text2semantic.llama import DualARTransformer, precompute_freqs_cis
        from fish_speech.models.dac.inference import load_model

        self.torch = torch
        if not torch.cuda.is_available():
            raise RuntimeError("Fish requires CUDA in this preset")
        torch.set_num_threads(min(8, torch.get_num_threads()))
        start = time.perf_counter()
        self.path = Path(model_path)
        # Official max_length override: bounds KV/attention buffers, not weights.
        # 4096 tokens leaves >=2048 for generation after the short cloned prompt.
        # Checkpoint loading uses assign=True. A meta skeleton avoids randomizing
        # billions of throwaway weights and doubling host RAM during startup.
        with torch.device("meta"):
            self.model = DualARTransformer.from_pretrained(
                self.path, load_weights=True, max_length=4096,
            )
        if any(p.is_meta for p in self.model.parameters()):
            raise RuntimeError("Fish checkpoint is incomplete; missing model parameters")
        config = self.model.config
        # These three non-persistent buffers are computed, not checkpoint weights.
        self.model.freqs_cis = precompute_freqs_cis(config.max_seq_len, config.head_dim, config.rope_base)
        self.model.fast_freqs_cis = precompute_freqs_cis(config.num_codebooks, config.fast_head_dim, config.rope_base)
        self.model.causal_mask = torch.tril(torch.ones(config.max_seq_len, config.max_seq_len, dtype=torch.bool))
        self.model.to(dtype=torch.bfloat16).eval()
        self.model._cache_setup_done = False
        self.codec = load_model("modded_dac_vq", self.path / "codec.pth", "cuda")
        torch.cuda.synchronize()
        self.load_seconds = time.perf_counter() - start
        self.count = 0

    def health(self):
        return {"status": "ready", "model": MODEL, "attention": "sdpa-math",
                "dtype": "bfloat16", "context_tokens": 4096, "request_count": self.count}

    def close(self):
        pass

    def synthesize(self, payload):
        import numpy as np
        import soundfile as sf
        from fish_speech.models.text2semantic.inference import generate_long, decode_one_token_ar
        from fish_speech.utils import set_seed

        options = generation_options(payload)
        seed = int(payload.get("seed", 13))
        torch = self.torch
        random.seed(seed)
        np.random.seed(seed)
        set_seed(seed)
        torch.cuda.reset_peak_memory_stats()
        torch.cuda.synchronize()
        start = time.perf_counter()
        with torch.inference_mode():
            # Keep the codec in FP32, with the same BF16 autocast as official WebUI.
            # SoundFile avoids torchaudio FFmpeg ABI mismatches on Windows.
            import torchaudio
            # The codec's long waveform activations otherwise push the full BF16
            # model over 16GB. Stage the two networks without quantizing either.
            self.model.to("cpu")
            torch.cuda.empty_cache()
            self.codec.to("cuda")
            wav, rate = sf.read(payload["reference"], dtype="float32", always_2d=True)
            wav = torch.from_numpy(wav.mean(axis=1)).to("cuda")
            wav = torchaudio.functional.resample(wav, rate, self.codec.sample_rate)
            with torch.autocast("cuda", dtype=torch.bfloat16):
                codes, lengths = self.codec.encode(wav[None, None], torch.tensor([len(wav)], device="cuda"))
            prompt = codes[0, :, :lengths[0]]
            del wav, codes
            self.codec.to("cpu")
            torch.cuda.empty_cache()
            self.model.to("cuda")
            code_segments = []
            # One conversational turn preserves cadence; no emotion tags or rewriting.
            for item in generate_long(
                model=self.model, device="cuda", decode_one_token=decode_one_token_ar,
                text=payload["text"], prompt_text=[payload["reference_text"]],
                prompt_tokens=[prompt], compile=False, chunk_length=1000, **options,
            ):
                if item.action == "sample":
                    if item.codes.shape[1] >= options["max_new_tokens"] - 1:
                        raise RuntimeError("Fish hit its generation limit; refusing truncated speech")
                    code_segments.append(item.codes.cpu())
            self.model.to("cpu")
            torch.cuda.empty_cache()
            self.codec.to("cuda")
            segments = []
            for codes in code_segments:
                with torch.autocast("cuda", dtype=torch.bfloat16):
                    audio = self.codec.from_indices(codes.to("cuda")[None])[0, 0]
                segments.append(audio.float().cpu().numpy())
        torch.cuda.synchronize()
        elapsed = time.perf_counter() - start
        if not segments:
            raise RuntimeError("Fish produced no audio")
        audio = np.concatenate(segments)
        if not audio.size or not np.isfinite(audio).all():
            raise RuntimeError("Invalid Fish waveform")
        output = Path(payload["output"]).resolve()
        output.parent.mkdir(parents=True, exist_ok=True)
        sf.write(output, audio, self.codec.sample_rate, subtype="PCM_16")
        self.count += 1
        duration = len(audio) / self.codec.sample_rate
        return {"model": MODEL, "model_path": str(self.path), "output": str(output),
                "seed": seed, "generation": options, "dtype": "bfloat16", "codec_dtype": "float32/bfloat16-autocast",
                "attention": "sdpa-math", "compile": False, "context_tokens": 4096,
                "memory_strategy": "stage-model-and-codec",
                "sample_rate": self.codec.sample_rate, "audio_seconds": duration,
                "load_seconds": self.load_seconds, "generate_seconds": elapsed,
                "realtime_factor": elapsed / duration,
                "peak_allocated_mb": torch.cuda.max_memory_allocated() / 2**20,
                "peak_reserved_mb": torch.cuda.max_memory_reserved() / 2**20,
                "request_index": self.count}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--model-path", required=True)
    parser.add_argument("--port", type=int, default=8790)
    args = parser.parse_args()
    serve(FishBackend(args.model_path), args.port)
