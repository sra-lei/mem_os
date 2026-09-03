"""pytest 配置：加载 YAML 测试用例、提供 memory provider / LLM / answer 等 fixture。

替代原 CLI (run_eval.py) 的入口职责：
  - 直接从 tests/test_cases/**/*.yaml 读取用例（不依赖 DB）
  - 通过命令行参数控制 memory-provider / llm / top-k
  - 每个用例隔离一个 MemoryProvider（user_id = case_id）

用法:
    pytest                              # 默认: mock provider, mock llm
    pytest --memory-provider base       # 使用 base (BM25) provider
    pytest --memory-provider struct      # 使用 struct provider
    pytest --llm deepseek               # 使用 DeepSeek 生成答案
    pytest --top-k 5                    # 检索 top-k=5
    pytest -m layer1                    # 只跑 layer1
    pytest -k bank_account              # 按名称过滤
"""
from __future__ import annotations

import json
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

TEST_CASES_DIR = ROOT / "tests" / "test_cases"

# layer (YAML category) -> pytest mark name
LAYER_MARKS = {"layer1": "layer1", "layer2": "layer2", "layer3": "layer3"}


def _parse_ts(value: Any) -> datetime:
    """YAML timestamp -> datetime，缺失时用当前时间兜底。"""
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
            try:
                return datetime.strptime(value, fmt)
            except ValueError:
                continue
    return datetime.utcnow()


def load_yaml_case(path: Path) -> dict:
    """读取单个 YAML 用例，返回标准化 dict。"""
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not data.get("test_id"):
        pytest.skip(f"invalid yaml (missing test_id): {path}")
    return data


def load_all_cases() -> list[tuple[str, dict]]:
    """加载全部 YAML 用例，返回 [(case_id, data), ...]。"""
    cases = []
    for path in sorted(TEST_CASES_DIR.glob("**/*.yaml")):
        data = load_yaml_case(path)
        cases.append((data["test_id"], data))
    return cases


# ------------------------------------------------------------------ #
#  Mock memory provider — 无外部依赖，用于 CI / 基线测试
# ------------------------------------------------------------------ #
class _MockMemoryProvider:
    """简单的内存 mock provider：存储对话消息，检索时返回全部内容。"""

    name = "mock"

    def __init__(self, user_id: str):
        self.user_id = user_id
        self._messages: list[str] = []

    def ingest(self, conversation) -> None:
        self._messages.extend(conversation.messages)

    def retrieve(self, query: str, top_k: int = 3) -> str:
        if not self._messages:
            return "（当前没有可用记忆）"
        lines = ["## 关于用户的长久记忆"]
        for msg in self._messages:
            lines.append(f"- {msg}")
        return "\n".join(lines)


def _build_provider(name: str, user_id: str):
    """工厂：mock 走本地实现，其余走 os_mem.build_memory_provider。"""
    if name == "mock":
        return _MockMemoryProvider(user_id=user_id)
    # 延迟导入 — 避免 module-level 触发 Milvus / LLM 连接
    from os_mem.provider import build_memory_provider
    return build_memory_provider(name, user_id=user_id)


# ------------------------------------------------------------------ #
#  命令行参数
# ------------------------------------------------------------------ #
def pytest_addoption(parser: pytest.Parser) -> None:
    group = parser.getgroup("memos-eval")
    group.addoption("--memory-provider", default="mock",
                    help="memory provider: mock | base | struct | full (default: mock)")
    group.addoption("--llm", default="mock",
                    help="llm client: mock | deepseek (default: mock)")
    group.addoption("--top-k", type=int, default=5,
                    help="retrieval top-k (default: 5)")


@pytest.fixture(scope="session")
def memory_provider_name(request: pytest.FixtureRequest) -> str:
    return request.config.getoption("--memory-provider")


@pytest.fixture(scope="session")
def llm_name(request: pytest.FixtureRequest) -> str:
    return request.config.getoption("--llm")


@pytest.fixture(scope="session")
def top_k(request: pytest.FixtureRequest) -> int:
    return request.config.getoption("--top-k")


# ------------------------------------------------------------------ #
#  核心流程 fixture — 延迟导入避免 module-level 副作用
# ------------------------------------------------------------------ #
@pytest.fixture
def llm_client(llm_name: str):
    from testing.llm import build_llm_client
    return build_llm_client(llm_name)


@pytest.fixture
def answer_generator(llm_client):
    from testing.llm import AnswerGenerator
    return AnswerGenerator(llm_client)


def pytest_generate_tests(metafunc: pytest.Metafunc) -> None:
    """为 test_eval_case 动态生成参数化用例。"""
    if "case_id" in metafunc.fixturenames and "case_data" in metafunc.fixturenames:
        cases = load_all_cases()
        ids = [cid for cid, _ in cases]
        metafunc.parametrize("case_id,case_data", cases, ids=ids)


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """根据 YAML category 自动打 layer 标记。"""
    for item in items:
        if hasattr(item, "callspec"):
            case_data = item.callspec.params.get("case_data")
            if isinstance(case_data, dict):
                category = str(case_data.get("category", "")).lower()
                mark_name = LAYER_MARKS.get(category)
                if mark_name:
                    item.add_marker(getattr(pytest.mark, mark_name))


# ------------------------------------------------------------------ #
#  用例执行流程
# ------------------------------------------------------------------ #
def run_case_pipeline(
    case_data: dict,
    memory_provider_name: str,
    answer_generator,
    top_k: int,
) -> tuple[str, str]:
    """执行单个用例的 ingest → retrieve → answer 流程。

    返回 (actual_answer, retrieved_memories)。
    """
    case_id = case_data["test_id"]
    provider = _build_provider(memory_provider_name, user_id=case_id)

    histories = case_data.get("conversation_histories", [])
    for conv in histories:
        conversation = _build_conversation(conv, case_id)
        provider.ingest(conversation)

    query = case_data.get("user_question")
    retrieved = provider.retrieve(query, top_k=top_k)
    completion = answer_generator.answer(query=query, memories=retrieved)
    return completion.text, retrieved


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
