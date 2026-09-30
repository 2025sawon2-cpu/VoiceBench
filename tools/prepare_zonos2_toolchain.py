"""Prepare portable MSVC/SDK and CUDA under VoiceBench, without a VS install."""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import urllib.request
import zipfile
from pathlib import Path

MSVC_REV = "49b58850aa5e5c1e65f090d4651ee76607883e11"
MSVC_SHA = "1791cccca594ff083ce8d3e0a139e6156d2c312e24332a909e608010f0230a51"


def sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def main():
    root = Path(__file__).resolve().parents[1]
    target = (root / ".runtime/toolchains").resolve()
    target.mkdir(parents=True, exist_ok=True)
    if not (target / "msvc/setup_x64.bat").is_file():
        # Reviewed immutable downloader; actual packages come from Microsoft and
        # are verified against the official VS manifest's SHA256 values.
        url = f"https://gist.githubusercontent.com/mmozeiko/7f3162ec2988e81e56d5c4e22cde9977/raw/{MSVC_REV}/portable-msvc.py"
        script = target / "portable-msvc.py"
        if not script.is_file() or sha256(script) != MSVC_SHA:
            urllib.request.urlretrieve(url, script)
        if sha256(script) != MSVC_SHA:
            raise RuntimeError("Portable MSVC downloader checksum mismatch")
        source = script.read_text(encoding="utf-8").replace("shutil.rmtree(", "safe_rmtree(")
        # Windows Installer refuses traversal through user-created junctions.
        source = source.replace('msiexec /a "{m}"', 'msiexec /a "{m.resolve()}"')
        # Do not leave a first-time installation hung forever on one CDN socket.
        source = 'import socket, time\nsocket.setdefaulttimeout(60)\n' + source
        guard = '''def safe_rmtree(path, **kwargs):
  root = Path.cwd().resolve()
  target = Path(path).resolve()
  target.relative_to(root)
  if target == root:
    raise RuntimeError("Refusing to delete toolchain root")
  shutil.rmtree(target, **kwargs)

def retry_download(function):
  def retry(*args, **kwargs):
    for attempt in range(5):
      try:
        return function(*args, **kwargs)
      except (OSError, EOFError) as exc:
        if attempt == 4:
          raise
        print(f"Download interrupted; retry {attempt + 1}/4: {type(exc).__name__}", flush=True)
        time.sleep(2 * (attempt + 1))
  return retry

download = retry_download(download)
download_progress = retry_download(download_progress)

'''
        source = source.replace("### parse command-line arguments", guard + "### parse command-line arguments")
        checked = target / "portable-msvc-checked.py"
        checked.write_text(source, encoding="utf-8")
        subprocess.run([sys.executable, str(checked), "--vs", "2022", "--msvc-version", "14.44",
                        "--sdk-version", "22621", "--accept-license"], cwd=target, check=True)

    cuda = target / "cuda-12.9"
    cuda.mkdir(exist_ok=True)
    # All components are official NVIDIA redistributable archives, not a global
    # CUDA installer. Reuse a complete existing toolchain on repeat installs.
    complete = all((cuda / p).is_file() for p in (
        "bin/nvcc.exe", "bin/cublas64_12.dll", "include/cuda_runtime.h", "include/cub/cub.cuh",
    ))
    if not complete:
        base = "https://developer.download.nvidia.com/compute/cuda/redist/"
        with urllib.request.urlopen(base + "redistrib_12.9.1.json") as response:
            manifest = json.load(response)
        for name in ("cuda_nvcc", "cuda_cudart", "cuda_cccl", "libcublas"):
            item = manifest[name]["windows-x86_64"]
            archive = target / f"{name}.zip"
            if not archive.exists() or sha256(archive) != item["sha256"]:
                urllib.request.urlretrieve(base + item["relative_path"], archive)
            if sha256(archive) != item["sha256"]:
                raise RuntimeError(f"CUDA archive checksum mismatch: {name}")
            with zipfile.ZipFile(archive) as z:
                for member in z.infolist():
                    relative = Path(*Path(member.filename).parts[1:])
                    destination = cuda / relative
                    destination.resolve().relative_to(cuda.resolve())
                    if member.is_dir():
                        destination.mkdir(parents=True, exist_ok=True)
                    else:
                        destination.parent.mkdir(parents=True, exist_ok=True)
                        destination.write_bytes(z.read(member))
    print(f"Portable build tools ready: {target}")


if __name__ == "__main__":
    main()
