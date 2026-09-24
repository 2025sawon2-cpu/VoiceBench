"""VoiceBench bridge to the locally built ZONOS2 CUDA server."""
from __future__ import annotations

import argparse
import base64
import json
import math
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

from local_tts_worker import serve, validate_payload

MODEL = "Zyphra/ZONOS2-Q8_0"


def generation_request(payload, embedding_npy):
    validate_payload(payload)
    options = payload.get("generation", {})
    if set(options) - {"temperature", "top_p", "min_p"}:
        raise ValueError("Unsupported ZONOS2 generation option")
    for key, value in options.items():
        if not isinstance(value, (int, float)) or not math.isfinite(value) or not 0 < value <= (2 if key == "temperature" else 1):
            raise ValueError(f"Invalid {key}")
    return {
        "text": payload["text"], "language": "ko", "text_normalization": False,
        "speaker_embedding_base64": base64.b64encode(embedding_npy).decode("ascii"),
        "seed": int(payload.get("seed", 13)),
        "accurate_mode": True, "emotion_enabled": False, "speaking_rate_enabled": False,
        "quality_enabled": True, "temperature": 1.15, "topk": 106, "top_p": 0.0,
        "min_p": 0.18, "repetition_penalty": 1.2, "repetition_window": 50,
        "repetition_codebooks": 8, "max_tokens": 4096, "stream": False, "format": "wav",
        **options,
    }


class ZonosBackend:
    def __init__(self, model_path, binary, native_port=8791, backend="cuda"):
        import socket
        # Never attach to or terminate an unrelated process occupying this port.
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", native_port))
        self.path = Path(model_path)
        self.url = f"http://127.0.0.1:{native_port}"
        self.count = 0
        self.backend = backend
        start = time.perf_counter()
        from zonos2_speaker import SpeakerEncoder
        runtime = Path(binary).resolve().parents[1]
        self.speaker = SpeakerEncoder(runtime / "speaker")
        # C++ argv/fopen handles non-ASCII absolute paths inconsistently. Also
        # exclude spk-encoder.gguf to prevent auto-loading the incompatible CPU
        # implementation. Hard links keep the original downloaded files intact.
        native_models = runtime / "native-models"
        native_models.mkdir(exist_ok=True)
        for name in ("zonos2-q8_0.gguf", "dac.gguf"):
            destination = native_models / name
            if not destination.exists():
                os.link(self.path / name, destination)
            elif not os.path.samefile(self.path / name, destination):
                raise RuntimeError(f"Unexpected existing native model: {destination}")
        if (native_models / "spk-encoder.gguf").exists():
            raise RuntimeError("Native CPU speaker encoder must not be auto-loaded")
        environment = os.environ.copy()
        cuda_bin = runtime.parents[1] / "toolchains/cuda-12.9/bin"
        environment["PATH"] = str(cuda_bin) + os.pathsep + environment.get("PATH", "")
        self.process = subprocess.Popen([
            str(binary), "zonos2-q8_0.gguf",
            "--dac", "dac.gguf",
            "--host", "127.0.0.1", "--port", str(native_port),
            "--gpu", "--dac-gpu", "--dac-threads", "1", "--batch", "1", "--max", "4096",
        ], cwd=native_models, stdout=sys.stdout, stderr=subprocess.STDOUT, env=environment,
           creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        try:
            while time.perf_counter() - start < 240:
                if self.process.poll() is not None:
                    raise RuntimeError(f"ZONOS2 exited during startup: {self.process.returncode}")
                try:
                    with urllib.request.urlopen(self.url + "/health", timeout=1) as response:
                        if json.load(response).get("status") == "ok":
                            break
                except OSError:
                    pass
                time.sleep(0.25)
            else:
                raise TimeoutError("ZONOS2 startup timed out")
        except BaseException:
            self.close()
            raise
        self.load_seconds = time.perf_counter() - start

    def health(self):
        return {"status": "ready" if self.process.poll() is None else "failed",
                "model": MODEL, "attention": self.backend, "quantization": "Q8_0",
                "native_pid": self.process.pid, "request_count": self.count}

    def close(self):
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=5)

    def synthesize(self, payload):
        import numpy as np
        import soundfile as sf
        import io

        validate_payload(payload)
        start = time.perf_counter()
        body = generation_request(payload, self.speaker.encode(Path(payload["reference"])))
        request = urllib.request.Request(self.url + "/tts/generate",
            data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(request, timeout=1200) as response:
            raw = response.read()
        elapsed = time.perf_counter() - start
        audio, rate = sf.read(io.BytesIO(raw), dtype="float32")
        if audio.ndim != 1 or not audio.size or not np.isfinite(audio).all():
            raise RuntimeError("Invalid ZONOS2 waveform")
        output = Path(payload["output"]).resolve()
        output.parent.mkdir(parents=True, exist_ok=True)
        sf.write(output, audio, rate, subtype="PCM_16")
        duration = len(audio) / rate
        self.count += 1
        # Never persist the base64 reference in job metrics/manifests.
        settings = {k: v for k, v in body.items() if k not in ("speaker_embedding_base64", "text")}
        return {"model": MODEL, "model_path": str(self.path), "output": str(output),
                "seed": body["seed"], "generation": settings, "backend": self.backend,
                "quantization": "Q8_0", "sample_rate": rate, "audio_seconds": duration,
                "speaker_encoder": "official-ecapa-pytorch-fp32-cpu",
                "load_seconds": self.load_seconds, "generate_seconds": elapsed,
                "realtime_factor": elapsed / duration, "request_index": self.count}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--model-path", required=True)
    parser.add_argument("--binary", required=True)
    parser.add_argument("--port", type=int, default=8790)
    parser.add_argument("--native-port", type=int, default=8791)
    parser.add_argument("--backend", choices=("cuda", "vulkan"), default="cuda")
    args = parser.parse_args()
    serve(ZonosBackend(args.model_path, args.binary, args.native_port, args.backend), args.port)
