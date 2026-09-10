"""事实提取任务 prompt 与 LLM 适配 —— 与通用客户端解耦。

归属：``os_mem.extractor`` 记忆提取域（2026-09-08 由 os_mem.utils 迁入
extraction/，2026-09-09 包更名 extraction→extractor），与 fact_extractor.py /
callers.py / regular_extractor.py 同域内聚；通用指纹工具仍留 ``os_mem.utils.prompt_fp``。

职责划分（2026-09 重构，为接入 LLM 网关铺路）：
- ``os_mem.infra.llm.deepseek_client.DeepSeekClient`` 是
  ``os_mem.infra.llm.base_client.ChatClient`` 契约的实现，只负责
  「通用 client 创建 + ``chat(messages)``」，不感知任何业务 prompt。
- 本模块负责「事实提取」这个任务的系统提示、消息拼装，并把任意满足
  ``ChatClient`` 契约的 client（DeepSeek / 未来网关实现）适配成提取链路需要的
  ``complete(dialog_text) -> raw_json`` 回调（见 ``FactExtractor``）。
  「LLM 调用 + 恢复策略」自 2026-09-09 起收敛于 ``callers.py`` 的 provider 自愈
  caller（恢复循环 + repair/截断路由 = provider 内部代码，validate 由任务注入），
  本模块的 ``build_extract_complete`` 保留为薄兼容（返回 caller 实例，鸭子接口
  outcome/__call__/repair 不变；拼装仍走本模块渲染）。

因此修改提取 prompt（含 {max_facts} 占位等）只需改本文件；
接入新的 LLM provider 时在 ``os_mem.infra.llm.factory`` 注册即可，
本文件与提取链路无需改动。
"""

from __future__ import annotations

from collections.abc import Callable

from os_mem.configs.mem_settings import memory_settings
from os_mem.infra.llm.base_client import ChatClient
from os_mem.utils.prompt_fp import fingerprint

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


def build_extract_messages(
    dialog_text: str, *, max_facts: int | None = None
) -> list[dict[str, str]]:
    """拼装事实提取的完整 messages（system 提示 + 待提取对话）。

    system 由静态模板渲染而成：
    - ``{max_facts}``    → 单次提取事实上限：入参优先，None → settings 现值
      （默认路径行为不变；caller 侧按自身画像的 max_facts 传入，见 callers）；
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


REPAIR_PROMPT = """你是 JSON 修复助手。以下是事实提取任务产生的一段
**不完整/截断的 JSON**（可能因输出长度限制被切断，或包含少量格式错误）。

任务：修复并补全它，输出**完整合法**的 JSON 对象（保持 {"facts": [...]} 结构）：
1. 保留所有已完整出现的 facts 条目，不要丢失、改写其中任何字段；
2. 若末尾条目被截断（缺闭合括号/引号/字段），按上下文补全其内容，
   或如果无法合理推断，删除该不完整条目；
3. 若内容完全不完整无法修复，则输出 {"facts": []}（不要输出空串或非 JSON 文本）。

只输出 JSON，不要任何解释文字。
"""


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


def build_extract_complete(client: ChatClient) -> Callable[[str], str]:
    """把通用 LLM client 适配为 ``FactExtractor`` 期望的提取回调（薄兼容层）。

    「LLM 调用 + 恢复策略」已收敛至提取域 caller 层（见
    docs/方案-提取任务与LLM模型画像解耦.md §2 v2）——本函数经工厂
    ``build_extraction_caller`` 按默认画像分发，当前返回
    ``DeepSeekExtractionCaller``：它具备旧 ``_ExtractComplete``
    的全部鸭子接口（``outcome`` / ``__call__`` / ``repair``，拼装仍用本模块的
    SYSTEM_PROMPT/REPAIR_PROMPT 渲染），同时携带新的 ``extract(dialog_text, *,
    validate, retries) -> CallResult`` 契约。函数体内延迟 import callers 避免循环
    依赖（具体实现 deepseek_caller 顶层 import 本模块的 prompt 渲染）。
    """
    from os_mem.extractor.callers import build_extraction_caller

    return build_extraction_caller(client)


# ------------------------------------------------------------------ #
#  内容指纹（prompt 迭代版本标识）—— 随评测 config_snapshot 落库，
#  使「某次跑分」与「当时提取 prompt 的内容」可对照（见 utils.prompt_fp）。
# ------------------------------------------------------------------ #
SYSTEM_PROMPT_FINGERPRINT = fingerprint(SYSTEM_PROMPT)
REPAIR_PROMPT_FINGERPRINT = fingerprint(REPAIR_PROMPT)
