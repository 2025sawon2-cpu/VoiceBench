"""Pinned public artifacts; selected reference audio never leaves this machine."""
from __future__ import annotations

import argparse
import hashlib
import json
import time
import urllib.request
import zipfile
from pathlib import Path

MODELS = {
    "fish-speech": ("fishaudio/s2-pro", "1de9996b6be38b745688de084d87a5633f714e4e",
                    ["*.json", "*.safetensors", "*.pth", "*.jinja", "LICENSE.md"]),
    "zonos2": ("Zyphra/ZONOS2-GGUF", "75c877ec8ac86dda42bfc0e9968c87f29e10ef57",
               ["zonos2-q8_0.gguf", "dac.gguf", "spk-encoder.gguf", "README.md"]),
}
ZONOS_RELEASE = "v0.5.1"


def main():
    from huggingface_hub import snapshot_download
    parser = argparse.ArgumentParser()
    parser.add_argument("engine", choices=MODELS)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    runtime = root / ".runtime/engines" / args.engine
    runtime.mkdir(parents=True, exist_ok=True)
    repo, revision, patterns = MODELS[args.engine]
    for attempt in range(3):
        try:
            path = snapshot_download(repo, revision=revision, allow_patterns=patterns,
                cache_dir=str(root / ".runtime/cache/huggingface/hub"), max_workers=3)
            break
        except Exception:
            if attempt == 2:
                raise
            time.sleep(2)
    (runtime / "model.json").write_text(json.dumps(
        {"repo": repo, "revision": revision, "path": path}, indent=2), encoding="utf-8")
    if args.engine == "zonos2":
        speaker_repo = "marksverdhei/Qwen3-Voice-Embedding-12Hz-1.7B"
        speaker_revision = "7577f61c42737fc8064bba773e2a18602df92803"
        speaker_path = snapshot_download(speaker_repo, revision=speaker_revision,
            local_dir=str(runtime / "speaker"), max_workers=1)
        (runtime / "speaker.json").write_text(json.dumps({"repo": speaker_repo,
            "revision": speaker_revision, "path": speaker_path}, indent=2), encoding="utf-8")
        url = f"https://api.github.com/repos/Zyphra/zonos2.cpp/releases/tags/{ZONOS_RELEASE}"
        with urllib.request.urlopen(url) as response:
            release = json.load(response)
        asset = next(a for a in release["assets"] if a["name"] == "zonos2-windows-x64-vulkan.zip")
        archive = runtime / f"zonos2-{ZONOS_RELEASE}.zip"
        digest = asset.get("digest", "")
        valid = archive.is_file() and archive.stat().st_size == asset["size"]
        if valid and digest.startswith("sha256:"):
            valid = hashlib.sha256(archive.read_bytes()).hexdigest() == digest.split(":", 1)[1]
        if not valid:
            urllib.request.urlretrieve(asset["browser_download_url"], archive)
        if digest.startswith("sha256:") and hashlib.sha256(archive.read_bytes()).hexdigest() != digest[7:]:
            raise RuntimeError("ZONOS2 archive checksum mismatch")
        with zipfile.ZipFile(archive) as z:
            destination = (runtime / "bin").resolve()
            for member in z.infolist():
                (destination / member.filename).resolve().relative_to(destination)
            z.extractall(destination)
        (runtime / "binary.json").write_text(json.dumps({
            "release": ZONOS_RELEASE, "url": asset["browser_download_url"],
            "sha256": hashlib.sha256(archive.read_bytes()).hexdigest()}, indent=2), encoding="utf-8")
    print(f"{args.engine} artifacts ready: {path}")


if __name__ == "__main__":
    main()
