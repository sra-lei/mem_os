"""评测 run 导出：本地 memos.db → evals/runs/run_<id>.json（跨机同步镜像）。

背景：memos.db 纯本地（*.db 不入 git），跨机对照靠本工具把一轮 run 导出为不可变
JSON 随 repo push/pull 流动，对侧用 tests/import_run.py 收敛进本地库。
设计见 docs/方案-评测记录跨机同步.md。本工具 stdlib-only，不 import os_mem/eval。

用法：
    uv run python tests/export_run.py                 # 最新 run（rowid 序）
    uv run python tests/export_run.py --run run_80f3  # 指定 run id 前缀（取首个匹配）
stdout 输出写入文件路径（scripts/run_eval_record.sh 依赖此输出），错误走 stderr。
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_DB = ROOT / "src" / "testing" / "data" / "memos.db"
_OUT_DIR = ROOT / "evals" / "runs"
SCHEMA_VERSION = 1


def _cols(con: sqlite3.Connection, table: str) -> list[str]:
    return [c[1] for c in con.execute(f"PRAGMA table_info({table})")]


def resolve_run_id(con: sqlite3.Connection, prefix: str | None) -> str:
    if prefix:
        row = con.execute(
            "SELECT id FROM test_runs WHERE id LIKE ? ORDER BY rowid LIMIT 1",
            (prefix + "%",),
        ).fetchone()
        if not row:
            raise SystemExit(f"未找到 run id 前缀 {prefix!r} 的 run")
        return row[0]
    row = con.execute("SELECT id FROM test_runs ORDER BY rowid DESC LIMIT 1").fetchone()
    if not row:
        raise SystemExit("memos.db 中没有 run 可导出")
    return row[0]


def main() -> None:
    ap = argparse.ArgumentParser(description="导出评测 run 为跨机同步 JSON")
    ap.add_argument("--run", default=None, help="run id 前缀（默认最新）")
    args = ap.parse_args()

    con = sqlite3.connect(f"file:{_DB}?mode=ro", uri=True)
    run_id = resolve_run_id(con, args.run)

    run_cols = _cols(con, "test_runs")
    case_cols = _cols(con, "test_case_results")
    run_row = con.execute(
        f"SELECT {', '.join(run_cols)} FROM test_runs WHERE id = ?", (run_id,)
    ).fetchone()
    case_rows = con.execute(
        f"SELECT {', '.join(case_cols)} FROM test_case_results "
        "WHERE run_id = ? ORDER BY case_id",
        (run_id,),
    ).fetchall()

    payload = {
        "schema_version": SCHEMA_VERSION,
        "exported_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "run": dict(zip(run_cols, run_row)),
        "cases": [dict(zip(case_cols, r)) for r in case_rows],
    }

    _OUT_DIR.mkdir(parents=True, exist_ok=True)
    out = _OUT_DIR / f"{run_id}.json"
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    print(out)
    print(f"已导出 run {run_id}：{len(case_rows)} 条 case → {out}", file=__import__("sys").stderr)


if __name__ == "__main__":
    main()
