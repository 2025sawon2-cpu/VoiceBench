from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


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
