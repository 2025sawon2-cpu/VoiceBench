from __future__ import annotations

import argparse
import json
import math
import random
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any


MODEL = "openbmb/VoxCPM2"


def validate_request(payload: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("Request must be an object")
    for key in ("text", "reference", "output"):
        if not isinstance(payload.get(key), str) or not payload[key].strip():
            raise ValueError(f"{key} is required")
    if not Path(payload["reference"]).is_file():
        raise ValueError("Reference audio does not exist")
    generation = payload.get("generation") or {}
    if not isinstance(generation, dict) or set(generation) - {
        "cfg_value", "inference_timesteps", "cloning_mode", "style_prompt"
    }:
        raise ValueError("Unsupported generation parameters")
    mode = generation.get("cloning_mode", "continuation")
    style = generation.get("style_prompt", "")
    if mode not in ("continuation", "controlled"):
        raise ValueError("cloning_mode must be continuation or controlled")
    if not isinstance(style, str) or len(style) > 400 or any(c in style for c in "()\n\r"):
        raise ValueError("style_prompt must be at most 400 characters without parentheses or newlines")
    if mode == "continuation":
        if not isinstance(payload.get("reference_text"), str) or not payload["reference_text"].strip():
            raise ValueError("reference_text is required for continuation cloning")
        if style.strip():
            raise ValueError("style_prompt requires controlled cloning")
    cfg = float(generation.get("cfg_value", 2.0))
    steps = generation.get("inference_timesteps", 10)
    if not math.isfinite(cfg) or not 0 < cfg <= 10:
        raise ValueError("cfg_value must be in (0, 10]")
    if isinstance(steps, bool) or not isinstance(steps, int) or not 1 <= steps <= 100:
        raise ValueError("inference_timesteps must be an integer from 1 to 100")
    return {"cfg_value": cfg, "inference_timesteps": steps,
            "cloning_mode": mode, "style_prompt": style.strip()}


def generation_arguments(payload: dict[str, Any], generation: dict[str, Any]) -> dict[str, Any]:
    """Keep performance directions out of the user's stored/spoken script."""
    text = payload["text"].strip()
    reference = str(Path(payload["reference"]).resolve())
    options = {key: generation[key] for key in ("cfg_value", "inference_timesteps")}
    if generation["cloning_mode"] == "controlled":
        if generation["style_prompt"]:
            text = f"({generation['style_prompt']}){text}"
        options["reference_wav_path"] = reference
    else:
        options.update(prompt_wav_path=reference, prompt_text=payload["reference_text"].strip())
    return {"text": text, "normalize": False, "denoise": False, "retry_badcase": False, **options}


class VoxCPMWorkerServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address: tuple[str, int], model_path: str):
        import torch
        from voxcpm import VoxCPM

        if not torch.cuda.is_available():
            raise RuntimeError("CUDA is unavailable in the VoxCPM2 environment")
        self.torch = torch
        started = time.perf_counter()
        # Windows baseline: native SDPA without torch.compile/Triton.
        # Keep the official model precision and diffusion step count.
        self.model = VoxCPM.from_pretrained(
            model_path, load_denoiser=False, optimize=False, device="cuda",
            local_files_only=True,
        )
        torch.cuda.synchronize()
        self.load_seconds = time.perf_counter() - started
        self.generation_lock = threading.Lock()
        self.request_count = 0
        self.model_path = model_path
        super().__init__(address, VoxCPMWorkerHandler)

    def health(self) -> dict[str, Any]:
        return {
            "status": "ready", "model": MODEL, "attention": "sdpa",
            "dtype": self.model.tts_model.config.dtype,
            "gpu": self.torch.cuda.get_device_name(0),
            "load_seconds": round(self.load_seconds, 4),
            "request_count": self.request_count,
        }

    def synthesize(self, payload: dict[str, Any]) -> dict[str, Any]:
        import numpy as np
        import soundfile as sf

        generation = validate_request(payload)
        seed = int(payload.get("seed", 13))
        output = Path(payload["output"]).resolve()
        with self.generation_lock:
            # PyPI 2.0.3 does not expose the newer generate(seed=...) API.
            random.seed(seed)
            np.random.seed(seed)
            self.torch.manual_seed(seed)
            self.torch.cuda.manual_seed_all(seed)
            self.torch.cuda.synchronize()
            self.torch.cuda.reset_peak_memory_stats()
            started = time.perf_counter()
            with self.torch.inference_mode():
                waveform = self.model.generate(
                    **generation_arguments(payload, generation),
                )
            self.torch.cuda.synchronize()
            generate_seconds = time.perf_counter() - started
            waveform = np.asarray(waveform, dtype=np.float32).squeeze()
            if waveform.ndim != 1 or not waveform.size or not np.isfinite(waveform).all():
                raise RuntimeError("VoxCPM2 returned an invalid waveform")
            sample_rate = self.model.tts_model.sample_rate
            duration = len(waveform) / sample_rate
            output.parent.mkdir(parents=True, exist_ok=True)
            sf.write(output, waveform, sample_rate, subtype="PCM_16")
            self.request_count += 1
            return {
                "output": str(output), "sample_rate": sample_rate, "seed": seed,
                "model": MODEL, "model_path": self.model_path,
                "dtype": self.model.tts_model.config.dtype, "attention": "sdpa",
                "compile": False, "cloning_mode": generation["cloning_mode"],
                "generation": {**generation, "retry_badcase": False},
                "load_seconds": round(self.load_seconds, 4),
                "generate_seconds": round(generate_seconds, 4),
                "audio_seconds": round(duration, 4),
                "realtime_factor": round(generate_seconds / duration, 4),
                "peak_allocated_mb": round(self.torch.cuda.max_memory_allocated() / 2**20, 1),
                "peak_reserved_mb": round(self.torch.cuda.max_memory_reserved() / 2**20, 1),
                "request_index": self.request_count,
            }


class VoxCPMWorkerHandler(BaseHTTPRequestHandler):
    server: VoxCPMWorkerServer

    def log_message(self, format: str, *args: Any) -> None:
        print(f"[VoxCPM2] {format % args}", flush=True)

    def send_json(self, payload: Any, status: int = 200) -> None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self) -> None:
        if self.path == "/health":
            self.send_json(self.server.health())
        else:
            self.send_json({"error": "Not found"}, 404)

    def do_POST(self) -> None:
        try:
            if self.path == "/shutdown":
                self.send_json({"status": "stopping"})
                threading.Thread(target=self.server.shutdown, daemon=True).start()
            elif self.path == "/synthesize":
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= 1024 * 1024:
                    raise ValueError("Invalid request body size")
                payload = json.loads(self.rfile.read(length).decode("utf-8"))
                self.send_json(self.server.synthesize(payload))
            else:
                self.send_json({"error": "Not found"}, 404)
        except Exception as error:
            self.send_json({"error": f"{type(error).__name__}: {error}"}, 500)


def main() -> None:
    parser = argparse.ArgumentParser(description="Persistent local VoxCPM2 worker")
    parser.add_argument("--port", type=int, default=8790)
    parser.add_argument("--model-path", required=True)
    args = parser.parse_args()
    server = VoxCPMWorkerServer(("127.0.0.1", args.port), args.model_path)
    print(f"VoxCPM2 ready on 127.0.0.1:{args.port}", flush=True)
    try:
        server.serve_forever(poll_interval=0.25)
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
