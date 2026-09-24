from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from voicebench.config import Paths
from voicebench.db import Database
from voicebench.service import Service


def engine_spec(root, engine, manifest):
    runtime = root / ".runtime" / "engines" / engine
    fish = engine == "fish-speech"
    command = [str(runtime / "venv/Scripts/python.exe"),
               str(root / "adapters" / ("fish_speech_worker.py" if fish else "zonos2_worker.py")),
               "--model-path", manifest["path"], "--port", "8790"]
    if not fish:
        command += ["--binary", str(runtime / "build-cuda/zonos2-server.exe"), "--backend", "cuda"]
    return {
        "slug": "fish-s2-pro-experimental" if fish else "zonos2-q8-experimental",
        "name": "Fish S2-Pro · BF16 · 연구/비상업 평가" if fish else "ZONOS2 · CUDA/Q8 · 음성 복제",
        "kind": "command", "enabled": True,
        "version": manifest["revision"],
        "config": {
            "cwd": str(root), "requires_reference_text": fish,
            "environment": {
                "PYTHONUTF8": "1", "HF_HUB_OFFLINE": "1", "HF_HUB_DISABLE_TELEMETRY": "1",
                "TOKENIZERS_PARALLELISM": "false", "PYTHONPATH": str(runtime / "source") if fish else str(root),
                "HF_HOME": str(root / ".runtime/cache/huggingface"),
            },
            "generation": ({"temperature": 0.8, "top_p": 0.8, "top_k": 30, "max_new_tokens": 2048}
                           if fish else {"temperature": 0.9, "top_p": 0.95, "min_p": 0.05}),
            "timeout_seconds": 1500,
            "worker": {
                "command": command, "endpoint": "http://127.0.0.1:8790/synthesize",
                "health_url": "http://127.0.0.1:8790/health",
                "model": "fishaudio/s2-pro" if fish else "Zyphra/ZONOS2-Q8_0",
                "attention": "sdpa-math" if fish else "cuda", "startup_timeout_seconds": 360,
                "log_path": str(root / ".runtime/logs" / f"{engine}-worker.log"),
            },
        },
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("engine", choices=["fish-speech", "zonos2", "all"], default="all", nargs="?")
    args = parser.parse_args()
    paths = Paths.discover()
    service = Service(paths, Database(paths.database))
    for engine in (["fish-speech", "zonos2"] if args.engine == "all" else [args.engine]):
        runtime = paths.root / ".runtime/engines" / engine
        manifest = json.loads((runtime / "model.json").read_text(encoding="utf-8"))
        required = ("config.json", "codec.pth", "model.safetensors.index.json") if engine == "fish-speech" else ("zonos2-q8_0.gguf", "dac.gguf", "spk-encoder.gguf")
        if not all((Path(manifest["path"]) / f).is_file() for f in required):
            raise SystemExit(f"Missing weights for {engine}")
        if engine == "zonos2" and not all((runtime / f).is_file() for f in (
            "build-cuda/zonos2-server.exe", "speaker/model.safetensors", "speaker/config.json",
        )):
            raise SystemExit("Missing ZONOS2 binary or official speaker encoder")
        spec = engine_spec(paths.root, engine, manifest)
        if not Path(spec["config"]["worker"]["command"][0]).is_file():
            raise SystemExit(f"Missing Python for {engine}")
        registered = service.upsert_engine(spec)
        print(f"Registered {registered['slug']} (id={registered['id']})")


if __name__ == "__main__":
    main()
