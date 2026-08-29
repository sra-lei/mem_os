"""Load YAML test cases from tests/test_cases/ into memos.db.

Idempotent: re-running updates existing definitions (keyed by test_id),
so it is safe to run after YAML edits.

Usage:
    python -m scripts.load_test_cases
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from sqlalchemy import text  # noqa: E402

from testing.db import get_engine, get_session, init_db  # noqa: E402
from testing.db.models import TestCaseDefinition  # noqa: E402

TEST_CASES_DIR = ROOT / "tests" / "test_cases"

# layer (YAML `category`) -> (dashboard phase, version_target)
# Mirrors the roadmap in docs/MemOs需求文档.md: v0.1 base recall,
# v0.2 multi-session, v0.3 proactive service.
LAYER_MAP = {
    "layer1": ("base", "v0.1"),
    "layer2": ("multi_session", "v0.2"),
    "layer3": ("proactive", "v0.3"),
}

NEW_COLUMNS = (
    "conversation_histories_raw",
    "evaluation_criteria",
    "expected_behavior",
    "source_path",
)

# Removed column: setup_dialog stored the same JSON as conversation_histories_raw.
# Dropped here (SQLite 3.35+ supports DROP COLUMN); safe to re-run.
DROPPED_COLUMNS = ("setup_dialog",)

# Words stripped from `title` when deriving tags (generic/structural words only).
_TAG_STOPWORDS = {
    "the", "a", "an", "of", "for", "with", "to", "in", "on", "at", "and",
    "across", "all", "setup", "details", "retrieval", "coordination",
    "synthesis", "management", "service", "services",
}


def _derive_expected_answer(criteria: str, max_len: int = 500) -> str:
    """Derive a short expected-answer summary from evaluation_criteria.

    The rubric's first paragraph is typically a direct statement of the
    expected behavior ("The agent must ..."). Fall back to a longer window
    if the first paragraph is too short to be meaningful.
    """
    if not criteria:
        return ""
    parts = [p.strip() for p in criteria.split("\n\n") if p.strip()]
    head = parts[0] if parts else criteria.strip()
    if len(head) < 40 and len(parts) > 1:
        head = " ".join(parts[:2])
    if len(head) > max_len:
        head = head[:max_len].rstrip() + "..."
    return head


def _derive_tags(title: str, max_tags: int = 5) -> list[str]:
    """Derive index tags from the case title (source YAML has no tags field)."""
    words = []
    for part in title.replace("-", " ").split():
        token = part.strip(".,;:()").lower()
        if token and token not in _TAG_STOPWORDS:
            words.append(token)
    seen: list[str] = []
    for w in words:
        if w not in seen:
            seen.append(w)
    return seen[:max_tags]


def _ensure_columns() -> None:
    """Lightweight migration: add new nullable TEXT columns if missing, drop
    removed columns (setup_dialog duplicated conversation_histories_raw).

    SQLite ALTER TABLE ADD/DROP COLUMN is safe on existing data; all new
    columns are nullable so no backfill is required.
    """
    engine = get_engine()
    with engine.begin() as conn:
        existing = {
            row[1]
            for row in conn.execute(
                text("PRAGMA table_info(test_case_definitions)")
            ).fetchall()
        }
        for col in NEW_COLUMNS:
            if col not in existing:
                conn.execute(
                    text(f"ALTER TABLE test_case_definitions ADD COLUMN {col} TEXT")
                )
                print(f"  migrated: added column {col}")
        for col in DROPPED_COLUMNS:
            if col in existing:
                conn.execute(text(f"ALTER TABLE test_case_definitions DROP COLUMN {col}"))
                print(f"  migrated: dropped column {col}")


def load_all() -> int:
    yaml_files = sorted(TEST_CASES_DIR.glob("**/*.yaml"))
    loaded = 0
    with get_session() as session:
        for path in yaml_files:
            data = yaml.safe_load(path.read_text(encoding="utf-8"))
            if not isinstance(data, dict) or not data.get("test_id"):
                print(f"  skip (missing test_id): {path.relative_to(ROOT)}")
                continue

            layer = str(data.get("category", "")).lower()
            phase, version_target = LAYER_MAP.get(layer, (layer, "v0.1"))
            histories = data.get("conversation_histories", [])
            histories_json = json.dumps(histories, ensure_ascii=False)
            expected_behavior = data.get("expected_behavior")
            # YAMLs without expected_behavior get a summary derived from the
            # rubric (41/60 cases); tags are derived from the title (all cases).
            if not expected_behavior:
                expected_behavior = _derive_expected_answer(data.get("evaluation_criteria") or "")
            tags = json.dumps(_derive_tags(str(data.get("title") or data["test_id"])), ensure_ascii=False)

            case = session.get(TestCaseDefinition, data["test_id"])
            if case is None:
                case = TestCaseDefinition(case_id=data["test_id"])
                session.add(case)

            case.name = str(data.get("title") or data["test_id"])
            case.category = phase
            case.version_target = version_target
            case.description = data.get("description")
            case.conversation_histories_raw = histories_json
            case.query = data.get("user_question")
            case.expected_answer = expected_behavior
            case.evaluation_criteria = data.get("evaluation_criteria")
            case.expected_behavior = expected_behavior
            case.tags = tags
            case.source_path = str(path.relative_to(ROOT)).replace("\\", "/")
            case.updated_at = datetime.now(timezone.utc).replace(tzinfo=None)
            loaded += 1
        session.commit()
    return loaded


def main() -> None:
    init_db()
    _ensure_columns()
    n = load_all()
    rel = TEST_CASES_DIR.relative_to(ROOT)
    print(f"Loaded {n} test cases from {rel}")


if __name__ == "__main__":
    main()
