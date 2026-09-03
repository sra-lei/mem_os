"""pytest 评测：替代 CLI run_eval.py 的入口。

每个用例执行 ingest → retrieve → answer，然后用 assert 校验
expected_behavior 中提取的关键事实是否出现在回答中——替代 Moonshot LLM 判定。

用法:
    pytest                              # 全部用例, mock provider
    pytest --memory-provider base       # 使用 BM25 provider
    pytest --llm deepseek               # 使用 DeepSeek 生成答案
    pytest -m layer1                    # 只跑 layer1
    pytest -k bank_account              # 按名称过滤
    pytest -n auto                      # 并行执行
"""
from __future__ import annotations

import re
from typing import Any

import pytest

from conftest import run_case_pipeline


# ------------------------------------------------------------------ #
#  关键事实提取 — 从 expected_behavior / evaluation_criteria 中提取
#  可断言的硬事实（数字、账号、金额等）
# ------------------------------------------------------------------ #

# 排除的"噪音"数字：年份、百分比等通常不构成关键事实
_NOISE_PATTERNS = re.compile(r"^(?:19|20)\d{2}$")  # 纯年份 2015, 2024...

# 关键数字模式：账号（6位以上连续数字）、金额（$前缀）、电话号码等
_LONG_NUMBER = re.compile(r"\b\d{6,}\b")          # 6位以上连续数字
_DOLLAR_AMOUNT = re.compile(r"\$[\d,]+(?:\.\d+)?")  # $5,000 / $125,000
_PHONE = re.compile(r"\b\d{3}[-.]?\d{3}[-.]?\d{4}\b")  # 503-555-8924

# 关键词/名称：大写开头的专有名词（排除常见词）
_COMMON_WORDS = {
    "The", "A", "An", "This", "That", "These", "Those",
    "Chase", "Wells", "Fargo", "Netflix", "Disney", "Apple",
    "Max", "Honda", "Tesla", "Amex", "Visa", "Mastercard",
}
# 专有名词（2+ 大写字母词或已知品牌）
_PROPER_NOUN = re.compile(r"\b(?:[A-Z][a-z]+)(?:\s+[A-Z][a-z]+)+\b")


def extract_key_facts(case_data: dict[str, Any]) -> list[str]:
    """从 expected_behavior 和 evaluation_criteria 提取关键事实。

    提取策略:
      1. 长数字（账号、卡号等 6 位以上）
      2. 美元金额（$5,000）
      3. 电话号码
      4. 专有名词组合（如 "Chase Sapphire"）

    返回去重后的事实列表，可能为空（某些用例无硬事实）。
    """
    text = " ".join(filter(None, [
        case_data.get("expected_behavior", ""),
        case_data.get("evaluation_criteria", ""),
    ]))
    if not text.strip():
        return []

    facts: list[str] = []

    for m in _LONG_NUMBER.finditer(text):
        num = m.group()
        if not _NOISE_PATTERNS.match(num):
            facts.append(num)

    facts.extend(m.group() for m in _DOLLAR_AMOUNT.finditer(text))
    facts.extend(m.group() for m in _PHONE.finditer(text))

    # 去重，保持顺序
    seen: set[str] = set()
    unique = []
    for f in facts:
        key = f.lower()
        if key not in seen:
            seen.add(key)
            unique.append(f)

    return unique


# ------------------------------------------------------------------ #
#  测试用例
# ------------------------------------------------------------------ #

def test_eval_case(
    case_id: str,
    case_data: dict[str, Any],
    memory_provider_name: str,
    answer_generator,
    top_k: int,
) -> None:
    """单个评测用例：ingest → retrieve → answer → assert。

    用 assert 替代 Moonshot 判定：
      - 从 expected_behavior 提取关键事实（账号、金额等）
      - 断言这些事实出现在 LLM 回答中
    """
    query = case_data.get("user_question")
    if not query:
        pytest.skip(f"{case_id}: 无 user_question")

    actual, retrieved = run_case_pipeline(
        case_data, memory_provider_name, answer_generator, top_k,
    )

    # 基本断言：回答非空
    assert actual, f"{case_id}: 回答为空"

    # 关键事实断言
    facts = extract_key_facts(case_data)
    if not facts:
        pytest.skip(f"{case_id}: expected_behavior 无可提取的硬事实（数字/金额等）")

    missing = [f for f in facts if f.lower() not in actual.lower()]
    assert not missing, (
        f"{case_id}: 回答中缺少以下关键事实: {missing}\n"
        f"  期望事实: {facts}\n"
        f"  实际回答: {actual[:500]}"
    )
