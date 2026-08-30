"""Memory storage layer.

Production memories live in their own database (os_mem.db) — deliberately
separate from memos.db, which holds only evaluation data (test_runs /
test_case_results / test_case_definitions). os_mem never touches memos.db.

Isolation design: any sqlite path can be injected. The evaluation framework
passes a temporary path per run (temp_db_path) and namespaces memories per
case via user_id; production uses default_db_path().

The actual table/CRUD implementation is YOUR work (需求文档 v0.1 module 1.2):

    CREATE TABLE memories (
        id                TEXT PRIMARY KEY,
        user_id           TEXT NOT NULL,
        fact              TEXT NOT NULL,          -- 纯文本事实
        source_session_id TEXT,
        created_at        TEXT
    );
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

from ..logger import get_logger

_logger = get_logger("os_mem.storage")

_PROJECT_ROOT = Path(__file__).resolve().parents[3]


def default_db_path() -> Path:
    """Production memory database path (project root / os_mem.db)."""
    return _PROJECT_ROOT / "os_mem.db"


def temp_db_path(prefix: str = "eval_mem_") -> Path:
    """Create an empty temp file usable as an evaluation-scoped memory database.

    Caller is responsible for deleting it after the run.
    """
    fd, name = tempfile.mkstemp(prefix=prefix, suffix=".db")
    os.close(fd)
    _logger.info(f"创建评测临时记忆库: {name}")
    return Path(name)
