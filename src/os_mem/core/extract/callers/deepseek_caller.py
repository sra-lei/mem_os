"""DeepSeek 提供方 extraction caller —— deepseek 专属逻辑全部内聚于此。

归属：``os_mem.core.extract`` 记忆提取域。通用上层（provider 无关的恢复循环
``ExtractionCore`` 在 ``extract_core.py``；干净契约 / 工厂在
``callers/base_caller.py``）；本文件承载「怎么跟 deepseek 要到合法结果」的全部
具体实现：

- **任务 prompt**：SYSTEM_PROMPT / REPAIR_PROMPT 模板（中文指令、json_object、
  {max_facts}/{categories_section} 占位均为当前模型调参形态），在
  ``outcome()`` / ``repair()`` 内渲染（max_facts 等直接读 memory_settings）、
  内容指纹（随评测 config_snapshot 落库）；
- generate 走 ``client.chat_outcome``（json_object 响应格式）；
- 恢复策略（截断检测、repair、对半切段、整段重试）不在本类重写——组合
  ``extract_core.ExtractionCore``，本文件只负责注入 deepseek 的低层能力
  （generate / repair_fn / dedup_fn / split_fn）；
- ``DeepSeekExtractionCaller`` 保留 ``outcome()`` / ``__call__()`` / ``repair()``
  鸭子接口——AB 脚本 Recorder 依赖 ``.outcome(...)`` 返回带 ``.usage`` 的
  ChatOutcome，且逐字读 ``__call__ = outcome().content``、``repair(partial)``
  走 ``client.chat``；旧调用方（build_caller / complete 注入）同样
  经此接口工作。

依赖方向（无环）：deepseek_caller → extract_core / callers.base_caller →
utils.extract_utils / utils.token_utils / model.models；→ configs /
infra.llm.base_client / utils.prompt_fp / vocab（函数内延迟 import）。具体实现
不被 callers 包顶层 import——工厂按 client 名在函数体内 lazy import。
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from functools import partial

from os_mem.configs.mem_settings import memory_settings
from os_mem.core.extract import ExtractionCore
from os_mem.core.extract.model import CallResult
from os_mem.core.extract.utils.extract_utils import (
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
# ``outcome()`` 用 memory_settings.DEEPSEEK_EXTRACT_MAX_FACTS 替换。
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
6. **跨会话属性复用**：若下方「已有属性清单」中已有与本次事实**同一实体、同一属性**的 key，
   必须沿用该 key，禁止为它发明近义新 key（清单里已有 `wire_amount`，就不要再写
   `amount` / `transfer_amount`）。清单里没有的、或语义并不相同的，再自拟精确 key；
   同一属性全程只用一个 key。

## 已有属性清单（该用户历史记忆里的属性名，供跨会话复用）
{attribute_hints_section}

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

# 内容指纹（prompt 迭代版本标识）—— 随评测 config_snapshot 落库，
# 使「某次跑分」与「当时提取 prompt 的内容」可对照（见 utils.prompt_fp）。
SYSTEM_PROMPT_FINGERPRINT = fingerprint(SYSTEM_PROMPT)
REPAIR_PROMPT_FINGERPRINT = fingerprint(REPAIR_PROMPT)

# 无历史属性时 {attribute_hints_section} 的中性占位：模板恒定（指纹只随模板变），
# 空词表 = 行为退化为「key 自拟」，不产生任何锚定约束。
_ATTRIBUTE_HINTS_EMPTY = '（暂无历史属性；自拟 key 后请保持稳定复用。）'


def render_attribute_hints_section(
    attribute_hints: Sequence[tuple[str, str]] | None,
) -> str:
    """把「该 user 已有 canonical attribute 词表」渲染成 prompt 段（D4-1.5）。

    ``attribute_hints`` 为 ``(category, attribute)`` 序列——权威源是
    ``struct_memories`` 的 lifecycle=current 行（读侧在 StrucMemService，
    本函数只做纯渲染，便于单测）。按 category 分组、组内字典序输出，
    空/无效一律回落中性占位（模板恒定，无词表 = 无锚定约束）。

    只提供**字符串上下文**，不含任何判断逻辑：要不要复用、复用哪条，
    由模型按其语义判断产出 key，系统的裁决仍是确定性代码（红线）。
    """
    if not attribute_hints:
        return _ATTRIBUTE_HINTS_EMPTY
    grouped: dict[str, list[str]] = {}
    for category, attribute in attribute_hints:
        name = str(attribute or '').strip()
        if not name:
            continue
        bucket = grouped.setdefault(str(category or 'other'), [])
        if name not in bucket:
            bucket.append(name)
    if not grouped:
        return _ATTRIBUTE_HINTS_EMPTY
    return '\n'.join(
        f'{category}: {", ".join(sorted(attributes))}'
        for category, attributes in sorted(grouped.items())
    )


class DeepSeekExtractionCaller:
    """DeepSeek 自愈提取 caller：恢复策略=代码（prompt 模板/渲染就在本模块）。

    - ``extract(dialog_text, *, validate, retries=2)``：任务侧唯一入口
      → CallResult{facts|None, stats}；validate 由任务注入；
    - ``outcome(dialog_text)`` / ``__call__(dialog_text)`` / ``repair(partial_json)``：
      鸭子接口保留（供 AB 脚本 Recorder 与旧 complete 调用方兼容）。
    """

    def __init__(
        self, client: ChatClient
    ) -> None:
        # prompt 模板固定用本模块的 SYSTEM_PROMPT/REPAIR_PROMPT 单源；
        # max_facts 渲染进 prompt，其余调参（model/temperature/输出预算）由
        # client 调用时直读 memory_settings，分段上限由任务层读 ChunkCaps。
        self._max_facts = memory_settings.DEEPSEEK_EXTRACT_MAX_FACTS
        self._client = client
        self._response_format = {'type': 'json_object'}
        self._core = ExtractionCore(
            generate=self._generate,
            repair_fn=self.repair,
            dedup_fn=dedup_facts,
            split_fn=split_text_midpoint
        )

    # ---- 任务侧干净契约 ------------------------------------------ #
    def extract(
        self,
        dialog_text: str,
        *,
        validate: Callable[[str], list],
        retries: int = 2,
        attribute_hints: Sequence[tuple[str, str]] | None = None,
    ) -> CallResult:
        """单段提取。

        ``attribute_hints``：该 user 已有 canonical attribute 词表（D4-1.5
        跨会话锚定），仅作 prompt 上下文——为空则完全走旧路径。
        """
        core = self._core_for(attribute_hints)
        facts, stats = core.extract(dialog_text, validate=validate, retries=retries)
        return CallResult(facts=facts, stats=stats)

    def _core_for(
        self, attribute_hints: Sequence[tuple[str, str]] | None
    ) -> ExtractionCore:
        """按次取恢复核心：无词表复用长期持有的实例（旧路径逐字节等价）；
        有词表则现绑一个（generate 闭包住词表）——避免共享可变状态被
        长对话并行分段竞争（词表是本次调用级参数，不是 caller 状态）。
        """
        if not attribute_hints:
            return self._core
        return ExtractionCore(
            generate=partial(self._generate, attribute_hints=attribute_hints),
            repair_fn=self.repair,
            dedup_fn=dedup_facts,
            split_fn=split_text_midpoint,
        )

    # ---- 低层能力：走 outcome() 单一调用路径，抽取 usage 三元组 ---- #
    def _generate(
        self,
        dialog_text: str,
        *,
        attribute_hints: Sequence[tuple[str, str]] | None = None,
    ) -> tuple[str, str | None, tuple[int, int] | None]:
        outcome = self.outcome(dialog_text, attribute_hints=attribute_hints)
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
    def outcome(
        self,
        dialog_text: str,
        *,
        attribute_hints: Sequence[tuple[str, str]] | None = None,
    ) -> ChatOutcome:
        """带 finish_reason 的提取调用（截断路由需要；兼容旧 _ExtractComplete）。

        直接走 ``client.chat_outcome``——``chat_outcome`` 是 ChatClient 协议的
        必备方法，finish_reason=length 由恢复循环识别并路由到对半切段。

        ``attribute_hints``：D4-1.5 跨会话属性锚定词表（(category, attribute)
        序列），渲染进 system prompt 的「已有属性清单」段；缺省 None =
        无锚定（清单段回落中性占位）。
        """
        from os_mem.vocab import render_categories_section
        # prompt 渲染 / 兼容适配 / 指纹
        system = (
            SYSTEM_PROMPT.replace(
                '{max_facts}', str(self._max_facts)
            ).replace(
                '{categories_section}', render_categories_section()
            ).replace(
                '{attribute_hints_section}',
                render_attribute_hints_section(attribute_hints),
            )
        )
        messages = [
            {'role': 'system', 'content': system},
            {'role': 'user', 'content': f'请从以下对话中提取结构化事实：\n\n{dialog_text}'},
        ]
        return self._client.chat_outcome(
            messages,
            response_format=self._response_format,
        )

    def __call__(self, dialog_text: str) -> str:
        return self.outcome(dialog_text).content

    def repair(self, partial_json: str) -> str:
        """拼装「修复截断 JSON」的 messages。"""
        system = (
            REPAIR_PROMPT
            + f'\n\n（注意：完整输出仍受 {self._max_facts} 条事实上限约束，若原输出已接近上限，'
            + '优先保留前面更重要的条目。）'
        )
        repair_message = [
            {'role': 'system', 'content': system},
            {'role': 'user', 'content': f'待修复的 JSON：\n\n{partial_json}'},
        ]
        return self._client.chat(
            repair_message, response_format=self._response_format,
        )


def build_caller(client: ChatClient) -> DeepSeekExtractionCaller:
    """具体实现标准工厂（build_extraction_caller 按 client.client_name() 分发到此）。"""
    return DeepSeekExtractionCaller(client)

