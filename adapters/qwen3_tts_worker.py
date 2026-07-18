from __future__ import annotations

import argparse
import json
import random
import threading
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any


DEFAULT_MODEL = "Qwen/Qwen3-TTS-12Hz-0.6B-Base"
LANGUAGES = {
    "ko": "Korean",
    "en": "English",
    "ja": "Japanese",
    "zh": "Chinese",
    "auto": "Auto",
}


class QwenWorkerServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address: tuple[str, int], model_name: str, dtype_name: str):
        import torch
        from qwen_tts import Qwen3TTSModel

        if not torch.cuda.is_available():
            raise RuntimeError("CUDA is unavailable in the Qwen3-TTS worker environment")
        capability = torch.cuda.get_device_capability(0)
        if dtype_name == "auto":
            dtype_name = "fp32" if capability[0] < 7 else "fp16"
        self.torch = torch
        self.dtype_name = dtype_name
        self.model_name = model_name
        self.device_name = torch.cuda.get_device_name(0)
        dtype = torch.float32 if dtype_name == "fp32" else torch.float16
        print(
            f"Loading {model_name} on {self.device_name} "
            f"(CC {capability[0]}.{capability[1]}, {dtype_name.upper()}/eager)",
            flush=True,
        )
        self.model = Qwen3TTSModel.from_pretrained(
            model_name,
            device_map="cuda:0",
            dtype=dtype,
            attn_implementation="eager",
            low_cpu_mem_usage=True,
        )
        self.prompt_cache: dict[tuple[str, str], Any] = {}
        self.generation_lock = threading.Lock()
        super().__init__(address, QwenWorkerHandler)

    def synthesize(self, payload: dict[str, Any]) -> dict[str, Any]:
        import numpy as np
        import soundfile as sf

        text = str(payload.get("text", "")).strip()
        reference = str(payload.get("reference", "")).strip()
        reference_text = str(payload.get("reference_text", "")).strip()
        output = Path(str(payload.get("output", ""))).resolve()
        language = LANGUAGES.get(str(payload.get("language", "ko")).lower(), "Korean")
        seed = int(payload.get("seed", 10))
        if not text or not reference or not reference_text:
            raise ValueError("text, reference, and reference_text are required")

        with self.generation_lock:
            random.seed(seed)
            np.random.seed(seed)
            self.torch.manual_seed(seed)
            self.torch.cuda.manual_seed_all(seed)
            cache_key = (str(Path(reference).resolve()), reference_text)
            prompt = self.prompt_cache.get(cache_key)
            if prompt is None:
                prompt = self.model.create_voice_clone_prompt(
                    ref_audio=cache_key[0],
                    ref_text=reference_text,
                    x_vector_only_mode=False,
                )
                self.prompt_cache[cache_key] = prompt
            with self.torch.inference_mode():
                wavs, sample_rate = self.model.generate_voice_clone(
                    text=text,
                    language=language,
                    voice_clone_prompt=prompt,
                )
            output.parent.mkdir(parents=True, exist_ok=True)
            sf.write(output, wavs[0], sample_rate, subtype="PCM_16")
        return {
            "output": str(output),
            "sample_rate": sample_rate,
            "prompt_cache_entries": len(self.prompt_cache),
        }


class QwenWorkerHandler(BaseHTTPRequestHandler):
    server: QwenWorkerServer

    def log_message(self, format: str, *args: Any) -> None:
        print(f"[Qwen worker] {format % args}", flush=True)

    def do_GET(self) -> None:
        if self.path != "/health":
            return self.send_json({"error": "Not found"}, HTTPStatus.NOT_FOUND)
        self.send_json(
            {
                "status": "ready",
                "model": self.server.model_name,
                "dtype": self.server.dtype_name,
                "gpu": self.server.device_name,
                "prompt_cache_entries": len(self.server.prompt_cache),
            }
        )

    def do_POST(self) -> None:
        try:
            if self.path == "/synthesize":
                length = int(self.headers.get("Content-Length", "0"))
                if length <= 0 or length > 1024 * 1024:
                    raise ValueError("Invalid request body size")
                payload = json.loads(self.rfile.read(length).decode("utf-8"))
                return self.send_json(self.server.synthesize(payload))
            if self.path == "/shutdown":
                self.send_json({"status": "stopping"})
                threading.Thread(target=self.server.shutdown, daemon=True).start()
                return
            self.send_json({"error": "Not found"}, HTTPStatus.NOT_FOUND)
        except Exception as error:
            self.send_json({"error": f"{type(error).__name__}: {error}"}, HTTPStatus.INTERNAL_SERVER_ERROR)

    def send_json(self, payload: Any, status: int = HTTPStatus.OK) -> None:
        content = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)


def main() -> None:
    parser = argparse.ArgumentParser(description="Persistent local Qwen3-TTS worker")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8790)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--dtype", choices=("auto", "fp16", "fp32"), default="auto")
    args = parser.parse_args()
    server = QwenWorkerServer((args.host, args.port), args.model, args.dtype)
    print(f"Qwen3-TTS worker ready at http://{args.host}:{args.port}", flush=True)
    try:
        server.serve_forever(poll_interval=0.25)
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
