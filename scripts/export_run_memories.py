#!/usr/bin/env python
"""评测批次记忆本体导出：memos.db 某 run 的 case_ids → memories.db 该批用户 → 镜像 JSON。

跨机记忆同步（见 docs/方案-评测记录跨机同步.md 的姊妹机制；落地细节在
os_mem.admin.MemAdminService.export_user_data / import_memory_batch）：
- 记忆本体（conv_meta / struct_memories / conv_messages）不进 git 的原因=整库是
  本地运行产物会分叉；本工具按「评测 run 涉及的用户」导出为不可变 JSON 镜像
  （memories_exports/run_<id>/*.json）随 repo push/pull 流动，对侧用
  tests/import_run_memories.py 幂等合并进本地库。
- 只读 memos.db（sqlite3 直连，stdlib-only）；memories.db 读取一律经 os_mem.admin
  窗口（对外纪律：外部不直连业务库）。

用法：
    uv run python scripts/export_run_memories.py                 # 最新 run
    uv run python scripts/export_run_memories.py --run run_xxx   # 指定 run 前缀
    uv run python scripts/export_run_memories.py --with-messages # 附带 conv_messages 原文
stdout 输出镜像目录路径（供 run_eval_record.sh 取用）。
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

_MEMOS_DB = ROOT / "src" / "testing" / "data" / "memos.db"
_OUT_DIR = ROOT / "memories_exports"


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
    ap = argparse.ArgumentParser(description="导出评测批次记忆本体镜像")
    ap.add_argument("--run", default=None, help="run id 前缀（默认最新）")
    ap.add_argument("--with-messages", action="store_true",
                    help="附带 conv_messages 原文（体积大；默认只导 conv_meta + struct_memories）")
    args = ap.parse_args()

    con = sqlite3.connect(f"file:{_MEMOS_DB}?mode=ro", uri=True)
    run_id = resolve_run_id(con, args.run)
    case_ids = [
        r[0]
        for r in con.execute(
            "SELECT DISTINCT case_id FROM test_case_results WHERE run_id = ?", (run_id,)
        ).fetchall()
    ]
    con.close()
    if not case_ids:
        raise SystemExit(f"run {run_id} 没有 case 结果行（可能未 --record-db 落库）")

    from os_mem.admin import get_mem_admin_service

    data = get_mem_admin_service().export_user_data(
        case_ids, with_messages=args.with_messages
    )
    out_dir = _OUT_DIR / run_id  # run_id 形如 run_64f82a0d1b，目录同形
    out_dir.mkdir(parents=True, exist_ok=True)
    for name in ("conv_meta", "struct_memories", "conv_messages"):
        (out_dir / f"{name}.json").write_text(
            json.dumps(data[name], ensure_ascii=False, indent=1)
        )
    n_struct = len(data["struct_memories"])
    n_meta = len(data["conv_meta"])
    n_msg = len(data["conv_messages"])
    print(f"[mem-sync] run {run_id}: 导出 {len(case_ids)} 用户 "
          f"(conv_meta={n_meta}, struct={n_struct}, messages={n_msg}) → {out_dir}", file=sys.stderr)
    print(out_dir)


if __name__ == "__main__":
    main()
