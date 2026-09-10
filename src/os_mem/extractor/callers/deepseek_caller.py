"""DeepSeek 提供方 extraction caller —— deepseek 专属逻辑全部内聚于此。

归属：``os_mem.extractor`` 记忆提取域。通用上层（provider 无关的恢复循环
``ExtractionCore`` 在 ``extraction_core.py``；干净契约 / 工厂在
``callers/framework.py``）；本文件承载「怎么跟 deepseek 要到合法结果」的全部
具体实现：

- **任务 prompt**：SYSTEM_PROMPT / REPAIR_PROMPT 模板（中文指令、json_object、
  {max_facts}/{categories_section} 占位均为当前模型调参形态）、``build_extract_messages``
  / ``build_repair_messages`` 渲染、内容指纹（随评测 config_snapshot 落库）、
  ``build_extract_complete`` 旧 client → complete 回调薄兼容（经工厂返回本 caller）；
- generate 走 ``client.chat_outcome``（json_object 响应格式）；
- 恢复策略（截断检测、repair、对半切段、整段重试）不在本类重写——组合
  ``extraction_core.ExtractionCore``，本文件只负责注入 deepseek 的低层能力
  （generate / repair_fn / dedup_fn / split_fn）；
- ``DeepSeekExtractionCaller`` 保留 ``outcome()`` / ``__call__()`` / ``repair()``
  鸭子接口——AB 脚本 Recorder 依赖 ``.outcome(...)`` 返回带 ``.usage`` 的
  ChatOutcome，且逐字读 ``__call__ = outcome().content``、``repair(partial)``
  走 ``client.chat``；旧调用方（build_extract_complete / complete 注入）同样
  经此接口工作。

依赖方向（无环）：deepseek_caller → extraction_core / callers.framework →
utils.extract_utils / utils.token_utils / model.models；→ configs /
infra.llm.base_client / utils.prompt_fp / vocab（函数内延迟 import）。具体实现
不被 callers 包顶层 import——工厂按 ``profile.caller`` 在函数体内 lazy import。
"""

from __future__ import annotations

from collections.abc import Callable

from os_mem.configs.mem_settings import memory_settings
from os_mem.extractor.extraction_core import ExtractionCore
from os_mem.extractor.llm_util import build_default_profile
from os_mem.extractor.model.models import CallResult, ModelProfile
from os_mem.extractor.utils.extract_utils import (
    MAX_TRUNC_SPLIT_DEPTH,
    dedup_facts,
    split_text_midpoint,
)
from os_mem.infra.llm.base_client import ChatClient, ChatOutcome
from os_mem.infra.logger import get_logger
from os_mem.utils.prompt_fp import fingerprint

_logger = get_logger('os_mem.extractor.callers.deepseek_caller')


# --------------------------------------------------------------------------- #
#  任务 prompt（deepseek 提取专用：中文指令 + json_object + 词表/max_facts 占位）
# --------------------------------------------------------------------------- #
# 提取任务系统提示：{max_facts} 为单次提取事实数量上限占位，调用时由
# ``build_extract_messages`` 用 memory_settings.DEEPSEEK_EXTRACT_MAX_FACTS 替换。
SYSTEM_PROMPT = """你是一个信息提取助手，从对话中提取值得长期记忆的事实。
事实句语言与对话一致（英文对话一律输出英文 "User ..." 句式；中文对话才用中文）。

## 提取标准
只提取用户明确陈述、持久、对未来交互有价值的信息（身份/联系方式/财务/偏好/健康/工作/家庭/教育等）。
不要提取：客服客套、瞬时决定（如"今天天气不错"）、与用户无关的内容。

## 规则
1. 每条事实独立成句（英文 "User ..."，中文 "用户 ..."）。
2. category 从以下列表选择：{categories_section}
3. key 是稳定字段名：同一概念只用一个 key、全程复用，禁止为同一事实发明近义 key。
   常见 key 参考：
   personal: full_name, date_of_birth, ssn
   contact: email, phone_number, address, emergency_contact
   finance: account_number, card_number, balance, claim_number, monthly_fee, amount
   preference: seat_preference, meal_preference, communication_preference
   health: allergy, medication, doctor_name, medical_history
   travel: confirmation_number, flight_number, departure_time, rental_confirmation
   education: course_name, professor, schedule
   family: spouse_name, child_name, relationship
   work: employer, occupation, income
   其他场景可自拟 key，但语义精确、同类复用。
4. **精确值宁多勿漏（最高优先级）**：含金额/编号/日期/时间/百分比/账号/号码的信息
   必须逐条原样提取，保留 $、千分位逗号、连字符等格式，不得省略、改写、四舍五入或合并。
   对话中**新产生或变更**的精确信息同样逐条提取。多个精确值并存的句子整体保留。例如：
   - "User's claim number is CLM-2024-894327"
   - "Refund of $1,600, minus 20% admin fee of $320, net refund $1,280"
   - "Weekly tuition is $617.50 (Emma $325 + Olivia $292.50)"
5. **叙述宁缺毋滥**：非精确的叙述性信息只保留确有长期价值的，拿不准的不提取；
   不提取对话中的过程性描述、客套与瞬时内容。精确值条目优先占位，单次最多输出 {max_facts} 条。

## 输出格式（JSON 对象，facts 为数组）
{"facts": [{"fact": "User's checking account number is 4429853327", "category": "finance", "key": "checking_account_number", "value": "4429853327", "confidence": 0.9}]}
"""


REPAIR_PROMPT = """你是 JSON 修复助手。以下是事实提取任务产生的一段
**不完整/截断的 JSON**（可能因输出长度限制被切断，或包含少量格式错误）。

任务：修复并补全它，输出**完整合法**的 JSON 对象（保持 {"facts": [...]} 结构）：
1. 保留所有已完整出现的 facts 条目，不要丢失、改写其中任何字段；
2. 若末尾条目被截断（缺闭合括号/引号/字段），按上下文补全其内容，
   或如果无法合理推断，删除该不完整条目；
3. 若内容完全不完整无法修复，则输出 {"facts": []}（不要输出空串或非 JSON 文本）。

只输出 JSON，不要任何解释文字。
"""


class DeepSeekExtractionCaller:
    """DeepSeek 自愈提取 caller：恢复策略=代码（prompt 模板/渲染就在本模块）。

    - ``extract(dialog_text, *, validate, retries=2)``：任务侧唯一入口
      → CallResult{facts|None, stats}；validate 由任务注入；
    - ``outcome(dialog_text)`` / ``__call__(dialog_text)`` / ``repair(partial_json)``：
      鸭子接口保留（供 AB 脚本 Recorder 与旧 complete 调用方兼容）。
    """

    def __init__(
        self, client: ChatClient, profile: ModelProfile | None = None
    ) -> None:
        # prompt 模板固定用本模块的 SYSTEM_PROMPT/REPAIR_PROMPT 单源（历史画像里
        # 曾预留 system_prompt/repair_prompt 覆盖字段，零消费已移除）。
        self._profile = profile or build_default_profile()
        self._client = client
        self._response_format = {'type': 'json_object'}
        self._core = ExtractionCore(
            generate=self._generate,
            repair_fn=self.repair,
            dedup_fn=dedup_facts,
            split_fn=split_text_midpoint,
            max_split_depth=MAX_TRUNC_SPLIT_DEPTH,
        )

    # ---- 任务侧干净契约 ------------------------------------------ #
    def extract(
        self,
        dialog_text: str,
        *,
        validate: Callable[[str], list],
        retries: int = 2,
    ) -> CallResult:
        facts, stats = self._core.extract(dialog_text, validate=validate, retries=retries)
        return CallResult(facts=facts, stats=stats)

    # ---- 低层能力：走 outcome() 单一调用路径，抽取 usage 三元组 ---- #
    def _generate(
        self, dialog_text: str
    ) -> tuple[str, str | None, tuple[int, int] | None]:
        outcome = self.outcome(dialog_text)
        # usage 口径与旧 _ExtractComplete/AB Recorder 一致（getattr 容错缺属性按 0）；
        # 恢复核心对每次 generate 累计 token 数（None → 0），见方案 §4 步骤 4。
        usage_tokens: tuple[int, int] | None = None
        if outcome.usage is not None:
            usage_tokens = (
                getattr(outcome.usage, 'prompt_tokens', 0) or 0,
                getattr(outcome.usage, 'completion_tokens', 0) or 0,
            )
        return outcome.content, outcome.finish_reason, usage_tokens

    # ---- 旧鸭子接口（AB Recorder / 旧调用方兼容） ------------------- #
    def outcome(self, dialog_text: str) -> ChatOutcome:
        """带 finish_reason 的提取调用（截断路由需要；兼容旧 _ExtractComplete）。

        client 支持 ``chat_outcome`` 时返回完整 outcome（含 finish_reason，length
        截断可由恢复循环识别）；否则退回 ``chat`` 包一层（无 finish 信息）。
        """
        messages = build_extract_messages(
            dialog_text, max_facts=self._profile.max_facts
        )
        chat_outcome = getattr(self._client, 'chat_outcome', None)
        if chat_outcome is not None:
            return chat_outcome(
                messages,
                response_format=self._response_format,
            )
        return ChatOutcome(
            self._client.chat(
                messages,
                response_format=self._response_format,
            )
        )

    def __call__(self, dialog_text: str) -> str:
        return self.outcome(dialog_text).content

    def repair(self, partial_json: str) -> str:
        return self._client.chat(
            build_repair_messages(partial_json, max_facts=self._profile.max_facts),
            response_format=self._response_format,
        )


def build_caller(client: ChatClient, profile: ModelProfile | None = None) -> DeepSeekExtractionCaller:
    """具体实现标准工厂（callers.build_extraction_caller 按 profile.caller 分发到此）。"""
    return DeepSeekExtractionCaller(client, profile=profile)


# --------------------------------------------------------------------------- #
#  prompt 渲染 / 兼容适配 / 指纹
# --------------------------------------------------------------------------- #
def build_extract_messages(
    dialog_text: str, *, max_facts: int | None = None
) -> list[dict[str, str]]:
    """拼装事实提取的完整 messages（system 提示 + 待提取对话）。

    system 由静态模板渲染而成：
    - ``{max_facts}``    → 单次提取事实上限：入参优先，None → settings 现值
      （默认路径行为不变；caller 按自身画像的 max_facts 传入）；
    - ``{categories_section}`` → active category 双语列表（fact_category 词表，
      见 os_mem.vocab）——改词表（停用/增补）即改提示，不动代码。
    """
    from os_mem.vocab import render_categories_section

    fact_limit = (
        max_facts
        if max_facts is not None
        else memory_settings.DEEPSEEK_EXTRACT_MAX_FACTS
    )
    system = (
        SYSTEM_PROMPT.replace(
            '{max_facts}', str(fact_limit)
        ).replace(
            '{categories_section}', render_categories_section()
        )
    )
    return [
        {'role': 'system', 'content': system},
        {'role': 'user', 'content': f'请从以下对话中提取结构化事实：\n\n{dialog_text}'},
    ]


def build_repair_messages(
    partial_json: str, max_facts: int | None = None
) -> list[dict[str, str]]:
    """拼装「修复截断 JSON」的 messages。"""
    fact_limit = max_facts or memory_settings.DEEPSEEK_EXTRACT_MAX_FACTS
    system = (
        REPAIR_PROMPT
        + f'\n\n（注意：完整输出仍受 {fact_limit} 条事实上限约束，若原输出已接近上限，'
        + '优先保留前面更重要的条目。）'
    )
    return [
        {'role': 'system', 'content': system},
        {'role': 'user', 'content': f'待修复的 JSON：\n\n{partial_json}'},
    ]


def build_extract_complete(client: ChatClient) -> DeepSeekExtractionCaller:
    """把通用 LLM client 适配为 ``FactExtractor`` 期望的提取回调（薄兼容层）。

    经工厂 ``build_extraction_caller`` 按默认画像分发，语义等价于直接构造本模块
    ``DeepSeekExtractionCaller``：具备旧 ``_ExtractComplete`` 的全部鸭子接口
    （``outcome`` / ``__call__`` / ``repair``，拼装走本模块渲染），同时携带新的
    ``extract(dialog_text, *, validate, retries) -> CallResult`` 契约。
    """
    return DeepSeekExtractionCaller(client)


# 内容指纹（prompt 迭代版本标识）—— 随评测 config_snapshot 落库，
# 使「某次跑分」与「当时提取 prompt 的内容」可对照（见 utils.prompt_fp）。
SYSTEM_PROMPT_FINGERPRINT = fingerprint(SYSTEM_PROMPT)
REPAIR_PROMPT_FINGERPRINT = fingerprint(REPAIR_PROMPT)
