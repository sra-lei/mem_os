"""caller 直读 settings + 观测增强（token 记账）单元测试。

覆盖（不依赖真实 LLM / Milvus）：
  - ``ChunkCaps.from_settings``：默认分段上限 = settings 现值；
  - ``DeepSeekExtractionCaller``：settings 的 max_facts 渲染进
    system/repair prompt（fake client 捕获 messages 断言）；
  - ``extract_structured_facts(chunk_caps=...)``：分段上限由 caps 供给任务层
    （fake complete 计数断言被切多段）；
  - token 记账：恢复核心按 generate 三元组的 usage_tokens 累计 in/out
    （含截断空返回烧计入）；caller 从 outcome.usage 映射 token 数。

用法:
    pytest tests/unit/test_extractor_profile.py
"""
from __future__ import annotations

from types import SimpleNamespace

from os_mem.configs.mem_settings import memory_settings
from os_mem.core.extract import ExtractionCore
from os_mem.core.extract.callers.deepseek_caller import DeepSeekExtractionCaller
from os_mem.core.extract.extractor.fact_extractor import FactExtractor
from os_mem.core.extract.model import ChunkCaps
from os_mem.infra.llm.base_client import ChatOutcome

_VALID_FACTS = (
    '[{"fact":"用户账户 4429853327","category":"finance",'
    '"key":"account","value":"4429853327","confidence":0.95}]'
)


# ------------------------------------------------------------------ #
#  ChunkCaps：默认分段上限 = settings 现值
# ------------------------------------------------------------------ #
class TestChunkCapsSettings:
    def test_chunk_caps_from_settings_matches_settings(self) -> None:
        caps = ChunkCaps.from_settings()
        assert caps.max_chars == memory_settings.DEEPSEEK_EXTRACT_MAX_CHARS
        assert caps.max_msgs == memory_settings.DEEPSEEK_EXTRACT_MAX_MSGS
        assert caps.overlap == memory_settings.DEEPSEEK_EXTRACT_OVERLAP


# ------------------------------------------------------------------ #
#  caller：settings 的 max_facts 渲染进 prompt（fake client 捕获 messages）
# ------------------------------------------------------------------ #
class CapturingChatClient:
    """记录 chat / chat_outcome 收到的 messages 的假 client（无网络）。"""

    def __init__(
        self,
        usage: object | None = None,
        finish_reason: str = 'stop',
    ) -> None:
        self.usage = usage
        self.finish_reason = finish_reason
        self.last_messages: list[dict[str, str]] | None = None

    def client_name(self) -> str:
        return 'deepseek'

    def chat_outcome(
        self,
        messages: list[dict[str, str]],
        *,
        response_format: dict | None = None,
        retries: int = 3,
    ) -> ChatOutcome:
        self.last_messages = messages
        return ChatOutcome(_VALID_FACTS, self.finish_reason, self.usage)

    def chat(
        self,
        messages: list[dict[str, str]],
        *,
        response_format: dict | None = None,
        retries: int = 3,
    ) -> str:
        self.last_messages = messages
        return _VALID_FACTS


class TestCallerPrompt:
    def _system(self, messages: list[dict[str, str]]) -> str:
        return next(m for m in messages if m['role'] == 'system')['content']

    def test_generate_renders_settings_max_facts(self) -> None:
        """settings 的 max_facts → _generate 发出的 system prompt 含「最多 N 条」。"""
        client = CapturingChatClient()
        caller = DeepSeekExtractionCaller(client)
        content, finish_reason, usage_tokens = caller._generate('对话文本')
        assert content == _VALID_FACTS
        assert finish_reason == 'stop'
        assert usage_tokens is None  # 假 client 无 usage → None
        assert client.last_messages is not None
        assert (
            f'最多输出 {memory_settings.DEEPSEEK_EXTRACT_MAX_FACTS} 条'
            in self._system(client.last_messages)
        )

    def test_repair_renders_settings_max_facts(self) -> None:
        """repair prompt 同样按 settings 的 max_facts 约束。"""
        client = CapturingChatClient()
        caller = DeepSeekExtractionCaller(client)
        caller.repair('{"facts": [')
        assert client.last_messages is not None
        assert (
            f'{memory_settings.DEEPSEEK_EXTRACT_MAX_FACTS} 条事实上限约束'
            in self._system(client.last_messages)
        )


# ------------------------------------------------------------------ #
#  任务层：chunk_caps 供给分段（complete 路径同样尊重 caps）
# ------------------------------------------------------------------ #
class CountingComplete:
    """每次调用返回合法单条事实并计数的假 complete（无 outcome/repair）。"""

    def __init__(self) -> None:
        self.calls = 0

    def __call__(self, text: str) -> str:
        self.calls += 1
        return _VALID_FACTS


class TestExtractChunkCaps:
    def test_small_chunk_caps_splits_dense_dialog(self) -> None:
        """40 条短消息 + 小 caps（max_msgs=10）→ 被切多段、多次 LLM 调用。"""
        lines = [f'第{i}条短消息' for i in range(1, 41)]
        dialog_text = '\n'.join(lines)
        fake = CountingComplete()
        fx = FactExtractor()
        caps = ChunkCaps(max_chars=100000, max_msgs=10, overlap=2)
        out = fx.extract_structured_facts(
            dialog_text, retries=1, complete=fake, chunk_caps=caps,
        )
        assert fake.calls >= 2
        assert len(out) == 1
        assert out[0].key == 'account'

    def test_within_caps_single_chunk(self) -> None:
        """caps 放宽（max_msgs=100）→ 40 条不切段，单次调用。"""
        lines = [f'第{i}条短消息' for i in range(1, 41)]
        fake = CountingComplete()
        fx = FactExtractor()
        caps = ChunkCaps(max_chars=100000, max_msgs=100, overlap=2)
        fx.extract_structured_facts(
            '\n'.join(lines), retries=1, complete=fake, chunk_caps=caps,
        )
        assert fake.calls == 1

    def test_default_chunk_caps_matches_settings(self) -> None:
        """不传 chunk_caps → settings 现值（40 条短消息触发默认 35 条上限切段）。"""
        lines = [f'第{i}条短消息' for i in range(1, 41)]
        fake = CountingComplete()
        fx = FactExtractor()
        fx.extract_structured_facts('\n'.join(lines), retries=1, complete=fake)
        assert fake.calls >= 2


# ------------------------------------------------------------------ #
#  token 记账：核心三元组 usage_tokens 累计
# ------------------------------------------------------------------ #
class TestCoreTokenLedger:
    def test_successful_generate_counts_tokens(self) -> None:
        def fake_generate(text: str) -> tuple[str, str | None, tuple[int, int] | None]:
            return _VALID_FACTS, 'stop', (100, 200)

        core = ExtractionCore(generate=fake_generate)
        facts, stats = core.extract(
            '对话文本', validate=FactExtractor.validate_response, retries=2
        )
        assert facts
        assert stats['llm_calls'] == 1
        assert stats['in_tokens'] == 100
        assert stats['out_tokens'] == 200

    def test_truncated_empty_burn_tokens_still_counted(self) -> None:
        """截断空返回（段不可切，放弃降级）也计入 token——烧掉的钱要记账。"""

        def empty_generate(
            text: str,
        ) -> tuple[str, str | None, tuple[int, int] | None]:
            return '', 'length', (300, 400)

        def never_split(text: str) -> tuple[str, str] | None:
            return None  # 单行/不可切 → trunc_empties 计数后放弃该段

        core = ExtractionCore(generate=empty_generate, split_fn=never_split)
        facts, stats = core.extract(
            '对话文本', validate=FactExtractor.validate_response, retries=2
        )
        assert facts is None
        assert stats['trunc_empties'] == 1
        assert stats['llm_calls'] == 1
        assert stats['in_tokens'] == 300
        assert stats['out_tokens'] == 400

    def test_generate_without_usage_counts_zero(self) -> None:
        """旧 complete 路径（usage_tokens=None）→ token 记账为 0，不崩。"""

        def plain_generate(
            text: str,
        ) -> tuple[str, str | None, tuple[int, int] | None]:
            return _VALID_FACTS, None, None

        core = ExtractionCore(generate=plain_generate)
        facts, stats = core.extract(
            '对话文本', validate=FactExtractor.validate_response, retries=1
        )
        assert facts
        assert stats['in_tokens'] == 0
        assert stats['out_tokens'] == 0


# ------------------------------------------------------------------ #
#  caller：outcome.usage → token 记账（chat_outcome 路径）
# ------------------------------------------------------------------ #
class TestCallerUsageLedger:
    def test_outcome_usage_mapped_into_stats(self) -> None:
        usage = SimpleNamespace(prompt_tokens=500, completion_tokens=123)
        client = CapturingChatClient(usage=usage)
        caller = DeepSeekExtractionCaller(client)
        result = caller.extract(
            '对话文本', validate=FactExtractor.validate_response, retries=1
        )
        assert result.facts
        assert result.stats['llm_calls'] == 1
        assert result.stats['in_tokens'] == 500
        assert result.stats['out_tokens'] == 123

    def test_usage_missing_counts_zero(self) -> None:
        client = CapturingChatClient(usage=None)
        caller = DeepSeekExtractionCaller(client)
        result = caller.extract(
            '对话文本', validate=FactExtractor.validate_response, retries=1
        )
        assert result.stats['in_tokens'] == 0
        assert result.stats['out_tokens'] == 0

    def test_extractor_absorbs_caller_tokens_into_snapshot_delta(self) -> None:
        """extract_structured_facts caller 路径把 token 记入实例计数（提取账口径）。"""
        usage = SimpleNamespace(prompt_tokens=40, completion_tokens=9)
        client = CapturingChatClient(usage=usage)
        from os_mem.core.extract.callers.base_caller import build_extraction_caller

        caller = build_extraction_caller(client)
        fx = FactExtractor()
        base = fx.stats_snapshot()
        fx.extract_structured_facts('对话文本', caller=caller)
        delta = fx.stats_delta(base)
        assert delta['in_tokens'] == 40
        assert delta['out_tokens'] == 9
        assert delta['llm_calls'] == 1
