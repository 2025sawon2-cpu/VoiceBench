"""Download the pinned public model; personal reference audio stays local."""
from __future__ import annotations

import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REVISION = "32279effe8c19989596f05d353d1447f51d9e915"


def main() -> None:
    os.environ["HF_HOME"] = str(ROOT / ".runtime" / "cache" / "huggingface")
    os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"
    os.environ["HF_HUB_DISABLE_SYMLINKS_WARNING"] = "1"
    from huggingface_hub import snapshot_download

    model_path = snapshot_download(
        "openbmb/VoxCPM2", revision=REVISION,
        allow_patterns=["*.json", "*.safetensors", "audiovae.pth", "*.py", "README.md"],
    )
    manifest = ROOT / ".runtime" / "engines" / "voxcpm2" / "model.json"
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_text(
        json.dumps({"model": "openbmb/VoxCPM2", "revision": REVISION, "path": model_path}, indent=2),
        encoding="utf-8",
    )
    print(f"VoxCPM2 downloaded: {model_path}")


if __name__ == "__main__":
    main()
