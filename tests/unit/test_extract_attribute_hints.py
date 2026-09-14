"""D4-1.5 提取期属性锚定（跨会话 canonical attribute 词表注入）单测。

覆盖三段：
1. **渲染**（``render_attribute_hints_section``）：分组/排序/空占位；
2. **透传**（caller → prompt；FactExtractor → caller）：词表只进 system prompt
   的「已有属性清单」段，repair 不受污染，缺省 None = 旧行为（中性占位）；
3. **读侧**（``read_attribute_vocabulary``）：只读 current 行、排除兜底属性、
   稳定排序 + 上限截断、异常回落空词表（提取绝不因锚定读取失败而中断）。

全程无 LLM / 无网络 / 无 Milvus：DB 走 tmp 文件（monkeypatch db_path）。
"""
from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any, Iterator

import pytest

if TYPE_CHECKING:
    from os_mem.core.extract.model import CallResult


@pytest.fixture()
def tmp_memory_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """把记忆库指向临时文件（与 test_struct_mem_sqlite 同款隔离夹具）。"""
    from os_mem.infra.storage import mem_storage

    db_file = tmp_path / 'test_memories.db'
    monkeypatch.setattr(mem_storage.MemoryDatabase, 'db_path', db_file)
    mem_storage.MemoryDatabase._engines.clear()
    yield db_file
    mem_storage.MemoryDatabase._engines.clear()


# --------------------------------------------------------------------------- #
#  1. 渲染
# --------------------------------------------------------------------------- #
def test_render_hints_groups_and_sorts() -> None:
    from os_mem.core.extract.callers.deepseek_caller import (
        render_attribute_hints_section,
    )

    section = render_attribute_hints_section(
        [
            ('finance', 'wire_date'),
            ('health', 'medication'),
            ('finance', 'wire_amount'),
            ('finance', 'wire_amount'),  # 重复 → 去重
        ]
    )
    assert section == (
        'finance: wire_amount, wire_date\n'
        'health: medication'
    )
    # 同一词表输入顺序不同 → 输出稳定（prompt 确定性）
    assert section == render_attribute_hints_section(
        [('finance', 'wire_amount'), ('health', 'medication'), ('finance', 'wire_date')]
    )


def test_render_hints_empty_falls_back_to_placeholder() -> None:
    from os_mem.core.extract.callers.deepseek_caller import (
        _ATTRIBUTE_HINTS_EMPTY,
        render_attribute_hints_section,
    )

    for empty in (None, [], [('finance', '')], [('finance', None)]):  # type: ignore[list-item]
        assert render_attribute_hints_section(empty) == _ATTRIBUTE_HINTS_EMPTY


def test_prompt_template_carries_hints_placeholder_and_rule() -> None:
    from os_mem.core.extract.callers.deepseek_caller import SYSTEM_PROMPT

    assert '{attribute_hints_section}' in SYSTEM_PROMPT
    assert '已有属性清单' in SYSTEM_PROMPT
    assert '跨会话属性复用' in SYSTEM_PROMPT
    # 规则须点明「同实体同属性沿用 key」与「清单外自拟」，否则锚定语义不成立
    assert '同一实体、同一属性' in SYSTEM_PROMPT
    # 指纹作用于模板：占位不渲染（随 config_snapshot 落库）
    assert '{attribute_hints_section}' in SYSTEM_PROMPT.split('## 输出格式')[0]


# --------------------------------------------------------------------------- #
#  2. 透传（caller → prompt / FactExtractor → caller）
# --------------------------------------------------------------------------- #
class _CapturingChatClient:
    """记录 chat_outcome 收到的 messages 的假 client（可返回合法 facts JSON）。"""

    def __init__(self, content: str = '') -> None:
        self._content = content
        self.last_messages: list[dict[str, str]] | None = None
        self.last_repair_messages: list[dict[str, str]] | None = None

    def client_name(self) -> str:
        return 'deepseek'

    def chat_outcome(
        self,
        messages: list[dict[str, str]],
        *,
        response_format: dict | None = None,
    ) -> Any:
        from os_mem.infra.llm.base_client import ChatOutcome

        self.last_messages = messages
        return ChatOutcome(self._content, finish_reason='stop')

    def chat(self, messages: list[dict[str, str]], **kwargs: Any) -> str:
        self.last_repair_messages = messages
        return self._content


def _facts_json(*items: tuple[str, str, str, str]) -> str:
    import json

    return json.dumps(
        {
            'facts': [
                {
                    'fact': fact,
                    'category': category,
                    'key': key,
                    'value': value,
                    'confidence': 0.9,
                }
                for fact, category, key, value in items
            ]
        }
    )


def test_caller_outcome_renders_hints_into_system_prompt(tmp_memory_db: Path) -> None:
    from os_mem.core.extract.callers.deepseek_caller import (
        _ATTRIBUTE_HINTS_EMPTY,
        DeepSeekExtractionCaller,
    )

    client = _CapturingChatClient()
    caller = DeepSeekExtractionCaller(client)

    # 无词表：占位已渲染（无残留），system 里不出现任何属性清单内容
    caller.outcome('你好')
    assert client.last_messages is not None
    system = client.last_messages[0]['content']
    assert '{attribute_hints_section}' not in system
    assert _ATTRIBUTE_HINTS_EMPTY in system

    # 有词表：清单渲染进 system prompt
    caller.outcome(
        '你好',
        attribute_hints=[('finance', 'wire_amount'), ('health', 'medication')],
    )
    assert client.last_messages is not None
    system = client.last_messages[0]['content']
    assert '{attribute_hints_section}' not in system
    assert 'finance: wire_amount' in system
    assert 'health: medication' in system


def test_caller_extract_forward_hints_only_changes_system_prompt(
    tmp_memory_db: Path,
) -> None:
    from os_mem.core.extract.callers.deepseek_caller import DeepSeekExtractionCaller
    from os_mem.core.extract.extractor.fact_extractor import FactExtractor

    content = _facts_json(
        ("User's wire amount is $95,000", 'finance', 'wire_amount', '$95,000'),
    )
    client = _CapturingChatClient(content)
    caller = DeepSeekExtractionCaller(client)

    result = caller.extract(
        '会话文本',
        validate=FactExtractor.validate_response,
        attribute_hints=[('finance', 'wire_amount')],
    )
    assert result.facts and result.facts[0].key == 'wire_amount'
    assert client.last_messages is not None
    assert 'finance: wire_amount' in client.last_messages[0]['content']
    # 用户消息不含词表（词表是系统上下文，不是对话内容）
    assert 'wire_amount' not in client.last_messages[1]['content']

    # repair 走独立 messages，不得被锚定段污染（REPAIR 模板不含该占位）
    caller.repair('{"facts": [')
    assert client.last_repair_messages is not None
    assert 'wire_amount' not in client.last_repair_messages[0]['content']


class _RecordingCaller:
    """记录每次 extract 调用收到的 attribute_hints（验证任务层透传）。"""

    def __init__(self) -> None:
        from os_mem.core.extract.model import CallResult
        from os_mem.core.extract.utils.extract_utils import empty_extraction_stats

        self.seen_hints: list[Any] = []
        self._stats = empty_extraction_stats()

    def extract(
        self,
        dialog_text: str,
        *,
        validate: Any,
        retries: int = 2,
        attribute_hints: Any = None,
    ) -> CallResult:
        from os_mem.core.extract.model import CallResult
        from os_mem.models.mem_models import MemoryFact

        self.seen_hints.append(attribute_hints)
        facts = [
            MemoryFact(
                fact='用户电汇金额 $95,000',
                category='finance',
                key='wire_amount',
                value='$95,000',
                confidence=0.9,
            )
        ]
        return CallResult(facts=facts, stats=dict(self._stats))


def test_fact_extractor_forwards_hints_single_chunk(tmp_memory_db: Path) -> None:
    from os_mem.core.extract.extractor.fact_extractor import FactExtractor

    caller = _RecordingCaller()
    hints = [('finance', 'wire_amount')]
    facts = FactExtractor().extract_structured_facts(
        '短对话文本', caller=caller, attribute_hints=hints
    )
    assert caller.seen_hints == [hints]
    assert facts and facts[0].key == 'wire_amount'


def test_fact_extractor_forwards_hints_to_every_chunk(tmp_memory_db: Path) -> None:
    from os_mem.core.extract.extractor.fact_extractor import FactExtractor
    from os_mem.core.extract.model import ChunkCaps

    caller = _RecordingCaller()
    hints = [('finance', 'wire_amount'), ('health', 'medication')]
    lines = [
        f'{{"role": "user", "content": "消息 {index}"}}' for index in range(12)
    ]
    FactExtractor().extract_structured_facts(
        '\n'.join(lines),
        caller=caller,
        chunk_caps=ChunkCaps(max_chars=4500, max_msgs=3, overlap=0),
        attribute_hints=hints,
    )
    assert len(caller.seen_hints) > 1  # 确实走了分段并行路径
    assert all(seen == hints for seen in caller.seen_hints)


def test_fact_extractor_without_hints_keeps_none(tmp_memory_db: Path) -> None:
    from os_mem.core.extract.extractor.fact_extractor import FactExtractor

    caller = _RecordingCaller()
    FactExtractor().extract_structured_facts('短对话文本', caller=caller)
    assert caller.seen_hints == [None]


# --------------------------------------------------------------------------- #
#  3. 读侧（read_attribute_vocabulary）
# --------------------------------------------------------------------------- #
def _seed_rows(user_id: str, rows: list[dict[str, Any]]) -> None:
    from os_mem.entries.mem_models import StructuredMemory
    from os_mem.infra.storage import get_session

    with get_session() as session:
        for row in rows:
            session.add(StructuredMemory(user_id=user_id, **row))
        session.commit()


def test_read_vocabulary_only_current_rows(tmp_memory_db: Path) -> None:
    from os_mem.core.services.struc_mem_service import read_attribute_vocabulary

    _seed_rows(
        'u1',
        [
            {'category': 'finance', 'key': 'wire_amount', 'attribute': 'wire_amount',
             'entity_ref': 'SELF', 'lifecycle': 'current', 'fact': 'a', 'value': '1'},
            {'category': 'finance', 'key': 'wire_amount', 'attribute': 'wire_amount',
             'entity_ref': 'SELF', 'lifecycle': 'superseded', 'fact': 'b', 'value': '2'},
            {'category': 'health', 'key': 'original_medication', 'attribute': 'medication',
             'entity_ref': 'SELF', 'lifecycle': 'historical', 'fact': 'c', 'value': '3'},
            {
                'category': 'health', 'key': 'medication', 'attribute': 'medication',
                'entity_ref': 'SELF', 'lifecycle': 'current', 'fact': 'd', 'value': '4',
            },
            # 兜底/降级行的属性不进锚定词表（复用它只会让后续会话也退化）
            {
                'category': 'other', 'key': 'verbatim_1', 'attribute': 'verbatim_1',
                'entity_ref': 'SELF', 'lifecycle': 'current', 'fact': 'e', 'value': '5',
            },
            {
                'category': 'other', 'key': 'raw_conversation', 'attribute': 'raw_conversation',
                'entity_ref': 'SELF', 'lifecycle': 'current', 'fact': 'f', 'value': '6',
            },
        ],
    )
    assert read_attribute_vocabulary('u1') == [
        ('finance', 'wire_amount'),
        ('health', 'medication'),
    ]
    # 其他用户互不串味
    assert read_attribute_vocabulary('u2') == []


def test_read_vocabulary_respects_max_items(tmp_memory_db: Path) -> None:
    from os_mem.core.services.struc_mem_service import read_attribute_vocabulary

    _seed_rows(
        'u1',
        [
            {'category': 'finance', 'key': key, 'attribute': key,
             'entity_ref': 'SELF', 'lifecycle': 'current', 'fact': key, 'value': key}
            for key in ('c_amount', 'a_amount', 'b_amount')
        ],
    )
    assert read_attribute_vocabulary('u1', max_items=2) == [
        ('finance', 'a_amount'),
        ('finance', 'b_amount'),
    ]


def test_read_vocabulary_failure_returns_empty(
    tmp_memory_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """读侧异常不得让提取中断——回落空词表（= 无锚定，等价旧行为）。"""
    from os_mem.core.services import struc_mem_service

    def _boom() -> Any:
        raise RuntimeError('db down')

    monkeypatch.setattr(struc_mem_service, 'get_session', _boom)
    assert struc_mem_service.read_attribute_vocabulary('u1') == []
