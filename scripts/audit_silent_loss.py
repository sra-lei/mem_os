#!/usr/bin/env python3
"""静默丢失审计：兜底句被 R1 剪掉、但其精确 token 在最终落库事实里找不到。

背景（2026-09-12）：layer1 全量扫描发现 17 条此类丢失（横跨 8 例），根因是
「多实体并存 → 同签名批内收敛丢弃事实」，而 R1 剪枝的判据用的是
「LLM 原始输出」而非「实际落库的事实集」，导致结构化与兜底双保险同时失效。

**纯确定性、零 LLM**：兜底句由正则产出，落库事实读 SQLite。
用法：
    .venv/bin/python scripts/audit_silent_loss.py            # 全部 layer1 用户
    .venv/bin/python scripts/audit_silent_loss.py layer2
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from loguru import logger  # noqa: E402

logger.remove()

from os_mem.extractor.regular_extractor import RegularExtractor  # noqa: E402
from os_mem.extractor.utils.token_utils import fact_tokens  # noqa: E402

DB = ROOT / "src/os_mem/data/memories.db"
prefix = sys.argv[1] if len(sys.argv) > 1 else "layer1"

db = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
db.row_factory = sqlite3.Row

users = [r[0] for r in db.execute(
    "SELECT DISTINCT user_id FROM conv_messages WHERE user_id LIKE ? ORDER BY user_id",
    (prefix + "%",),
)]

total_fb = kept = pruned = silent = 0
findings: list[tuple[str, str, list[str], str]] = []

for uid in users:
    msgs = [r["content"] for r in db.execute(
        "SELECT content FROM conv_messages WHERE user_id=? ORDER BY source_session_id, seq", (uid,))]
    if not msgs:
        continue
    fallbacks = RegularExtractor.fallback_numeric_facts("\n".join(msgs))
    rows = db.execute("SELECT key, fact, value FROM struct_memories WHERE user_id=?", (uid,)).fetchall()
    stored_keys = {r["key"] for r in rows}
    stored_tokens: set[str] = set()
    for r in rows:
        if not (r["key"] or "").startswith("verbatim"):
            stored_tokens |= fact_tokens(f'{r["fact"]} {r["value"] or ""}')

    for f in fallbacks:
        total_fb += 1
        if f.key in stored_keys:
            kept += 1
            continue
        pruned += 1
        toks = fact_tokens(f'{f.fact} {f.value or ""}')
        if toks and not (toks <= stored_tokens):
            silent += 1
            findings.append((uid, f.key, sorted(toks), (f.fact or "")[:90]))

print(f"用户数={len(users)}  兜底句 {total_fb} | 落库 {kept} | 被剪 {pruned}")
print(f"其中【静默丢失】{silent} 条\n")
for uid, key, toks, fact in findings:
    print(f"  {uid:<34} {key}  tokens={toks}")
    print(f"      {fact}")
if not silent:
    print("（无静默丢失）")
