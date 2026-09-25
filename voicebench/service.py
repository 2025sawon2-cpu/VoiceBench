from __future__ import annotations

import hashlib
import itertools
import json
import random
import re
import shutil
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
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
STUDIO_ENGINE_SLUG = "qwen3-tts-17b-pascal-expressive"
STUDIO_INPUT_POLICY = "single_segment_full_text_v1"
STUDIO_DEFAULT_SEED = 13
LONG_FORM_QUALITY_MODE = "long_form_hq"
LONG_FORM_CHAPTER_TARGET_CHARS = 1800
LONG_FORM_CHAPTER_MIN_CHARS = 900
EXTERNAL_REUSE_WINDOW_HOURS = 24
STUDIO_SEGMENT_TARGET_CHARS = 180
STUDIO_SEGMENT_HARD_MAX_CHARS = 240
STUDIO_SEGMENT_MIN_CHARS = 60

_TRANSITION_OPENERS = re.compile(
    r"^(?:자|하지만|그러나|그렇지만|그렇다면|이제|먼저|다음으로|한편|반대로|"
    r"결국|따라서|그러므로|마지막으로|결론적으로)(?:[,，]\s*|\s+|$)"
)
_CONCLUSION_OPENERS = re.compile(r"^(?:결국|따라서|그러므로|마지막으로|결론적으로)\b")
_SENTENCE_PATTERN = re.compile(
    r".+?(?:[.!?。！？]+[\"'’”\)\]\}」』]*|$)(?=\s+|$)"
)


@dataclass(frozen=True)
class SpeechSegment:
    text: str
    paragraph_index: int
    sentence_count: int
    semantic_role: str
    paragraph_end: bool
    pause_after_ms: int


def _split_long_sentence(text: str, target: int, hard_maximum: int) -> list[str]:
    pieces: list[str] = []
    remaining = text.strip()
    minimum = max(20, target // 2)
    while len(remaining) > hard_maximum:
        candidates = [
            match.end()
            for match in re.finditer(r"[,，;；:：]", remaining[: hard_maximum + 1])
            if match.end() >= minimum
            # Keep numbers and times such as 12,345 and 03:45 intact.
            and not (
                match.start() > 0
                and remaining[match.start() - 1].isdigit()
                and remaining[match.end() : match.end() + 1].isdigit()
            )
        ]
        if candidates:
            boundary = min(candidates, key=lambda value: (abs(value - target), -value))
        else:
            boundary = remaining.rfind(" ", minimum, hard_maximum + 1)
            if boundary < minimum:
                boundary = hard_maximum
        pieces.append(remaining[:boundary].strip())
        remaining = remaining[boundary:].strip()
    if remaining:
        pieces.append(remaining)
    return pieces


def _split_sentences(paragraph: str) -> list[str]:
    sentences = [match.group(0).strip() for match in _SENTENCE_PATTERN.finditer(paragraph)]
    return [sentence for sentence in sentences if sentence] or [paragraph.strip()]


def _sentence_role(text: str, opening: bool = False) -> str:
    ending = text.rstrip().rstrip("\"'’”)\]}」』")
    if re.search(r"[?？]+$", ending):
        return "question"
    if re.search(r"[!！]+$", ending):
        return "exclamation"
    if _CONCLUSION_OPENERS.match(text):
        return "conclusion"
    if _TRANSITION_OPENERS.match(text):
        return "transition"
    return "opening" if opening else "statement"


def _segment_role(units: list[dict[str, Any]]) -> str:
    if units[-1]["role"] in {"question", "exclamation"}:
        return str(units[-1]["role"])
    if units[0]["role"] in {"transition", "conclusion", "opening"}:
        return str(units[0]["role"])
    return "statement"


def _base_pause_after(text: str, role: str) -> int:
    if role == "question":
        return 360
    if role == "exclamation":
        return 340
    ending = text.rstrip().rstrip("\"'’”)\]}」』")
    if re.search(r"[.。]+$", ending):
        return 280
    if re.search(r"[,，;；:：]+$", ending):
        return 170
    return 220


def plan_script(
    text: str,
    maximum: int = STUDIO_SEGMENT_TARGET_CHARS,
    preserve_sentences: bool = False,
) -> list[SpeechSegment]:
    cleaned = re.sub(r"[ \t]+", " ", text.replace("\r\n", "\n").strip())
    if not cleaned:
        return []
    hard_maximum = max(maximum, round(maximum * 4 / 3))
    if maximum == STUDIO_SEGMENT_TARGET_CHARS:
        hard_maximum = STUDIO_SEGMENT_HARD_MAX_CHARS
    planned: list[SpeechSegment] = []
    paragraphs = [item for item in re.split(r"\n\s*\n+", cleaned) if item.strip()]
    for paragraph_index, raw_paragraph in enumerate(paragraphs, start=1):
        paragraph = re.sub(r"\s*\n\s*", " ", raw_paragraph).strip()
        units: list[dict[str, Any]] = []
        for sentence_index, sentence in enumerate(_split_sentences(paragraph)):
            role = _sentence_role(
                sentence,
                opening=paragraph_index == 1 and sentence_index == 0,
            )
            for piece_index, piece in enumerate(
                _split_long_sentence(sentence, maximum, hard_maximum)
            ):
                units.append(
                    {
                        "text": piece,
                        # A long question's rising ending belongs to its last
                        # piece, not the first piece ending at a comma.
                        "role": _sentence_role(piece, opening=role == "opening")
                        if piece_index == 0
                        else _sentence_role(piece),
                        "sentence_count": 1 if piece_index == 0 else 0,
                    }
                )

        groups: list[list[dict[str, Any]]] = []
        current: list[dict[str, Any]] = []
        current_length = 0

        def flush() -> None:
            nonlocal current, current_length
            if current:
                groups.append(current)
                current = []
                current_length = 0

        for unit in units:
            unit_text = str(unit["text"])
            begins_transition = unit["role"] in {"transition", "conclusion"}
            if (
                current
                and begins_transition
                and current_length >= min(STUDIO_SEGMENT_MIN_CHARS, maximum // 2)
            ):
                flush()
            combined_length = current_length + (1 if current else 0) + len(unit_text)
            if current and combined_length > maximum:
                flush()
            current.append(unit)
            current_length += (1 if current_length else 0) + len(unit_text)
            if preserve_sentences or (
                unit["role"] in {"question", "exclamation"}
                and current_length >= min(STUDIO_SEGMENT_MIN_CHARS, maximum // 2)
            ):
                flush()
        flush()

        if (
            not preserve_sentences
            and len(groups) > 1
            # Do not undo a deliberate question/transition breath just to
            # fill a short tail. These cuts carry meaning, not only length.
            and groups[-1][0]["role"] not in {"transition", "conclusion"}
            and groups[-2][-1]["role"] not in {"question", "exclamation"}
            and len(" ".join(str(item["text"]) for item in groups[-1]))
            < min(STUDIO_SEGMENT_MIN_CHARS, maximum // 2)
        ):
            previous_text = " ".join(str(item["text"]) for item in groups[-2])
            tail_text = " ".join(str(item["text"]) for item in groups[-1])
            if len(previous_text) + 1 + len(tail_text) <= hard_maximum:
                groups[-2].extend(groups.pop())

        paragraph_segments: list[SpeechSegment] = []
        for group in groups:
            group_text = " ".join(str(item["text"]) for item in group)
            role = _segment_role(group)
            paragraph_segments.append(
                SpeechSegment(
                    text=group_text,
                    paragraph_index=paragraph_index,
                    sentence_count=sum(int(item["sentence_count"]) for item in group),
                    semantic_role=role,
                    paragraph_end=False,
                    pause_after_ms=_base_pause_after(group_text, role),
                )
            )
        if paragraph_segments:
            last = paragraph_segments[-1]
            paragraph_pause = (
                500
                if last.semantic_role == "question"
                else 450
                if last.semantic_role == "exclamation"
                else 420
            )
            paragraph_segments[-1] = SpeechSegment(
                text=last.text,
                paragraph_index=last.paragraph_index,
                sentence_count=last.sentence_count,
                semantic_role=last.semantic_role,
                paragraph_end=True,
                pause_after_ms=max(paragraph_pause, last.pause_after_ms),
            )
        planned.extend(paragraph_segments)

    for index in range(len(planned) - 1):
        following = planned[index + 1]
        if following.semantic_role not in {"transition", "conclusion"}:
            continue
        current = planned[index]
        planned[index] = SpeechSegment(
            text=current.text,
            paragraph_index=current.paragraph_index,
            sentence_count=current.sentence_count,
            semantic_role=current.semantic_role,
            paragraph_end=current.paragraph_end,
            pause_after_ms=max(460, current.pause_after_ms),
        )
    return planned


def split_script(
    text: str,
    maximum: int = STUDIO_SEGMENT_TARGET_CHARS,
    preserve_sentences: bool = False,
) -> list[str]:
    return [
        segment.text
        for segment in plan_script(text, maximum, preserve_sentences)
    ]


def partition_studio_chapters(
    segments: list[dict[str, Any]],
    target_chars: int = LONG_FORM_CHAPTER_TARGET_CHARS,
    minimum_chars: int = LONG_FORM_CHAPTER_MIN_CHARS,
) -> list[list[dict[str, Any]]]:
    if not segments:
        return []
    chapters: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    current_chars = 0
    for segment in segments:
        length = len(str(segment.get("text", "")))
        if current and current_chars + length > target_chars:
            prefix_chars = 0
            paragraph_boundary = 0
            for boundary, item in enumerate(current, start=1):
                prefix_chars += len(str(item.get("text", "")))
                if item.get("paragraph_end") and prefix_chars >= minimum_chars:
                    paragraph_boundary = boundary
            if paragraph_boundary:
                chapters.append(current[:paragraph_boundary])
                current = current[paragraph_boundary:]
                current_chars = sum(len(str(item.get("text", ""))) for item in current)
            elif current_chars >= minimum_chars:
                chapters.append(current)
                current = []
                current_chars = 0
        current.append(segment)
        current_chars += length
    if current:
        chapters.append(current)
    if len(chapters) > 1:
        last_chars = sum(len(str(item.get("text", ""))) for item in chapters[-1])
        if last_chars < minimum_chars:
            chapters[-2].extend(chapters.pop())
    return chapters


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
        # Group jobs by engine so memory-constrained GPUs do not reload models
        # for every prompt when comparing multiple model sizes.
        for engine_id, prompt, seed in itertools.product(engine_ids, project["prompts"], seeds):
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

    def preview_studio_script(self, text: str) -> dict[str, Any]:
        """Use the synthesis planner without creating projects, prompts or jobs."""
        if not isinstance(text, str):
            raise ValueError("대본은 문자열이어야 합니다.")
        segments = plan_script(text)
        return {
            "segment_count": len(segments),
            "join_count": max(0, len(segments) - 1),
            "paragraph_count": max((s.paragraph_index for s in segments), default=0),
            "segments": [
                {"position": index, **asdict(segment)}
                for index, segment in enumerate(segments, start=1)
            ],
        }

    def create_studio_request(
        self,
        text: str,
        seed: int = STUDIO_DEFAULT_SEED,
        engine_slug: str = STUDIO_ENGINE_SLUG,
        source: str = "studio",
        quality_mode: str = LONG_FORM_QUALITY_MODE,
        request_fingerprint: str | None = None,
    ) -> dict[str, Any]:
        normalized = text.strip()
        if not normalized:
            raise ValueError("합성할 대본을 입력하세요.")
        speech_plan = plan_script(normalized)
        if not speech_plan:
            raise ValueError("합성할 수 있는 문장이 없습니다.")
        if source not in {"studio", "external_api"}:
            raise ValueError("Unsupported Studio request source.")
        if quality_mode not in {"standard", LONG_FORM_QUALITY_MODE}:
            raise ValueError("Unsupported quality mode.")
        project = self.ensure_studio_project()
        engine = self.db.fetch_one(
            "SELECT * FROM engines WHERE slug=? AND enabled=1", (engine_slug,)
        )
        if not engine:
            raise ValueError(f"사용 가능한 TTS 엔진이 없습니다: {engine_slug}")
        now = utc_now()
        request_id = self.db.execute(
            """INSERT INTO studio_requests(
                   project_id,original_text,seed,source,quality_mode,request_fingerprint,
                   status,created_at
               ) VALUES(?,?,?,?,?,?,?,?)""",
            (
                project["id"], normalized, int(seed), source, quality_mode,
                request_fingerprint, "queued", now,
            ),
        )
        for position, segment in enumerate(speech_plan, start=1):
            prompt = self.add_prompt(project["id"], segment.text, "studio")
            job_id = self.db.execute(
                """INSERT INTO jobs(project_id,prompt_id,engine_id,seed,status,created_at)
                   VALUES(?,?,?,?,?,?)""",
                (project["id"], prompt["id"], engine["id"], int(seed), "queued", now),
            )
            self.db.execute(
                """INSERT INTO studio_segments(
                       request_id,position,text,prompt_id,job_id,paragraph_index,
                       sentence_count,semantic_role,paragraph_end,pause_after_ms
                   ) VALUES(?,?,?,?,?,?,?,?,?,?)""",
                (
                    request_id,
                    position,
                    segment.text,
                    prompt["id"],
                    job_id,
                    segment.paragraph_index,
                    segment.sentence_count,
                    segment.semantic_role,
                    int(segment.paragraph_end),
                    segment.pause_after_ms,
                ),
            )
        return self.studio_request(request_id)

    def external_tts_fingerprint(self, text: str) -> tuple[str, str]:
        normalized = text.strip()
        if not normalized:
            raise ValueError("합성할 대본을 입력하세요.")
        project = self.ensure_studio_project()
        engine = self.db.fetch_one(
            "SELECT * FROM engines WHERE slug=? AND enabled=1", (STUDIO_ENGINE_SLUG,)
        )
        if not engine:
            raise ValueError(f"사용 가능한 TTS 엔진이 없습니다: {STUDIO_ENGINE_SLUG}")
        fingerprint_data = {
            "version": 6,
            "input_policy": STUDIO_INPUT_POLICY,
            "text": normalized,
            "seed": STUDIO_DEFAULT_SEED,
            "quality_mode": LONG_FORM_QUALITY_MODE,
            "engine_slug": engine["slug"],
            "engine_version": engine["version"],
            "engine_config": engine.get("config_json", "{}"),
            "reference_sha256": project.get("reference_sha256"),
            "reference_text": project.get("reference_text", ""),
        }
        digest = hashlib.sha256(json_dumps(fingerprint_data).encode("utf-8")).hexdigest()
        return normalized, digest

    def reusable_external_tts_request(self, text: str) -> dict[str, Any] | None:
        normalized, fingerprint = self.external_tts_fingerprint(text)
        project = self.ensure_studio_project()
        engine = self.db.fetch_one(
            "SELECT id FROM engines WHERE slug=? AND enabled=1", (STUDIO_ENGINE_SLUG,)
        )
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=EXTERNAL_REUSE_WINDOW_HOURS)).isoformat(
            timespec="seconds"
        )
        row = self.db.fetch_one(
            """SELECT DISTINCT sr.id,sr.status,sr.output_path,sr.request_fingerprint
               FROM studio_requests sr
               JOIN studio_segments ss ON ss.request_id=sr.id
               JOIN jobs j ON j.id=ss.job_id
               WHERE sr.source='external_api' AND sr.original_text=?
                 AND sr.project_id=? AND sr.seed=? AND sr.quality_mode=?
                 AND j.engine_id=?
                 AND sr.request_fingerprint=?
                 AND (
                   sr.status IN ('queued','running') OR
                   (sr.status='succeeded' AND sr.finished_at>=?)
                 )
               ORDER BY CASE sr.status
                 WHEN 'running' THEN 0 WHEN 'queued' THEN 1 ELSE 2 END,
                 sr.id DESC LIMIT 1""",
            (
                normalized, project["id"], STUDIO_DEFAULT_SEED, LONG_FORM_QUALITY_MODE,
                engine["id"], fingerprint, cutoff,
            ),
        )
        if not row:
            return None
        if row["status"] == "succeeded" and (
            not row.get("output_path") or not Path(row["output_path"]).is_file()
        ):
            return None
        return self.external_tts_request(row["id"])

    def create_external_tts_request(self, text: str) -> dict[str, Any]:
        normalized, fingerprint = self.external_tts_fingerprint(text)
        return self.create_studio_request(
            normalized,
            STUDIO_DEFAULT_SEED,
            source="external_api",
            request_fingerprint=fingerprint,
        )

    def external_tts_request(self, request_id: int) -> dict[str, Any]:
        request = self.studio_request(request_id)
        if request.get("source") != "external_api":
            raise KeyError("External TTS request not found.")
        succeeded = request["status"] == "succeeded" and bool(request.get("output_path"))
        chapters = []
        for chapter in request.get("chapters", []):
            item = dict(chapter)
            item["audio_url"] = (
                f"/v1/tts/{request_id}/chapters/{chapter['position']}/audio"
                if chapter["status"] == "succeeded" else None
            )
            chapters.append(item)
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
            "quality_mode": request.get("quality_mode"),
            "quality_control": request.get("quality_control"),
            "continuity": request.get("continuity"),
            "speech_plan": request.get("speech_plan"),
            "chapters": chapters,
            "download_count": int(request.get("download_count") or 0),
            "last_downloaded_at": request.get("last_downloaded_at"),
            "status_url": f"/v1/tts/{request_id}",
            "audio_url": f"/v1/tts/{request_id}/audio" if succeeded else None,
        }

    def external_tts_audio_path(self, request_id: int) -> Path:
        self.external_tts_request(request_id)
        return self.studio_audio_path(request_id)

    def record_external_tts_download(self, request_id: int) -> None:
        self.external_tts_audio_path(request_id)
        self.db.execute(
            """UPDATE studio_requests
               SET download_count=download_count+1,last_downloaded_at=?
               WHERE id=?""",
            (utc_now(), request_id),
        )

    def external_tts_chapter_audio_path(self, request_id: int, chapter_position: int) -> Path:
        self.external_tts_request(request_id)
        return self.studio_chapter_audio_path(request_id, chapter_position)

    def active_external_tts_count(self) -> int:
        row = self.db.fetch_one(
            """SELECT COUNT(*) AS count FROM studio_requests
               WHERE source='external_api' AND status IN ('queued','running')"""
        )
        return int(row["count"] if row else 0)

    def recover_interrupted_jobs(self) -> int:
        """Return work left running by a previous VoiceBench process to the queue."""
        self.db.execute(
            """UPDATE jobs SET status='cancelled',finished_at=?
               WHERE status='running' AND id IN (
                 SELECT ss.job_id FROM studio_segments ss
                 JOIN studio_requests sr ON sr.id=ss.request_id
                 WHERE sr.status='cancelled'
               )""",
            (utc_now(),),
        )
        row = self.db.fetch_one("SELECT COUNT(*) AS count FROM jobs WHERE status='running'")
        recovered = int(row["count"] if row else 0)
        self.db.execute(
            "UPDATE jobs SET status='queued',started_at=NULL WHERE status='running'"
        )
        if recovered:
            self.db.execute(
                """UPDATE studio_requests SET status='queued'
                   WHERE status='running' AND id IN (
                     SELECT DISTINCT request_id FROM studio_segments ss
                     JOIN jobs j ON j.id=ss.job_id WHERE j.status='queued'
                   )"""
            )
        return recovered

    def cancel_studio_request(self, request_id: int) -> dict[str, Any]:
        request = self.studio_request(request_id)
        if request["status"] in {"succeeded", "failed", "cancelled"}:
            return request
        self.db.execute(
            """UPDATE jobs SET status='cancelled',finished_at=?
               WHERE status='queued' AND id IN (
                   SELECT job_id FROM studio_segments WHERE request_id=?
               )""",
            (utc_now(), request_id),
        )
        self.db.execute(
            "UPDATE studio_requests SET status='cancelled',finished_at=? WHERE id=?",
            (utc_now(), request_id),
        )
        return self.studio_request(request_id)

    def studio_request(self, request_id: int) -> dict[str, Any]:
        request = self.db.fetch_one(
            """SELECT sr.*,MIN(e.slug) AS engine_slug,MIN(e.name) AS engine_name,
               COUNT(ss.id) AS segment_count,
               SUM(CASE WHEN j.status='succeeded' THEN 1 ELSE 0 END) AS completed_segments,
               SUM(CASE WHEN j.status='failed' THEN 1 ELSE 0 END) AS failed_segments
               FROM studio_requests sr
               LEFT JOIN studio_segments ss ON ss.request_id=sr.id
               LEFT JOIN jobs j ON j.id=ss.job_id
               LEFT JOIN engines e ON e.id=j.engine_id
               WHERE sr.id=? GROUP BY sr.id""",
            (request_id,),
        )
        if not request:
            raise KeyError("TTS 요청을 찾을 수 없습니다.")
        request["segment_count"] = int(request["segment_count"] or 0)
        request["completed_segments"] = int(request["completed_segments"] or 0)
        request["failed_segments"] = int(request["failed_segments"] or 0)
        pending = self.db.fetch_one(
            """SELECT MIN(j.id) AS first_pending_job,
                      SUM(CASE WHEN j.status='queued' THEN 1 ELSE 0 END) AS queued_segments,
                      SUM(CASE WHEN j.status='running' THEN 1 ELSE 0 END) AS running_segments
               FROM studio_segments ss JOIN jobs j ON j.id=ss.job_id
               WHERE ss.request_id=?""",
            (request_id,),
        ) or {}
        request["queued_segments"] = int(pending.get("queued_segments") or 0)
        request["running_segments"] = int(pending.get("running_segments") or 0)
        first_pending_job = pending.get("first_pending_job")
        if first_pending_job:
            ahead = self.db.fetch_one(
                """SELECT COUNT(*) AS count FROM jobs
                   WHERE status IN ('queued','running') AND id < ?""",
                (first_pending_job,),
            )
            request["jobs_ahead"] = int(ahead["count"] if ahead else 0)
        else:
            request["jobs_ahead"] = 0
        segment_rows = self.db.fetch_all(
            """SELECT ss.position,ss.text,j.status,r.metrics_json,
                      ss.paragraph_index,ss.sentence_count,ss.semantic_role,
                      ss.paragraph_end,ss.pause_after_ms
               FROM studio_segments ss JOIN jobs j ON j.id=ss.job_id
               LEFT JOIN runs r ON r.job_id=j.id
               WHERE ss.request_id=? ORDER BY ss.position""",
            (request_id,),
        )
        quality_control: dict[str, Any] = {
            "enabled": request.get("quality_mode") == LONG_FORM_QUALITY_MODE,
            "checked_segments": 0,
            "regenerated_segments": 0,
            "fallback_selected_segments": 0,
            "warning_segments": 0,
            "total_attempts": 0,
            "selected_seed_counts": {},
        }
        continuity: dict[str, Any] = {
            "recorded_segments": 0,
            "requested_segments": 0,
            "used_segments": 0,
            "fallback_segments": 0,
            "reset_reason_counts": {},
        }
        for segment in segment_rows:
            if not segment.get("metrics_json"):
                continue
            try:
                metrics = json.loads(segment["metrics_json"])
            except (TypeError, json.JSONDecodeError):
                continue
            context = metrics.get("continuity")
            if isinstance(context, dict):
                continuity["recorded_segments"] += 1
                continuity["requested_segments"] += int(bool(context.get("requested")))
                continuity["used_segments"] += int(bool(context.get("used")))
                continuity["fallback_segments"] += int(bool(context.get("fallback_to_reference")))
                if context.get("requested") and context.get("reset_reason"):
                    reason = context["reset_reason"]
                    counts = continuity["reset_reason_counts"]
                    counts[reason] = counts.get(reason, 0) + 1
            quality = metrics.get("quality_control") or {}
            if not quality.get("enabled"):
                continue
            quality_control["checked_segments"] += 1
            quality_control["total_attempts"] += int(quality.get("attempt_count") or 0)
            if quality.get("regenerated"):
                quality_control["regenerated_segments"] += 1
            if quality.get("fallback_selected"):
                quality_control["fallback_selected_segments"] += 1
            if quality.get("status") in {"best_available", "waveform_only"}:
                quality_control["warning_segments"] += 1
            selected_seed = quality.get("selected_seed")
            if selected_seed is not None:
                key = str(selected_seed)
                quality_control["selected_seed_counts"][key] = (
                    quality_control["selected_seed_counts"].get(key, 0) + 1
                )
        request["quality_control"] = quality_control
        request["continuity"] = continuity
        role_counts: dict[str, int] = {}
        for segment in segment_rows:
            role = str(segment.get("semantic_role") or "statement")
            role_counts[role] = role_counts.get(role, 0) + 1
        request["speech_plan"] = {
            "version": 1,
            "paragraph_count": max(
                (int(segment.get("paragraph_index") or 1) for segment in segment_rows),
                default=0,
            ),
            "segment_count": len(segment_rows),
            "sentence_count": sum(
                int(segment.get("sentence_count") or 0) for segment in segment_rows
            ),
            "paragraph_breaks": sum(
                bool(segment.get("paragraph_end")) for segment in segment_rows[:-1]
            ),
            "role_counts": role_counts,
        }
        request["chapters"] = []
        if request.get("quality_mode") == LONG_FORM_QUALITY_MODE:
            for position, chapter in enumerate(partition_studio_chapters(segment_rows), start=1):
                statuses = [item["status"] for item in chapter]
                chapter_path = (
                    self.paths.artifacts / "studio" / f"request-{request_id}-chapter-{position:02d}.wav"
                )
                if all(status == "succeeded" for status in statuses):
                    status = "succeeded" if chapter_path.exists() else "running"
                elif any(status == "failed" for status in statuses):
                    status = "failed"
                elif any(status == "running" for status in statuses):
                    status = "running"
                elif any(status == "cancelled" for status in statuses):
                    status = "cancelled"
                else:
                    status = "queued"
                request["chapters"].append(
                    {
                        "position": position,
                        "status": status,
                        "segment_count": len(chapter),
                        "completed_segments": sum(s == "succeeded" for s in statuses),
                        "character_count": sum(len(item["text"]) for item in chapter),
                        "audio_url": (
                            f"/api/studio/{request_id}/chapters/{position}/audio"
                            if status == "succeeded" else None
                        ),
                    }
                )
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
        studio_engines = self.db.fetch_all(
            """SELECT id,slug,name,version,enabled FROM engines
               WHERE enabled=1 AND (
                 slug='qwen3-tts-06b-pascal' OR slug LIKE 'qwen3-tts-17b-pascal%'
               )
               ORDER BY CASE slug
                 WHEN 'qwen3-tts-17b-pascal-expressive' THEN 0
                 WHEN 'qwen3-tts-17b-pascal' THEN 1
                 WHEN 'qwen3-tts-17b-pascal-stable' THEN 2
                 ELSE 3 END"""
        )
        requests = self.db.fetch_all(
            "SELECT id FROM studio_requests ORDER BY id DESC LIMIT 50"
        )
        processing = self.db.fetch_one(
            """SELECT ss.request_id FROM jobs j JOIN studio_segments ss ON ss.job_id=j.id
               WHERE j.status='running' ORDER BY j.started_at,j.id LIMIT 1"""
        )
        return {
            "available": available and bool(studio_engines),
            "voice": {
                "name": "내 목소리 · B 레퍼런스",
                "duration_seconds": 9.95,
                "reference_path": str(reference) if available else None,
            },
            "engine": engine,
            "default_engine_slug": STUDIO_ENGINE_SLUG,
            "studio_engines": studio_engines,
            "project_id": project["id"] if project else None,
            "processing_request_id": processing["request_id"] if processing else None,
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

    def studio_chapter_audio_path(self, request_id: int, chapter_position: int) -> Path:
        request = self.studio_request(request_id)
        chapter = next(
            (item for item in request.get("chapters", []) if item["position"] == chapter_position),
            None,
        )
        if not chapter or chapter["status"] != "succeeded":
            raise FileNotFoundError("아직 완성된 장 음성이 없습니다.")
        path = (
            self.paths.artifacts / "studio" / f"request-{request_id}-chapter-{chapter_position:02d}.wav"
        ).resolve()
        studio_root = (self.paths.artifacts / "studio").resolve()
        if studio_root not in path.parents or not path.exists():
            raise FileNotFoundError("장 음성 파일을 찾을 수 없습니다.")
        return path
