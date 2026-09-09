"""D4-4 五例定向回放：重置 → 仅提取入库（走 D4 版本裁决）→ 验库。

不跑 answer/judge（省钱）。跑法：
  cd /opt/mem_os && PYTHONPATH=src:tests .venv/bin/python scripts/d4_replay_5.py
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

CASES = {
    "layer2_10_travel_rebooking_chain": "10改签",
    "layer2_11_medical_treatment_evolution": "11诊疗",
    "layer2_12_contradictory_financial_instructions": "12矛盾",
    "layer2_17_tech_support_cascade": "17技术支持",
    "layer2_20_healthcare_coverage_changes": "20医保",
}
DB = ROOT / "src/os_mem/data/memories.db"


def reset_users(uids: list[str]) -> None:
    db = sqlite3.connect(DB)
    cur = db.cursor()
    for uid in uids:
        for table in ("conv_meta", "struct_memories"):
            cur.execute(f"DELETE FROM {table} WHERE user_id=?", (uid,))
    db.commit()
    db.close()
    print("[reset] sqlite done:", len(uids), "users")

    from os_mem.infra.storage import get_memory_vector_store

    store = get_memory_vector_store()
    # Milvus 按 user_id 精确 OR 删除
    clauses = " or ".join(f'user_id == "{u}"' for u in uids)
    resp = store.client.delete(collection_name=store.collection_name, filter=clauses)
    print("[reset] milvus:", resp)


def ingest_cases(uids: list[str]) -> None:
    from eval.harness import _build_conversation
    from os_mem.provider import build_memory_provider

    cases_dir = ROOT / "tests/test_cases/layer2"
    for uid in uids:
        # uid=layer2_10_travel_rebooking_chain -> 10_travel_rebooking_chain.yaml
        fname = uid.split("_", 1)[1]
        path = cases_dir / f"{fname}.yaml"
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        provider = build_memory_provider("struct", user_id=uid)
        for conv in data["conversation_histories"]:
            convo = _build_conversation(conv, uid)
            print(f"  [{uid}] ingest {convo.id} ({len(convo.messages)} msgs)")
            provider.ingest(convo)
        print(f"[{uid}] ingest done")


def verify_case12() -> None:
    """case12 关键断言：wire 金额/日期/reference 各自只留一条 current 且为最终值。"""
    db = sqlite3.connect(DB)
    db.row_factory = sqlite3.Row
    uid = "layer2_12_contradictory_financial_instructions"

    print("\n===== case12 wire 簇版本状态 =====")
    rows = db.execute(
        "SELECT attribute,lifecycle,value,version,source_conversation_id,"
        "source_started_at FROM struct_memories WHERE user_id=? "
        "AND attribute IN ('wire_amount','wire_date','wire_reference') "
        "ORDER BY attribute,version",
        (uid,),
    ).fetchall()
    for r in rows:
        print(f"  {r['attribute']:<16} {r['lifecycle']:<10} v{r['version']} "
              f"={r['value']:<12} {r['source_conversation_id']} @{r['source_started_at']}")

    print("\n--- 裁决检查 ---")
    for attr, want in [("wire_amount", "$95,000"),
                       ("wire_reference", "WT-89089")]:
        cur = db.execute(
            "SELECT COUNT(*) c FROM struct_memories WHERE user_id=? "
            "AND attribute=? AND lifecycle='current'", (uid, attr)).fetchone()["c"]
        vals = [r["value"] for r in db.execute(
            "SELECT value FROM struct_memories WHERE user_id=? AND attribute=? "
            "AND lifecycle='current'", (uid, attr)).fetchall()]
        ok = cur == 1 and (want in (vals[0] if vals else ""))
        print(f"  {attr}: current行数={cur} 值={vals} 期望含 {want} -> "
              f"{'PASS' if ok else 'CHECK'}")

    # historical original_wire_amount
    hist = db.execute(
        "SELECT value,lifecycle FROM struct_memories WHERE user_id=? "
        "AND attribute='wire_amount' AND lifecycle='historical'", (uid,)).fetchall()

    # current 总数 / superseded 总数
    for life in ("current", "superseded", "historical"):
        n = db.execute(
            "SELECT COUNT(*) c FROM struct_memories WHERE user_id=? AND lifecycle=?",
            (uid, life)).fetchone()["c"]
        print(f"  {life}: {n}")
    db.close()


def main() -> None:
    uids = list(CASES)
    reset_users(uids)
    ingest_cases(uids)
    verify_case12()
    print("\n[done] 回放+验库完成（未跑 answer/judge）")


if __name__ == "__main__":
    main()
