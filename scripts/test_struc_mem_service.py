"""测试 StructuredMemService：LLM 提取结构化事实 → 向量化 → mem_os 向量库 → 混合检索。

前置：
- 依赖已安装（uv sync），.env 已配置 DEEPSEEK_API_KEY / DASHSCOPE_API_KEY / MILVUS_URI / MILVUS_API_KEY
- 真实调用 DeepSeek（提取）、DashScope（embedding）、在线 Milvus（存储/检索）

用法：
    uv run python -m scripts.test_struc_mem_service [--msgs N]

可选 --msgs：限制参与提取的对话消息数（默认 15，减小 LLM 调用成本/耗时）。
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path

import yaml

from os_mem.core.services.struc_mem_service import get_structured_mem_service
from os_mem.models import Conversation

TEST_CASE = "tests/test_cases/layer1/01_bank_account_setup.yaml"
USER_ID = "layer1_01_bank_account"


def parse_ts(value) -> datetime:
    """YAML timestamp -> datetime（缺失/解析失败用当前时间）。"""
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
            try:
                return datetime.strptime(value, fmt)
            except ValueError:
                continue
    return datetime.utcnow()


def build_conversation(msgs_limit: int | None) -> Conversation:
    data = yaml.safe_load(Path(TEST_CASE).read_text(encoding="utf-8"))
    conv_raw = data["conversation_histories"][0]
    msgs = conv_raw["messages"]
    if msgs_limit:
        msgs = msgs[:msgs_limit]
    return Conversation(
        id=conv_raw["conversation_id"],
        user_id=USER_ID,
        summary="",
        messages=[json.dumps(m, ensure_ascii=False) for m in msgs],
        source_session_id=conv_raw["conversation_id"],
        started_at=parse_ts(conv_raw.get("timestamp")),
        ended_at=parse_ts(conv_raw.get("ended_at") or conv_raw.get("timestamp")),
        message_count=len(msgs),
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--msgs", type=int, default=15,
                        help="参与提取的对话消息数（默认 15，减小 LLM 成本）")
    args = parser.parse_args()

    svc = get_structured_mem_service()

    print(f"[1] 构造会话: {TEST_CASE}（前 {args.msgs} 条消息）")
    conv = build_conversation(args.msgs)

    print(f"[2] LLM 提取结构化事实 + 写入向量库（mem_os）...")
    svc.add_structured_memory(conv)

    print(f"[3] 混合检索: query='我的支票账户号码是多少？' user_id={USER_ID}")
    memories = svc.get_structured_memory(USER_ID, "我的支票账户号码是多少？", top_k=3)
    print(f"    命中 {len(memories)} 条:")
    for m in memories:
        print(f"      - [{m.category}/{m.key}] {m.fact} (value={m.value})")

    print(f"[4] 按类别检索: query='旅行偏好'（无则空列表为正常）")
    hits2 = svc.get_structured_memory(USER_ID, "旅行偏好", top_k=3)
    print(f"    命中 {len(hits2)} 条")


if __name__ == "__main__":
    main()
