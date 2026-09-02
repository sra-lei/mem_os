"""单独测试混合检索：用 layer1 各用例的 user_question 检索 mem_os 向量库。

目的：排查评测通过率低是否出在"检索环节"——直接看每条用例能召回到什么事实，
以及期望答案里的关键信息（数字/账号等）有没有被召回。

用法：
    uv run python -m scripts.test_hybrid_retrieval            # 全部 20 条
    uv run python -m scripts.test_hybrid_retrieval --limit 5  # 前 5 条
    uv run python -m scripts.test_hybrid_retrieval --case layer1_01_bank_account
"""
from __future__ import annotations

import argparse
import re
from pathlib import Path

import yaml

from os_mem.infra.storage import get_memory_vector_store
from os_mem.infra.storage.vectorizer import get_vectorizer

TEST_DIR = Path("tests/test_cases/layer1")


def load_cases() -> list[dict]:
    cases = []
    for p in sorted(TEST_DIR.glob("*.yaml")):
        data = yaml.safe_load(p.read_text(encoding="utf-8"))
        cases.append({
            "case_id": data["test_id"],
            "question": data.get("user_question", ""),
            "expected": data.get("expected_behavior") or data.get("evaluation_criteria", ""),
            "source": p.name,
        })
    return cases


def key_numbers(text: str) -> list[str]:
    """从期望答案里提取疑似关键信息的长数字（账号/路由/金额/编号等）。"""
    return re.findall(r"\b\d{4,}\b", text)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--case", type=str, default=None)
    parser.add_argument("--top-k", type=int, default=5)
    args = parser.parse_args()

    store = get_memory_vector_store()
    vectorizer = get_vectorizer()

    cases = load_cases()
    if args.case:
        cases = [c for c in cases if args.case in c["case_id"]]
    if args.limit:
        cases = cases[: args.limit]

    total_hit = 0
    total_num = 0
    print(f"mem_os 库总记录: {store.count()}\n")

    for c in cases:
        q = c["question"]
        uid = c["case_id"]
        try:
            q_vec = vectorizer.embed(q)
        except Exception as e:
            print(f"[{c['case_id']}] embedding 失败: {e}")
            continue

        hits = store.search(q_vec, query_text=q, top_k=args.top_k, user_id=uid)

        print(f"== {c['case_id']} | {c['source']}")
        print(f"   问题: {q[:90]}")
        if not hits:
            print("   ❌ 检索 0 条命中")
            continue
        for h in hits:
            print(f"   [{h['distance']:.3f}] {h.get('category','?')}/{h.get('key','?')}: "
                  f"{(h.get('fact') or '')[:80]}")

        # 期望答案里的关键数字是否被召回
        nums = key_numbers(c["expected"])
        if nums:
            hit_text = " ".join(h.get("fact") or "" for h in hits)
            found = [n for n in nums if n in hit_text]
            total_num += len(nums)
            total_hit += len(found)
            mark = "✅" if found else "❌"
            print(f"   {mark} 期望关键数字 {len(found)}/{len(nums)} 被召回: "
                  f"{[n for n in nums if n in hit_text]} 缺失: {[n for n in nums if n not in hit_text]}")
        print()

    if total_num:
        print(f"汇总: 关键数字召回率 {total_hit}/{total_num} = {total_hit/total_num:.0%}")


if __name__ == "__main__":
    main()
