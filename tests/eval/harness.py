"""评测执行编排 —— 评测运行库（eval 包）的运行层。

职责：单个用例的 ingest → retrieve → answer 全流程（provider 构建 + 会话构造）。

只依赖 os_mem 与 eval.cases；不依赖 pytest 与 testing（管理侧）——
可在脚本/CLI/调试中直接调用。
"""

from __future__ import annotations

import json

from eval.cases import _parse_ts


def build_provider(name: str, user_id: str):
    """延迟导入 — 避免 module-level 触发 Milvus / LLM 连接。"""
    from os_mem.provider import build_memory_provider

    return build_memory_provider(name, user_id=user_id)


def _build_conversation(conv: dict, case_id: str):
    """构造 Conversation 对象 — 延迟导入避免 module-level 副作用。"""
    from os_mem.models import Conversation

    return Conversation(
        id=conv.get("conversation_id"),
        user_id=case_id,
        summary="",
        messages=[
            json.dumps(m, ensure_ascii=False)
            for m in conv.get("messages", [])
        ],
        source_session_id=conv.get("conversation_id"),
        started_at=_parse_ts(conv.get("timestamp")),
        ended_at=_parse_ts(conv.get("ended_at") or conv.get("timestamp")),
        message_count=len(conv.get("messages", [])),
    )


def run_case_pipeline(
    case_data: dict,
    memory_provider_name: str,
    answer_generator,
    top_k: int,
):
    """执行单个用例的 ingest → retrieve → answer 流程。

    返回 (completion, retrieved_memories)：completion 含文本与 token 消耗，
    retrieved_memories 为注入给 LLM 的记忆文本。
    """
    case_id = case_data["test_id"]
    provider = build_provider(memory_provider_name, user_id=case_id)

    histories = case_data.get("conversation_histories", [])
    for conv in histories:
        conversation = _build_conversation(conv, case_id)
        provider.ingest(conversation)

    query = case_data.get("user_question")
    retrieved = provider.retrieve(query, top_k=top_k)
    completion = answer_generator.answer(query=query, memories=retrieved)
    return completion, retrieved
