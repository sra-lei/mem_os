"""评测 run 导入：evals/runs/run_*.json → 本地 memos.db（幂等收敛为并集）。

对侧机器 export 的 run 镜像，pull 到本地后执行本工具即并入本地评测库，
重复导入同一 run 不产生重复行（run 行 INSERT OR REPLACE，case 行整 run 重放）。
设计见 docs/方案/方案-评测记录跨机同步.md。本工具 stdlib-only。

用法：
    uv run python tests/import_run.py [路径/目录/glob ...]   # 缺省= evals/runs/*.json
"""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_DB = ROOT / "src" / "testing" / "data" / "memos.db"
_DEFAULT_GLOB = ROOT / "evals" / "runs" / "*.json"
SCHEMA_VERSION = 1


def _cols(con: sqlite3.Connection, table: str) -> list[str]:
    return [c[1] for c in con.execute(f"PRAGMA table_info({table})")]


def collect_files(patterns: list[str]) -> list[Path]:
    if patterns:
        out: list[Path] = []
        for p in patterns:
            path = Path(p)
            if path.is_dir():
                out.extend(sorted(path.glob("*.json")))
            elif path.is_file():
                out.append(path)
            else:
                hits = sorted(ROOT.glob(p)) if not path.is_absolute() else []
                out.extend(hits if hits else [path])
        return out
    return sorted(_DEFAULT_GLOB.glob("*.json")) if _DEFAULT_GLOB.parent.exists() else []


def import_file(con: sqlite3.Connection, path: Path) -> tuple[str, int, bool]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("schema_version", 0) != SCHEMA_VERSION:
        raise ValueError(f"schema_version {data.get('schema_version')} 不识别（本工具支持 {SCHEMA_VERSION}）")
    run_row = data.get("run")
    cases = data.get("cases") or []
    if not run_row or "id" not in run_row:
        raise ValueError("缺少 run 元信息")

    run_cols = _cols(con, "test_runs")
    case_cols = _cols(con, "test_case_results")
    missing_run = [k for k in run_row if k not in run_cols]
    if missing_run:
        raise ValueError(f"本地 test_runs 缺列 {missing_run}——库版本落后于导出侧，跳过")
    bad_case = [k for k in cases[0] if k not in case_cols] if cases else []
    if bad_case:
        raise ValueError(f"本地 test_case_results 缺列 {bad_case}——库版本落后于导出侧，跳过")

    existed = con.execute(
        "SELECT 1 FROM test_runs WHERE id = ?", (run_row["id"],)
    ).fetchone() is not None

    con.execute("BEGIN IMMEDIATE")
    try:
        cols = ", ".join(run_cols)
        ph = ", ".join("?" for _ in run_cols)
        con.execute(
            f"INSERT OR REPLACE INTO test_runs ({cols}) VALUES ({ph})",
            [run_row[c] for c in run_cols],
        )
        con.execute("DELETE FROM test_case_results WHERE run_id = ?", (run_row["id"],))
        if cases:
            cols = ", ".join(case_cols)
            ph = ", ".join("?" for _ in case_cols)
            con.executemany(
                f"INSERT INTO test_case_results ({cols}) VALUES ({ph})",
                [[c[k] for k in case_cols] for c in cases],
            )
        con.execute("COMMIT")
    except Exception:
        con.execute("ROLLBACK")
        raise
    return run_row["id"], len(cases), existed


def main() -> None:
    files = collect_files(sys.argv[1:])
    if not files:
        print("没有可导入的 run JSON（缺省目录 evals/runs/ 为空？）")
        raise SystemExit(1)

    con = sqlite3.connect(_DB)
    ok = fail = 0
    for f in files:
        try:
            rid, n_cases, existed = import_file(con, f)
            verb = "重放收敛（已存在）" if existed else "新增"
            print(f"{rid}: {verb}，{n_cases} 条 case（{f.name}）")
            ok += 1
        except Exception as e:  # noqa: BLE001 —— 逐文件容错
            print(f"✗ {f.name}: {e}")
            fail += 1
    print(f"完成：成功 {ok}，失败 {fail}")
    raise SystemExit(1 if fail else 0)


if __name__ == "__main__":
    main()
