"""提取 prompt A/B 回放实验（零副作用：不写 memories.db / Milvus / conv_meta）。

对比：线上 SYSTEM_PROMPT（≈850 tok）vs 候选精简版（≈420 tok）
会话：主 3 段（L2-10/13/20 c3，基线=今晨 run_7a2a539541 实测）只跑候选臂；
      加宽 2 段（L2-01 c1 / L1-04）跑 双臂（old vs new）真对比。

用法: uv run python scripts/ab_extract_prompt.py
输出: 每会话每臂一行统计（calls/截断/切段/repair/降级 + in/out tokens + facts +
      数字保真率 + 耗时），最后打印汇总。
"""
from __future__ import annotations

import json
import sys
import time
import yaml
from os_mem.extractor.fact_extractor import FactExtractor
from os_mem.extractor.deepseek_caller import (
    build_extract_complete,
    build_repair_messages,
)
from os_mem.extractor.regular_extractor import _NUMERIC_TOKENS, fact_tokens
from os_mem.infra.llm import get_llm_client
from os_mem.vocab import render_categories_section

# --------------------------------------------------------------------- #
#  候选精简 SYSTEM_PROMPT（目标：保住语言/类别/key 稳定/精确值三条硬规则，
#  砍散文重复与冗长示例，宁缺毋滥压超发）
# --------------------------------------------------------------------- #
CANDIDATE_SYSTEM = """你是一个信息提取助手，从对话中提取值得长期记忆的事实。
事实句语言与对话一致（英文对话一律输出英文 "User ..." 句式；中文对话才用中文）。

## 提取标准
只提取用户明确陈述、持久、对未来交互有价值的信息（身份/联系方式/财务/偏好/健康/工作/家庭/教育等）。
不要提取：客服客套、瞬时决定（如"今天天气不错"）、与用户无关的内容。
宁缺毋滥：拿不准是否长期价值的不提取；精确信息宁可多收（见规则 4），叙述性信息从简。

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
4. 金额/编号/日期/时间/百分比/账号等精确值原样保留（含 $、千分位逗号、连字符），
   对话中新产生或变更的精确信息同样逐条提取（如 "User's claim number is CLM-2024-894327"）。
5. 单次最多输出 {max_facts} 条——优先保留最有长期价值与最精确的条目。

## 输出格式（JSON 对象，facts 为数组）
{"facts": [{"fact": "User's checking account number is 4429853327", "category": "finance", "key": "checking_account_number", "value": "4429853327", "confidence": 0.9}]}
"""


# --------------------------------------------------------------------- #
#  候选 v1.5 精简 SYSTEM_PROMPT（v1 判废修正：v1 把「宁缺毋滥」全局化误伤精确值，
#  数字保真 0-25%。v1.5 = 精确值宁多勿漏 / 叙述宁缺毋滥，恢复 3 个精确值示例）
# --------------------------------------------------------------------- #
CANDIDATE_V15_SYSTEM = """你是一个信息提取助手，从对话中提取值得长期记忆的事实。
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


class CandidateComplete:
    """候选 prompt 的 complete 适配（outcome + repair，与线上 _ExtractComplete 同构）。"""

    def __init__(self, client, system_text: str = CANDIDATE_V15_SYSTEM) -> None:
        self._client = client
        self._response_format = {"type": "json_object"}
        from os_mem.configs.mem_settings import memory_settings

        self._system = system_text.replace(
            "{max_facts}", str(memory_settings.DEEPSEEK_EXTRACT_MAX_FACTS)
        ).replace("{categories_section}", render_categories_section())

    def _messages(self, dialog_text: str) -> list[dict[str, str]]:
        return [
            {"role": "system", "content": self._system},
            {"role": "user", "content": f"请从以下对话中提取结构化事实：\n\n{dialog_text}"},
        ]

    def outcome(self, dialog_text: str):
        return self._client.chat_outcome(
            self._messages(dialog_text), response_format=self._response_format
        )

    def __call__(self, dialog_text: str) -> str:
        return self.outcome(dialog_text).content

    def repair(self, partial_json: str) -> str:
        return self._client.chat(
            build_repair_messages(partial_json), response_format=self._response_format
        )


class Recorder:
    """包一层 complete，累计成功调用的 usage（in/out tokens）。"""

    def __init__(self, inner) -> None:
        self.inner = inner
        self.in_tokens = 0
        self.out_tokens = 0

    def outcome(self, text: str):
        oc = self.inner.outcome(text)
        if oc.usage is not None:
            self.in_tokens += getattr(oc.usage, "prompt_tokens", 0) or 0
            self.out_tokens += getattr(oc.usage, "completion_tokens", 0) or 0
        return oc

    def __call__(self, text: str) -> str:
        return self.outcome(text).content

    def repair(self, partial_json: str) -> str:
        return self.inner.repair(partial_json)


def load_session(case_path: str, conv_idx: int) -> str:
    data = yaml.safe_load(open(f"tests/test_cases/{case_path}"))
    conv = data["conversation_histories"][conv_idx]
    # 与 tests/eval/harness.py _build_conversation 同序列化（生产口径一致）
    return "\n".join(json.dumps(m, ensure_ascii=False) for m in conv["messages"])


def numeric_coverage(dialog_text: str, facts) -> float:
    """数字保真率：dialog 中含精确 token 的句子被产出事实 token 覆盖的比例。"""
    sentence_texts: list[str] = []
    for line in dialog_text.split("\n"):
        try:
            obj = json.loads(line)
        except Exception:
            continue
        content = obj.get("content") if isinstance(obj, dict) else ""
        if not content:
            continue
        for sent in __import__("re").split(r"(?<=[.!?。！？])\s+", content):
            sent = sent.strip()
            if 8 <= len(sent) <= 600 and _NUMERIC_TOKENS.search(sent):
                sentence_texts.append(sent)
    if not sentence_texts:
        return 1.0
    covered = set()
    union = set()
    for f in facts:
        union |= fact_tokens(f"{f.fact} {f.value or ''}")
    for i, sent in enumerate(sentence_texts):
        if fact_tokens(sent) and fact_tokens(sent) <= union:
            covered.add(i)
    return len(covered) / len(sentence_texts)


def run_arm(session_text: str, complete_factory, label: str) -> dict:
    client = get_llm_client()
    complete = Recorder(complete_factory(client))
    fx = FactExtractor()
    t0 = time.monotonic()
    base = fx.stats_snapshot()
    facts = fx.extract_structured_facts(session_text, complete=complete)
    elapsed = time.monotonic() - t0
    st = fx.stats_delta(base)
    return {
        "label": label,
        "calls": st["llm_calls"],
        "trunc": st["trunc_empties"],
        "split": st["split_recursions"],
        "repair": st["repair_calls"],
        "repair_ok": st["repair_ok"],
        "degrade": st["degrade_rows"],
        "in": complete.in_tokens,
        "out": complete.out_tokens,
        "facts": len(facts),
        "cov": numeric_coverage(session_text, facts),
        "sec": elapsed,
    }


def main() -> None:
    client = get_llm_client()
    # 变体：compare = 三臂（旧/v1/v15）同 5 段；v1/v15 = 单臂候选
    # 注意：client 已关思考（DEEPSEEK_THINKING=False）——v1/v1.5 早期判废数据
    # 是思考模式 ON 下测的（思考劣化输出污染了 prompt 文本对比），需关思考重测
    variant = sys.argv[1] if len(sys.argv) > 1 else "compare"
    compare = variant == "compare"
    if compare:
        factories = {
            "old": build_extract_complete,
            "v1": lambda c: CandidateComplete(c, CANDIDATE_SYSTEM),  # noqa: E731
            "v15": lambda c: CandidateComplete(c, CANDIDATE_V15_SYSTEM),  # noqa: E731
        }
        arms = ["old", "v1", "v15"]
        print(
            f"候选对比: compare（旧/v1/v15）· v1={len(CANDIDATE_SYSTEM)}字符 "
            f"v1.5={len(CANDIDATE_V15_SYSTEM)}字符"
        )
    else:
        system_text = (
            CANDIDATE_SYSTEM if variant == "v1" else CANDIDATE_V15_SYSTEM
        )
        print(f"候选 prompt 版本: {variant}（字符 {len(system_text)} ≈ {len(system_text) // 3} tok）")
        factories = {"v15" if variant != "v1" else "v1": lambda c, st=system_text: CandidateComplete(c, st)}
        arms = list(factories)

    all_plans = [
        # (case_path, conv_idx, label)
        ("layer2/10_travel_rebooking_chain.yaml", 2, "L2-10-c3"),
        ("layer2/13_home_services_cascade.yaml", 2, "L2-13-c3"),
        ("layer2/20_healthcare_coverage_changes.yaml", 2, "L2-20-c3"),
        ("layer2/01_multiple_vehicles.yaml", 0, "L2-01-c1"),
        ("layer1/04_airline_booking.yaml", 0, "L1-04"),
    ]
    results = []
    for case_path, conv_idx, label in all_plans:
        session_text = load_session(case_path, conv_idx)
        print(
            f"[{label}] {case_path} conv#{conv_idx} "
            f"字符={len(session_text)} 消息={len(session_text.splitlines())}"
        )
        for arm in arms:
            factory = factories[arm]
            r = run_arm(session_text, factory, f"{label}:{arm}")
            results.append(r)
            print(
                f"  {arm:>4} | calls={r['calls']} 截断={r['trunc']} 切段={r['split']} "
                f"repair={r['repair']}(ok{r['repair_ok']}) 降级={r['degrade']} "
                f"| in={r['in']:,} out={r['out']:,} | facts={r['facts']} "
                f"数字保真={r['cov']:.0%} | {r['sec']:.0f}s"
            )
            sys.stdout.flush()
        sys.stdout.flush()
    out_path = f"/tmp/ab_{variant}_results.json"
    with open(out_path, "w") as fh:
        json.dump(results, fh, ensure_ascii=False, indent=1)
    print(f"[saved] {out_path} ({len(results)} rows)")
    print("基线/历史见 docs/实验记录-提取prompt精简AB-2026-09-09.md")


if __name__ == "__main__":
    sys.exit(main())
