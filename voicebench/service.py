from __future__ import annotations

import hashlib
import itertools
import json
import random
import re
import shutil
from pathlib import Path
from typing import Any

from .config import Paths
from .db import Database
from .util import atomic_write_bytes, json_dumps, sha256_file, system_snapshot, utc_now


DEFAULT_CORPUS = [
    {"category": "general", "text": "안녕하세요. 오늘은 목소리 복제 품질을 차분하게 확인해 보겠습니다."},
    {"category": "batchim", "text": "꽃잎 위 맑은 이슬과 짧고 굵은 빗방울이 햇빛을 받아 반짝였다."},
    {"category": "liaison", "text": "옷 안에 작은 열쇠가 있고, 부엌 앞에는 읽을 책이 놓여 있습니다."},
    {"category": "number", "text": "주문 번호는 20260712이고, 수량은 1,284개입니다."},
    {"category": "money", "text": "총 결제 금액은 12만 3천 4백 50원이며 할인율은 17.5퍼센트입니다."},
    {"category": "date", "text": "회의는 2026년 7월 12일 오후 3시 45분에 시작합니다."},
    {"category": "phone", "text": "문의 전화는 02-1234-5678이고 접수 번호는 031-987-6543입니다."},
    {"category": "unit", "text": "물 750밀리리터와 밀가루 1.5킬로그램을 섞어 주세요."},
    {"category": "english", "text": "GPU와 CUDA 설정을 확인한 뒤 Voice Cloning 테스트를 실행합니다."},
    {"category": "proper_noun", "text": "제주 서귀포와 경주 불국사를 거쳐 서울 광화문으로 이동했습니다."},
    {"category": "question", "text": "정말 이 목소리가 원래 화자와 자연스럽게 닮았다고 느끼시나요?"},
    {"category": "emotion", "text": "드디어 해냈어! 믿기지 않을 만큼 기쁘고 가슴이 벅차올라."},
    {"category": "emotion", "text": "괜찮다고 말했지만, 사실은 조금 서운하고 마음이 무거웠다."},
    {"category": "whisper", "text": "아무도 듣지 못하게 아주 조용하고 부드러운 목소리로 말해 주세요."},
    {"category": "formal", "text": "본 실험은 동일한 조건에서 각 음성 합성 엔진의 결과를 비교합니다."},
    {"category": "dialogue", "text": "잠깐만, 그게 무슨 뜻이야? 조금 더 쉽게 다시 설명해 줄래?"},
    {"category": "long", "text": "새벽부터 내리던 비가 그치자 골목 끝 작은 빵집의 문이 열렸다. 따뜻한 빵 냄새가 젖은 공기 사이로 천천히 퍼졌고, 지나가던 사람들은 잠시 발걸음을 늦추었다."},
    {"category": "long", "text": "우리는 결과가 빨리 나오는 모델보다 발음이 정확하고 목소리가 오래 유지되는 모델을 찾고 있다. 처리 시간이 조금 길어지더라도 최종 음질과 자연스러움이 더 중요하다."},
    {"category": "mixed", "text": "Python 3.10 환경에서 FP16 모드로 실행하고 결과를 WAV 파일로 저장합니다."},
    {"category": "pause", "text": "첫 번째 결과를 들어 보겠습니다. 잠시 멈춘 뒤, 두 번째 결과와 비교해 주세요."},
]

STUDIO_DESCRIPTION = "VoiceBench personal TTS studio"
STUDIO_ENGINE_SLUG = "qwen3-tts-06b-pascal"


def split_script(text: str, maximum: int = 90) -> list[str]:
    cleaned = re.sub(r"[ \t]+", " ", text.strip())
    if not cleaned:
        return []
    sentences = [
        part.strip()
        for part in re.split(r"(?<=[.!?。！？])\s+|\n+", cleaned)
        if part.strip()
    ]
    pieces: list[str] = []
    for sentence in sentences:
        remaining = sentence
        while len(remaining) > maximum:
            boundary = max(
                remaining.rfind(mark, 0, maximum + 1)
                for mark in (" ", ",", "，", ";", ":")
            )
            if boundary < maximum // 2:
                boundary = maximum
            pieces.append(remaining[:boundary].strip(" ,，;:"))
            remaining = remaining[boundary:].strip(" ,，;:")
        if remaining:
            pieces.append(remaining)

    packed: list[str] = []
    for piece in pieces:
        if packed and len(packed[-1]) + 1 + len(piece) <= maximum:
            packed[-1] += " " + piece
        else:
            packed.append(piece)
    return packed


class Service:
    def __init__(self, paths: Paths, database: Database):
        self.paths = paths
        self.db = database
        self._ensure_defaults()

    def _ensure_defaults(self) -> None:
        now = utc_now()
        self.db.execute(
            """INSERT INTO engines(slug,name,kind,version,config_json,enabled,created_at,updated_at)
               VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(slug) DO NOTHING""",
            ("mock-a", "Mock A · 하네스 검증용", "mock", "1", "{}", 1, now, now),
        )
        self.db.execute(
            """INSERT INTO engines(slug,name,kind,version,config_json,enabled,created_at,updated_at)
               VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(slug) DO NOTHING""",
            ("mock-b", "Mock B · 하네스 검증용", "mock", "2", "{}", 1, now, now),
        )

    def state(self) -> dict[str, Any]:
        projects = self.db.fetch_all("SELECT * FROM projects ORDER BY id DESC")
        engines = self.db.fetch_all("SELECT * FROM engines ORDER BY id")
        for engine in engines:
            engine["config"] = json.loads(engine.pop("config_json") or "{}")
        return {"projects": projects, "engines": engines, "system": system_snapshot()}

    def create_project(self, name: str, description: str = "") -> dict[str, Any]:
        if not name.strip():
            raise ValueError("프로젝트 이름이 필요합니다.")
        now = utc_now()
        project_id = self.db.execute(
            "INSERT INTO projects(name,description,created_at,updated_at) VALUES(?,?,?,?)",
            (name.strip(), description.strip(), now, now),
        )
        return self.get_project(project_id)

    def get_project(self, project_id: int) -> dict[str, Any]:
        project = self.db.fetch_one("SELECT * FROM projects WHERE id=?", (project_id,))
        if not project:
            raise KeyError("프로젝트를 찾을 수 없습니다.")
        project["prompts"] = self.db.fetch_all(
            "SELECT * FROM prompts WHERE project_id=? ORDER BY position", (project_id,)
        )
        project["jobs"] = self.db.fetch_all(
            """SELECT status, COUNT(*) AS count FROM jobs WHERE project_id=?
               GROUP BY status ORDER BY status""",
            (project_id,),
        )
        project["run_count"] = self.db.fetch_one(
            "SELECT COUNT(*) AS count FROM runs WHERE project_id=?", (project_id,)
        )["count"]
        return project

    def upload_reference(
        self, project_id: int, filename: str, content: bytes, reference_text: str = ""
    ) -> dict[str, Any]:
        self.get_project(project_id)
        if not content:
            raise ValueError("빈 음성 파일입니다.")
        suffix = Path(filename).suffix.lower() or ".wav"
        digest = hashlib.sha256(content).hexdigest()
        destination = self.paths.references / f"{digest}{suffix}"
        if not destination.exists():
            atomic_write_bytes(destination, content)
        now = utc_now()
        self.db.execute(
            """UPDATE projects SET reference_path=?,reference_sha256=?,reference_text=?,updated_at=?
               WHERE id=?""",
            (str(destination), digest, reference_text.strip(), now, project_id),
        )
        return self.get_project(project_id)

    def update_reference_text(self, project_id: int, reference_text: str) -> dict[str, Any]:
        self.get_project(project_id)
        self.db.execute(
            "UPDATE projects SET reference_text=?,updated_at=? WHERE id=?",
            (reference_text.strip(), utc_now(), project_id),
        )
        return self.get_project(project_id)

    def import_default_corpus(self, project_id: int) -> dict[str, Any]:
        self.get_project(project_id)
        existing = self.db.fetch_one("SELECT COUNT(*) AS count FROM prompts WHERE project_id=?", (project_id,))["count"]
        now = utc_now()
        for offset, item in enumerate(DEFAULT_CORPUS, start=existing + 1):
            self.db.execute(
                """INSERT INTO prompts(project_id,position,category,original_text,normalized_text,created_at)
                   VALUES(?,?,?,?,?,?)""",
                (project_id, offset, item["category"], item["text"], item["text"], now),
            )
        return self.get_project(project_id)

    def add_prompt(self, project_id: int, text: str, category: str = "custom") -> dict[str, Any]:
        if not text.strip():
            raise ValueError("대본이 비어 있습니다.")
        position = self.db.fetch_one(
            "SELECT COALESCE(MAX(position),0)+1 AS position FROM prompts WHERE project_id=?", (project_id,)
        )["position"]
        prompt_id = self.db.execute(
            """INSERT INTO prompts(project_id,position,category,original_text,normalized_text,created_at)
               VALUES(?,?,?,?,?,?)""",
            (project_id, position, category, text.strip(), text.strip(), utc_now()),
        )
        return self.db.fetch_one("SELECT * FROM prompts WHERE id=?", (prompt_id,))

    def upsert_engine(self, payload: dict[str, Any]) -> dict[str, Any]:
        slug = str(payload.get("slug", "")).strip().lower()
        name = str(payload.get("name", "")).strip()
        kind = str(payload.get("kind", "command"))
        if not slug or not name or kind not in {"mock", "command"}:
            raise ValueError("올바른 엔진 slug, name, kind가 필요합니다.")
        config = payload.get("config", {})
        now = utc_now()
        self.db.execute(
            """INSERT INTO engines(slug,name,kind,version,config_json,enabled,created_at,updated_at)
               VALUES(?,?,?,?,?,?,?,?)
               ON CONFLICT(slug) DO UPDATE SET name=excluded.name,kind=excluded.kind,
               version=excluded.version,config_json=excluded.config_json,enabled=excluded.enabled,
               updated_at=excluded.updated_at""",
            (
                slug,
                name,
                kind,
                str(payload.get("version", "")),
                json_dumps(config),
                1 if payload.get("enabled", True) else 0,
                now,
                now,
            ),
        )
        engine = self.db.fetch_one("SELECT * FROM engines WHERE slug=?", (slug,))
        engine["config"] = json.loads(engine.pop("config_json"))
        return engine

    def queue_benchmark(self, project_id: int, engine_ids: list[int], seeds: list[int]) -> dict[str, Any]:
        project = self.get_project(project_id)
        if not project.get("reference_path"):
            raise ValueError("먼저 레퍼런스 음성을 업로드하세요.")
        if not project["prompts"]:
            raise ValueError("평가 대본이 없습니다.")
        if not engine_ids or not seeds:
            raise ValueError("엔진과 seed가 필요합니다.")
        valid = self.db.fetch_all(
            f"SELECT id,config_json FROM engines WHERE enabled=1 AND id IN ({','.join('?' for _ in engine_ids)})",
            tuple(engine_ids),
        )
        valid_ids = {row["id"] for row in valid}
        if valid_ids != set(engine_ids):
            raise ValueError("존재하지 않거나 비활성화된 엔진이 포함되어 있습니다.")
        if not project.get("reference_text") and any(
            json.loads(row.get("config_json") or "{}").get("requires_reference_text")
            for row in valid
        ):
            raise ValueError("선택한 엔진에는 레퍼런스 음성의 정확한 대본이 필요합니다.")
        now = utc_now()
        count = 0
        for prompt, engine_id, seed in itertools.product(project["prompts"], engine_ids, seeds):
            self.db.execute(
                """INSERT INTO jobs(project_id,prompt_id,engine_id,seed,status,created_at)
                   VALUES(?,?,?,?,?,?)""",
                (project_id, prompt["id"], engine_id, int(seed), "queued", now),
            )
            count += 1
        return {"queued": count}

    def runs(self, project_id: int) -> list[dict[str, Any]]:
        rows = self.db.fetch_all(
            """SELECT r.*,p.position,p.category,p.original_text,e.name AS engine_name,j.seed
               FROM runs r JOIN prompts p ON p.id=r.prompt_id
               JOIN engines e ON e.id=r.engine_id JOIN jobs j ON j.id=r.job_id
               WHERE r.project_id=? ORDER BY r.id DESC""",
            (project_id,),
        )
        for row in rows:
            row["metrics"] = json.loads(row.pop("metrics_json") or "{}")
            row.pop("manifest_json", None)
        return rows

    def next_comparison(self, project_id: int) -> dict[str, Any] | None:
        unrated = self.db.fetch_one(
            "SELECT * FROM comparisons WHERE project_id=? AND winner IS NULL ORDER BY id LIMIT 1", (project_id,)
        )
        if not unrated:
            candidates = self.db.fetch_all(
                """SELECT r.id,r.prompt_id FROM runs r
                   WHERE r.project_id=? ORDER BY r.prompt_id,r.id""",
                (project_id,),
            )
            grouped: dict[int, list[int]] = {}
            for row in candidates:
                grouped.setdefault(row["prompt_id"], []).append(row["id"])
            for prompt_id, run_ids in grouped.items():
                for first, second in itertools.combinations(run_ids, 2):
                    exists = self.db.fetch_one(
                        """SELECT id FROM comparisons WHERE
                           (left_run_id=? AND right_run_id=?) OR (left_run_id=? AND right_run_id=?)""",
                        (first, second, second, first),
                    )
                    if exists:
                        continue
                    if random.Random(first * 1000003 + second).random() < 0.5:
                        first, second = second, first
                    comparison_id = self.db.execute(
                        """INSERT INTO comparisons(project_id,prompt_id,left_run_id,right_run_id,created_at)
                           VALUES(?,?,?,?,?)""",
                        (project_id, prompt_id, first, second, utc_now()),
                    )
                    unrated = self.db.fetch_one("SELECT * FROM comparisons WHERE id=?", (comparison_id,))
                    break
                if unrated:
                    break
        if not unrated:
            return None
        prompt = self.db.fetch_one("SELECT * FROM prompts WHERE id=?", (unrated["prompt_id"],))
        return {
            "id": unrated["id"],
            "prompt": prompt,
            "left": {"run_id": unrated["left_run_id"], "audio_url": f"/api/audio/{unrated['left_run_id']}"},
            "right": {"run_id": unrated["right_run_id"], "audio_url": f"/api/audio/{unrated['right_run_id']}"},
        }

    def rate_comparison(self, comparison_id: int, winner: str, notes: str = "") -> dict[str, Any]:
        if winner not in {"left", "right", "tie", "both_bad"}:
            raise ValueError("잘못된 평가 값입니다.")
        self.db.execute(
            "UPDATE comparisons SET winner=?,notes=?,rated_at=? WHERE id=?",
            (winner, notes.strip(), utc_now(), comparison_id),
        )
        return self.db.fetch_one("SELECT * FROM comparisons WHERE id=?", (comparison_id,))

    def audio_path(self, run_id: int) -> Path:
        row = self.db.fetch_one("SELECT output_path FROM runs WHERE id=?", (run_id,))
        if not row:
            raise KeyError("결과 음성을 찾을 수 없습니다.")
        path = Path(row["output_path"]).resolve()
        if self.paths.outputs.resolve() not in path.parents:
            raise ValueError("허용되지 않은 출력 경로입니다.")
        return path

    def comparison_summary(self, project_id: int) -> list[dict[str, Any]]:
        return self.db.fetch_all(
            """SELECT e.name,e.slug,
               SUM(CASE WHEN (c.winner='left' AND c.left_run_id=r.id) OR
                              (c.winner='right' AND c.right_run_id=r.id) THEN 1 ELSE 0 END) AS wins,
               COUNT(c.id) AS appearances
               FROM runs r JOIN engines e ON e.id=r.engine_id
               LEFT JOIN comparisons c ON c.left_run_id=r.id OR c.right_run_id=r.id
               WHERE r.project_id=? GROUP BY e.id ORDER BY wins DESC,e.name""",
            (project_id,),
        )

    def ensure_studio_project(self) -> dict[str, Any]:
        reference = self.paths.data / "prepared" / "ref_voice" / "selected_reference.wav"
        transcript = reference.with_suffix(".txt")
        if not reference.exists() or not transcript.exists():
            raise FileNotFoundError("선택된 기본 레퍼런스 음성 또는 대본이 없습니다.")
        project = self.db.fetch_one(
            "SELECT * FROM projects WHERE description=? ORDER BY id LIMIT 1",
            (STUDIO_DESCRIPTION,),
        )
        if not project:
            project = self.create_project("내 목소리 TTS 스튜디오", STUDIO_DESCRIPTION)
        self.db.execute(
            """UPDATE projects SET reference_path=?,reference_sha256=?,reference_text=?,updated_at=?
               WHERE id=?""",
            (
                str(reference.resolve()),
                sha256_file(reference),
                transcript.read_text(encoding="utf-8").strip(),
                utc_now(),
                project["id"],
            ),
        )
        return self.get_project(project["id"])

    def create_studio_request(
        self,
        text: str,
        seed: int = 10,
        engine_slug: str = STUDIO_ENGINE_SLUG,
        source: str = "studio",
    ) -> dict[str, Any]:
        normalized = text.strip()
        if not normalized:
            raise ValueError("합성할 대본을 입력하세요.")
        chunks = split_script(normalized)
        if not chunks:
            raise ValueError("합성할 수 있는 문장이 없습니다.")
        if source not in {"studio", "external_api"}:
            raise ValueError("Unsupported Studio request source.")
        project = self.ensure_studio_project()
        engine = self.db.fetch_one(
            "SELECT * FROM engines WHERE slug=? AND enabled=1", (engine_slug,)
        )
        if not engine:
            raise ValueError(f"사용 가능한 TTS 엔진이 없습니다: {engine_slug}")
        now = utc_now()
        request_id = self.db.execute(
            """INSERT INTO studio_requests(project_id,original_text,seed,source,status,created_at)
               VALUES(?,?,?,?,?,?)""",
            (project["id"], normalized, int(seed), source, "queued", now),
        )
        for position, chunk in enumerate(chunks, start=1):
            prompt = self.add_prompt(project["id"], chunk, "studio")
            job_id = self.db.execute(
                """INSERT INTO jobs(project_id,prompt_id,engine_id,seed,status,created_at)
                   VALUES(?,?,?,?,?,?)""",
                (project["id"], prompt["id"], engine["id"], int(seed), "queued", now),
            )
            self.db.execute(
                """INSERT INTO studio_segments(request_id,position,text,prompt_id,job_id)
                   VALUES(?,?,?,?,?)""",
                (request_id, position, chunk, prompt["id"], job_id),
            )
        return self.studio_request(request_id)

    def external_tts_request(self, request_id: int) -> dict[str, Any]:
        request = self.studio_request(request_id)
        if request.get("source") != "external_api":
            raise KeyError("External TTS request not found.")
        succeeded = request["status"] == "succeeded" and bool(request.get("output_path"))
        return {
            "id": request["id"],
            "status": request["status"],
            "seed": request["seed"],
            "segment_count": request["segment_count"],
            "completed_segments": request["completed_segments"],
            "duration_seconds": request.get("duration_seconds"),
            "error": request.get("error"),
            "created_at": request["created_at"],
            "started_at": request.get("started_at"),
            "finished_at": request.get("finished_at"),
            "status_url": f"/v1/tts/{request_id}",
            "audio_url": f"/v1/tts/{request_id}/audio" if succeeded else None,
        }

    def external_tts_audio_path(self, request_id: int) -> Path:
        self.external_tts_request(request_id)
        return self.studio_audio_path(request_id)

    def active_external_tts_count(self) -> int:
        row = self.db.fetch_one(
            """SELECT COUNT(*) AS count FROM studio_requests
               WHERE source='external_api' AND status IN ('queued','running')"""
        )
        return int(row["count"] if row else 0)

    def studio_request(self, request_id: int) -> dict[str, Any]:
        request = self.db.fetch_one(
            """SELECT sr.*,
               COUNT(ss.id) AS segment_count,
               SUM(CASE WHEN j.status='succeeded' THEN 1 ELSE 0 END) AS completed_segments,
               SUM(CASE WHEN j.status='failed' THEN 1 ELSE 0 END) AS failed_segments
               FROM studio_requests sr
               LEFT JOIN studio_segments ss ON ss.request_id=sr.id
               LEFT JOIN jobs j ON j.id=ss.job_id
               WHERE sr.id=? GROUP BY sr.id""",
            (request_id,),
        )
        if not request:
            raise KeyError("TTS 요청을 찾을 수 없습니다.")
        request["audio_url"] = (
            f"/api/studio/audio/{request_id}" if request.get("output_path") else None
        )
        return request

    def studio_state(self) -> dict[str, Any]:
        reference = self.paths.data / "prepared" / "ref_voice" / "selected_reference.wav"
        transcript = reference.with_suffix(".txt")
        available = reference.exists() and transcript.exists()
        project = self.ensure_studio_project() if available else None
        engine = self.db.fetch_one(
            "SELECT id,slug,name,version,enabled FROM engines WHERE slug=?",
            (STUDIO_ENGINE_SLUG,),
        )
        requests = self.db.fetch_all(
            "SELECT id FROM studio_requests ORDER BY id DESC LIMIT 50"
        )
        return {
            "available": available and bool(engine and engine["enabled"]),
            "voice": {
                "name": "내 목소리 · B 레퍼런스",
                "duration_seconds": 9.95,
                "reference_path": str(reference) if available else None,
            },
            "engine": engine,
            "project_id": project["id"] if project else None,
            "requests": [self.studio_request(row["id"]) for row in requests],
        }

    def studio_audio_path(self, request_id: int) -> Path:
        request = self.studio_request(request_id)
        if not request.get("output_path"):
            raise FileNotFoundError("아직 완성된 음성이 없습니다.")
        path = Path(request["output_path"]).resolve()
        studio_root = (self.paths.artifacts / "studio").resolve()
        if studio_root not in path.parents:
            raise ValueError("허용되지 않은 Studio 출력 경로입니다.")
        return path
