from __future__ import annotations

import os
import json
import re
from dataclasses import dataclass
from pathlib import Path


DEFAULT_STUDIO_ENGINE_SLUG = "zonos2-q8-experimental"


def local_studio_engine(root: Path) -> str:
    """Server-owned, Git-ignored configuration; never derived from a client request."""
    path = root / ".runtime" / "studio-settings.json"
    if not path.exists():
        return DEFAULT_STUDIO_ENGINE_SLUG
    settings = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(settings, dict):
        raise ValueError("studio-settings.json must contain a JSON object")
    slug = settings.get("default_engine_slug", DEFAULT_STUDIO_ENGINE_SLUG)
    if not isinstance(slug, str) or not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,99}", slug):
        raise ValueError("Invalid default_engine_slug in studio-settings.json")
    return slug


@dataclass(frozen=True)
class Paths:
    root: Path
    data: Path
    database: Path
    artifacts: Path
    references: Path
    outputs: Path
    static: Path

    @classmethod
    def discover(cls) -> "Paths":
        root = Path(os.environ.get("VOICEBENCH_ROOT", Path.cwd())).resolve()
        data = root / "data"
        return cls(
            root=root,
            data=data,
            database=data / "voicebench.sqlite3",
            artifacts=data / "artifacts",
            references=data / "artifacts" / "references",
            outputs=data / "artifacts" / "outputs",
            static=root / "voicebench" / "static",
        )

    def ensure(self) -> None:
        for directory in (self.data, self.artifacts, self.references, self.outputs):
            directory.mkdir(parents=True, exist_ok=True)
