#!/usr/bin/env python3
"""按 user 前缀清空评测记忆缓存（使提取缓存失效，重跑评测前必做）。

背景（AGENTS.md 铁律 2 / §4.3）：同一 ``(user_id, source_session_id)`` 一旦 COMPLETED，
``claim`` 会**跳过提取**——改了**提取/入库侧**（FactExtractor / 归一器 / 实体解析 /
R1 判据）之后不清缓存，重跑测到的仍是旧数据，而且静默不报错。
D4-2（实体解析）与 Fix B（R1 判据）改的正是这一侧，因此本地 2026-09-09 那份库
必须清掉重跑，才能得到 D4 口径的数据。

清理范围（三项对应「旧数据的三个来源」）：
1. SQLite ``conv_meta``：该 user 的会话状态行 → 门禁复位（下次 ingest 可 claim）；
2. SQLite ``struct_memories``：该 user 的事实行 → 否则新事实会与 D4-2 前的旧行
   按签名比较（旧行 attribute=key 的语义），污染版本裁决与投影；
3. Milvus ``mem_os``：该 user 的全部向量 → **实例可能跨机共享**，故严格按 user 限定，
   绝不整库 drop。

**不动** ``conv_messages``（原文，不参与缓存门禁；仍是「这条记忆该不该改」的依据）。
注意：struct provider 的 ingest **不写** conv_messages（只有 base provider 写），
所以后面跑 struct 评测不会把原文补回来，``scripts/audit_silent_loss.py`` 需要原文时
得另想办法（见 13.9 讨论）。

用法：
    uv run python scripts/reset_eval_memories.py --prefix layer1 --dry-run  # 只报告
    uv run python scripts/reset_eval_memories.py --prefix layer1 --yes       # 真删
    uv run python scripts/reset_eval_memories.py --user layer1_05_internet_service --yes
    uv run python scripts/reset_eval_memories.py --prefix layer1 --yes --skip-milvus
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / "src/os_mem/data/memories.db"
# 参与缓存门禁/裁决的两张表（conv_messages 刻意不在内）
TABLES = ("conv_meta", "struct_memories")


def _all_users() -> set[str]:
    if not DB.exists():
        sys.exit(f"记忆库不存在：{DB}")
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    try:
        found: set[str] = set()
        for table in TABLES:
            found |= {
                r[0] for r in con.execute(f"select distinct user_id from {table}")
            }
        return found
    finally:
        con.close()


def _target_users(prefix: str | None, users: list[str]) -> list[str]:
    found = _all_users()
    if users:
        missing = sorted(u for u in users if u not in found)
        if missing:
            sys.exit(f"库中不存在这些 user（检查拼写）：{missing}")
        return sorted(set(users))
    if not prefix:
        sys.exit("必须给 --prefix 或 --user（防误清整库）")
    hit = sorted(u for u in found if u.startswith(prefix))
    if not hit:
        sys.exit(f"没有 user_id 以 {prefix!r} 开头，未做任何改动")
    return hit


def _survey(user_ids: list[str]) -> dict[str, dict[str, int]]:
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    out: dict[str, dict[str, int]] = {}
    try:
        for u in user_ids:
            counts = {
                t: con.execute(
                    f"select count(*) from {t} where user_id=?", (u,)
                ).fetchone()[0]
                for t in TABLES
            }
            counts["conv_messages"] = con.execute(
                "select count(*) from conv_messages where user_id=?", (u,)
            ).fetchone()[0]
            out[u] = counts
        return out
    finally:
        con.close()


def _wipe_sqlite(user_ids: list[str]) -> dict[str, dict[str, int]]:
    con = sqlite3.connect(DB)
    deleted: dict[str, dict[str, int]] = {}
    try:
        for u in user_ids:
            deleted[u] = {}
            for t in TABLES:
                cur = con.execute(f"delete from {t} where user_id=?", (u,))
                deleted[u][t] = int(cur.rowcount or 0)
        con.commit()
        return deleted
    finally:
        con.close()


def _wipe_milvus(user_ids: list[str]) -> dict[str, object]:
    """按 user 删向量。lazy import：--dry-run 不触发 os_mem 加载（也不连云端）。"""
    from os_mem.infra.storage import get_memory_vector_store

    store = get_memory_vector_store()
    out: dict[str, object] = {}
    for u in user_ids:
        try:
            out[u] = store.delete_memories(user_id=u)
        except Exception as e:  # noqa: BLE001 - 逐 user 容错：SQLite 已清要如实报出
            out[u] = f"FAILED: {type(e).__name__}: {e}"
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description="按 user 前缀清空评测记忆缓存")
    ap.add_argument("--prefix", default=None, help="user_id 前缀，如 layer1 / layer2")
    ap.add_argument(
        "--user", action="append", default=[], help="精确 user_id（可重复）"
    )
    ap.add_argument("--yes", action="store_true", help="确认执行删除（缺省=只报告）")
    ap.add_argument("--dry-run", action="store_true", help="只报告，零改动")
    ap.add_argument(
        "--skip-milvus", action="store_true", help="只清 SQLite，不碰向量库"
    )
    args = ap.parse_args()

    user_ids = _target_users(args.prefix, args.user)
    before = _survey(user_ids)
    print(f"命中 {len(user_ids)} 个 user（prefix={args.prefix or '-'}）：")
    for u in user_ids:
        b = before[u]
        print(
            f"  {u:<42} struct_memories={b['struct_memories']:<5} "
            f"conv_meta={b['conv_meta']:<3} conv_messages(保留)={b['conv_messages']}"
        )
    n_facts = sum(v["struct_memories"] for v in before.values())
    n_meta = sum(v["conv_meta"] for v in before.values())
    print(f"合计待清：struct_memories {n_facts} 行 / conv_meta {n_meta} 行")

    if args.dry_run or not args.yes:
        print("\n[DRY-RUN] 未做任何改动；加 --yes 才真删。")
        return

    deleted = _wipe_sqlite(user_ids)
    print(
        f"\nSQLite 已清：struct_memories "
        f"{sum(v['struct_memories'] for v in deleted.values())} 行 / conv_meta "
        f"{sum(v['conv_meta'] for v in deleted.values())} 行"
    )

    if args.skip_milvus:
        print("Milvus：按 --skip-milvus 跳过（旧向量仍在，需另用管理窗口「重建投影」）")
    else:
        print("Milvus 清理：")
        for u, res in _wipe_milvus(user_ids).items():
            print(f"  {u:<42} delete_memories -> {res}")

    print(
        "\n[done] 缓存已失效：下次 ingest 会重新提取（会话由评测用例重建，"
        "不依赖 conv_messages）。"
    )


if __name__ == "__main__":
    main()
