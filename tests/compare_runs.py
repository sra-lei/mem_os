"""评测 run 对照：A vs B 的通过率与失败集差异（替代人肉对失败集）。

A/B 各可为：
- run id 前缀（如 run_80f3，查本地 memos.db，取首个匹配）
- 本机 evals/runs/ 下的 json 文件路径或文件名

输出：双方通过率 + 失败集 + 对称差（一侧过一侧挂的 case，即 B 相对 A 修复/回退）。
设计见 docs/方案/方案-评测记录跨机同步.md。本工具 stdlib-only。

用法：
    uv run python tests/compare_runs.py run_80f3624109 run_64f82a0d1b
    uv run python tests/compare_runs.py evals/runs/run_A.json run_64f8
"""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_DB = ROOT / "src" / "testing" / "data" / "memos.db"
_RUNS_DIR = ROOT / "evals" / "runs"


def _load_from_db(run_id: str) -> dict:
    con = sqlite3.connect(f"file:{_DB}?mode=ro", uri=True)
    meta = con.execute(
        "SELECT * FROM test_runs WHERE id LIKE ? ORDER BY rowid LIMIT 1", (run_id + "%",)
    ).fetchone()
    if not meta:
        raise SystemExit(f"本地库未找到 run 前缀 {run_id!r}（他侧 run 需先 import_run.py）")
    cols = [c[1] for c in con.execute("PRAGMA table_info(test_runs)")]
    run = dict(zip(cols, meta))
    ccols = [c[1] for c in con.execute("PRAGMA table_info(test_case_results)")]
    cases = {
        r[ccols.index("case_id")]: dict(zip(ccols, r))
        for r in con.execute(
            "SELECT * FROM test_case_results WHERE run_id = ?", (run["id"],)
        )
    }
    return {"run": run, "cases": cases}


def _load_from_file(arg: str) -> dict:
    path = Path(arg)
    if not path.is_file() and not path.is_absolute():
        candidate = _RUNS_DIR / path.name if not path.name.endswith(".json") else _RUNS_DIR / path.name
        if candidate.is_file():
            path = candidate
    if not path.is_file():
        raise SystemExit(f"找不到 {arg}（既不是本地 run 前缀也不是可读 json 文件）")
    data = json.loads(path.read_text(encoding="utf-8"))
    return {"run": data["run"], "cases": {c["case_id"]: c for c in data["cases"]}}


def load(arg: str) -> dict:
    return _load_from_file(arg) if Path(arg).is_file() else _load_from_db(arg)


def _fmt(run: dict) -> str:
    return (
        f"{run['id']}  {run.get('phase') or '-'}  run_at={str(run.get('run_at'))[:19]}  "
        f"pass_rate={run.get('pass_rate')}  (库列 total_cases={run.get('total_cases')})"
    )


def main() -> None:
    if len(sys.argv) != 3:
        raise SystemExit("用法: compare_runs.py <A> <B>   （run id 前缀或 json 路径）")
    a = load(sys.argv[1])
    b = load(sys.argv[2])

    def stats(x: dict) -> tuple[int, int, list[str]]:
        cases = x["cases"]
        failed = sorted(cid for cid, c in cases.items() if not c["passed"])
        return len(cases), len(cases) - len(failed), failed

    n_a, p_a, fail_a = stats(a)
    n_b, p_b, fail_b = stats(b)

    print(f"A: {_fmt(a['run'])}   通过 {p_a}/{n_a}")
    print(f"B: {_fmt(b['run'])}   通过 {p_b}/{n_b}")
    print()
    set_a, set_b = set(fail_a), set(fail_b)
    only_b = sorted(set_a - set_b)   # A 失败、B 通过 → B 相对 A 修复/胜出
    only_a = sorted(set_b - set_a)   # B 失败、A 通过 → B 相对 A 回退
    both = sorted(set_a & set_b)
    print(f"A 失败 {len(fail_a)} 个: {fail_a}")
    print(f"B 失败 {len(fail_b)} 个: {fail_b}")
    print()
    print(f"[B 修复/胜出] A 挂 B 过（{len(only_b)}）: {only_b}")
    print(f"[B 回退]     B 挂 A 过（{len(only_a)}）: {only_a}")
    print(f"[双方都挂]   （{len(both)}）: {both}")


if __name__ == "__main__":
    main()
