from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from voicebench.config import Paths
from voicebench.db import Database
from voicebench.service import Service

SLUG = "voxcpm2-experimental"
STYLE_PRESETS = {
    "bright": ("VoxCPM2 2B · 쇼츠 은은한 추천", "natural conversational voice, mostly neutral with a hint of warmth and a slight smile, restrained intonation, normal speaking pace"),
    "energetic": ("VoxCPM2 2B · 쇼츠 신나는 추천", "excited and enthusiastic, bright energetic voice, animated rising and falling intonation, punchy emphasis, brisk conversational delivery"),
}


def engine_spec(root: Path, model_path: Path, preset: str | None = None) -> dict:
    runtime = root / ".runtime" / "engines" / "voxcpm2"
    spec = {
        "slug": SLUG,
        "name": "VoxCPM2 2B · 실험용 음성 복제",
        "kind": "command",
        "version": f"voxcpm 2.0.3 · {model_path.name}",
        "enabled": True,
        "config": {
            "cwd": str(root),
            "environment": {
                "HF_HOME": str(root / ".runtime" / "cache" / "huggingface"),
                "HF_HUB_OFFLINE": "1", "HF_HUB_DISABLE_TELEMETRY": "1",
                "PYTHONUTF8": "1", "TOKENIZERS_PARALLELISM": "false",
            },
            # Use the same endpoint as Qwen: _ensure_worker unloads the previous
            # model before starting this one. The application queue is serial.
            "worker": {
                "command": [
                    str(runtime / "venv" / "Scripts" / "python.exe"),
                    str(root / "adapters" / "voxcpm2_worker.py"),
                    "--model-path", str(model_path), "--port", "8790",
                ],
                "endpoint": "http://127.0.0.1:8790/synthesize",
                "health_url": "http://127.0.0.1:8790/health",
                "model": "openbmb/VoxCPM2", "attention": "sdpa",
                "startup_timeout_seconds": 300,
                "log_path": str(root / ".runtime" / "logs" / "voxcpm2-worker.log"),
            },
            "requires_reference_text": True,
            "generation": {"cfg_value": 2.0, "inference_timesteps": 10},
            "timeout_seconds": 900,
        },
    }
    if preset:
        name, style = STYLE_PRESETS[preset]
        spec["slug"] = f"{SLUG}-{preset}"
        spec["name"] = name
        # User rejected the style-controlled outputs, including subtle emotion.
        # Keep historical engines/results, but do not re-enable on installation.
        spec["enabled"] = False
        spec["config"]["requires_reference_text"] = False
        spec["config"]["generation"].update(cloning_mode="controlled", style_prompt=style)
    return spec


def hq_engine_spec(root: Path, model_path: Path) -> dict:
    spec = engine_spec(root, model_path)
    spec["slug"] = "voxcpm2-hq-experimental"
    spec["name"] = "VoxCPM2 · HQ 20 steps"
    spec["config"]["generation"]["inference_timesteps"] = 20
    return spec


def main() -> None:
    paths = Paths.discover()
    runtime = paths.root / ".runtime" / "engines" / "voxcpm2"
    if not (runtime / "venv" / "Scripts" / "python.exe").is_file():
        raise SystemExit("Run install-voxcpm2.ps1 first: engine Python is missing")
    manifest = json.loads((runtime / "model.json").read_text(encoding="utf-8"))
    model_path = Path(manifest["path"])
    if not (model_path / "config.json").is_file():
        raise SystemExit("VoxCPM2 model is missing; run install-voxcpm2.ps1")
    paths.ensure()
    service = Service(paths, Database(paths.database))
    for preset in (None, *STYLE_PRESETS):
        engine = service.upsert_engine(engine_spec(paths.root, model_path, preset))
        print(f"Registered {engine['slug']} (id={engine['id']})")
    engine = service.upsert_engine(hq_engine_spec(paths.root, model_path))
    print(f"Registered {engine['slug']} (id={engine['id']})")


if __name__ == "__main__":
    main()
