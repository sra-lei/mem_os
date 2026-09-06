"""LLM-as-Judge evaluation.

IMPORTANT design point: grading uses `evaluation_criteria` (the multi-line
grading rubric in the test-case YAML), NOT `expected_answer` — 41/60 YAMLs have
no expected_behavior field, and the rubric is the authoritative scoring input.

Score convention: 0.0 ~ 1.0; passed = score >= threshold (default 0.7, matches
EvalView需求文档.md phase-4 design).
"""
from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from typing import Protocol

from openai import OpenAI

from eval.config import settings


@dataclass
class JudgeResult:
    score: float            # 0.0 ~ 1.0
    passed: bool
    reasoning: str | None = None
    error: str | None = None

class JudgeProvider(Protocol):
    """Grades one answer. Note: it receives the RUBRIC (evaluation_criteria),
    not a fixed expected answer string."""
    def evaluate(
        self,
        query: str,
        criteria: str | None,
        actual: str,
    ) -> JudgeResult:
        ...


# 普通字符串模板 + .format()（criteria/query/actual 在 evaluate 时注入）
SYSTEM_PROMPT: str = '''
# 角色
你是 Kimi，由 Moonshot AI 提供的人工智能助手，你更擅长中文和英文的对话。
# 任务
你会为根据{criteria}，对于用户的问题{query}，以及给定的答案{actual}来输出一个评分。
# 规则
你会拒绝一切涉及恐怖主义，种族歧视，黄色暴力等问题的回答。
Moonshot AI 为专有名词，不可翻译成其他语言。
# 输出格式
输出 JudgeResult 格式的 JSON，包含分数、是否通过、评分理由和错误信息。
评分理由和错误信息用中文输出。
'''

# JSON Schema for structured output (score/passed/reasoning/error)
_JUDGE_SCHEMA = {
    "type": "object",
    "properties": {
        "score": {"type": "number", "description": "分数 0.0 ~ 1.0"},
        "passed": {"type": "boolean"},
        "reasoning": {"type": ["string", "null"]},
        "error": {"type": ["string", "null"]},
    },
    "required": ["score", "passed"],
    "additionalProperties": False,
}

class MoonshotJudgeProvider(Protocol):
    """Grades one answer. Note: it receives the RUBRIC (evaluation_criteria),
    not a fixed expected answer string."""

    # 请求节流：读配置 MOONSHOT_MIN_INTERVAL（RPM=3 时代曾需 20s；
    # 充值/升配额后调小，如 RPM=60 → 1s）。空等时间由配置控制。
    _last_call: float = 0.0

    def __init__(self) -> None:
        self.client = OpenAI(
            api_key=settings.MOONSHOT_API_KEY,
            base_url=settings.MOONSHOT_BASE_URL,
            max_retries=3,  # openai SDK 会对 429/5xx 自动重试（指数退避）
        )

    @classmethod
    def _min_interval(cls) -> float:
        return getattr(settings, "MOONSHOT_MIN_INTERVAL", 1.0)

    @classmethod
    def _throttle(cls) -> None:
        """请求节流：保证调用间隔 >= 配置的最小间隔（防 429）。"""
        wait = cls._min_interval() - (time.monotonic() - cls._last_call)
        if wait > 0:
            time.sleep(wait)
        cls._last_call = time.monotonic()

    def evaluate(
        self,
        query: str,
        criteria: str | None,
        actual: str,
    ) -> JudgeResult:
        try:
            self._throttle()
            prompt = SYSTEM_PROMPT.format(criteria=criteria, query=query, actual=actual)
            completion = self.client.chat.completions.create(
                model=settings.MOONSHOT_MODEL,
                messages=[{"role": "system", "content": prompt}],
                response_format={
                    "type": "json_schema",
                    "json_schema": {
                        "name": "judge_result",
                        "schema": _JUDGE_SCHEMA,
                        "strict": True,
                    },
                }
            )
        except Exception as exc:  # noqa: BLE001 — 重试耗尽（含 429 持续超限）时兜底，不让 runner 崩
            return JudgeResult(
                score=0.0,
                passed=False,
                reasoning="moonshot judge 调用失败",
                error=f"{type(exc).__name__}: {exc}",
            )
        content = completion.choices[0].message.content
        print(f"[judge] {content}")
        try:
            obj = json.loads(content or "")
        except (json.JSONDecodeError, TypeError) as e:
            return JudgeResult(
                score=0.0,
                passed=False,
                reasoning=f"judge 输出不是合法 JSON: {(content or '')[:200]}",
                error=str(e),
            )
        return JudgeResult(
            score=float(obj.get("score", 0.0)),
            passed=bool(obj.get("passed", False)),
            reasoning=obj.get("reasoning"),
            error=obj.get("error"),
        )



def build_judge(name: str = "moonshot", threshold: float = 0.7) -> JudgeProvider:
    """Judge 工厂（真实链路，无 mock）。

    threshold 保留在签名里以兼容历史调用；Moonshot 判分的通过与否由模型
    按其输出决定，本地阈值仅 assert 判定使用。
    """
    if name == "moonshot":
        return MoonshotJudgeProvider()
    raise ValueError(f"unknown judge: {name!r} (available: moonshot)")


# ------------------------------------------------------------------ #
#  assert 判定（默认）：本地确定性信息点命中判定，零成本、可复现。
#  与 moonshot 输出结构一致（JudgeResult），调用方无需感知差异。
#
#  v2 修复（2026-09）：历史实现对期望文本做“≥4 位裸数字 + 英文关键词”子串
#  比对——$18,500 这类千分位金额、PAC-778K4M / 12C / MAT-151 编号全抽不到，
#  落到英文 rubric 词 vs 中文回答的错配上，大量正确答案被判 0。
#
#  现在改为抽取“信息点”后做归一化包含判断（中英文写法皆可命中）：
#    1) 金额（千分位/小数）：$2,400 → 2400，$1,017.50 → 1017.50
#    2) 字母数字编号/代码：PAC-778K4M、MAT-151、CLM-2024-894327、5524-8897-2234-4001
#    3) 座位/短编号：12C、14C
#    4) ≥4 位纯数字：账号/路由/确认号等（整块日期先行剔除）
#  回答先做等价归一（去 $/逗号/连字符/空格、忽略大小写），再做包含判断。
#
#  日期不作为硬信息点（避免 19/20 这类“只差年份字面”的误伤）；仅当期望文本
#  没有其他信息点时，用“月-日”组合做回退判定（兼容 December 23 / 12月23日）。
# ------------------------------------------------------------------ #
_MONTH_ALIASES = {
    'january': 1, 'jan': 1,
    'february': 2, 'feb': 2,
    'march': 3, 'mar': 3,
    'april': 4, 'apr': 4,
    'may': 5,
    'june': 6, 'jun': 6,
    'july': 7, 'jul': 7,
    'august': 8, 'aug': 8,
    'september': 9, 'sept': 9, 'sep': 9,
    'october': 10, 'oct': 10,
    'november': 11, 'nov': 11,
    'december': 12, 'dec': 12,
}

# 归一化：去掉金额符号/千分位/连字符/空白等纯格式噪音，保留数字/字母/小数点与中文
_NORM_RE = re.compile(r"[\s,$%_'\"-]+")

# 整块英文日期（December 23, 2024 / Nov 21st 2025）——整体剔除，不把年份当普通数字
_DATE_BLOCK_RE = re.compile(
    r"\b(?:"
    + "|".join(sorted((re.escape(m) for m in _MONTH_ALIASES), key=len, reverse=True))
    + r")\.?\s+\d{1,2}(?:st|nd|rd|th)?(?:,?\s*\d{4})?\b",
    re.IGNORECASE,
)

# 金额：$2,400 / $1,017.50 / $150
_AMOUNT_RE = re.compile(r"\$\s?\d{1,3}(?:,\d{3})+(?:\.\d+)?|\$\s?\d+(?:\.\d+)?")

# 字母开头编号/代码：PAC-778K4M / MAT-151 / CLM-2024-894327 / E7739482M
# （字母与数字必须紧连或经连字符；禁止空格分隔，避免吞掉 “account 4429…” 这类普通短语）
_CODE_RE = re.compile(r"\b[A-Z]{1,8}-?\d{2,}[A-Za-z0-9-]*\b", re.IGNORECASE)
# 长数字序列（信用卡等多组连写）：5524-8897-2234-4001
_CARD_RE = re.compile(r"\b\d{4,}(?:[-\s]\d{4,}){1,}\b")
# 座位/短编号：12C、14C
_SEAT_RE = re.compile(r"\b\d{1,3}[A-Z]\b", re.IGNORECASE)
# 剩余 ≥4 位纯数字：账号/路由/确认号
_NUM4_RE = re.compile(r"(?<!\d)\d{4,}(?!\d)")

# 中文日期：12月23日 / 1月6日；数字日期：12/23
_CJK_DATE_RE = re.compile(r"(\d{1,2})\s*月\s*(\d{1,2})\s*日?")
_NUM_DATE_RE = re.compile(r"(?<!\d)(\d{1,2})/(\d{1,2})(?!\d)")


def _norm(text: str) -> str:
    """等价归一：去格式噪音 + 忽略大小写（保留数字/字母/小数点/中文）。"""
    return _NORM_RE.sub('', text).lower()


def _expected_facts(expected: str) -> list[str]:
    """从期望文本抽取需核验的“信息点”（去重保序，归一化形式）。

    日期整块先行剔除——其年份/日号属于辅助信息，不要求以字面出现
    （DeepSeek 中文回答通常写 “12月23日” 而非 “December 23, 2024”）。
    """
    text = _DATE_BLOCK_RE.sub(' ', expected)
    facts: list[str] = []
    for m in _AMOUNT_RE.finditer(text):
        facts.append(_norm(m.group(0)))
    text = _AMOUNT_RE.sub(' ', text)
    for m in _CODE_RE.finditer(text):
        facts.append(_norm(m.group(0)))
    text = _CODE_RE.sub(' ', text)
    for m in _CARD_RE.finditer(text):
        facts.append(_norm(m.group(0)))
    text = _CARD_RE.sub(' ', text)
    for m in _SEAT_RE.finditer(text):
        facts.append(_norm(m.group(0)))
    text = _SEAT_RE.sub(' ', text)
    for n in _NUM4_RE.findall(text):
        facts.append(_norm(n))
    return list(dict.fromkeys(facts))


def _parse_month_days(text: str) -> set[tuple[int, int]]:
    """提取文本中的“月-日”组合（英文 December 23 / 中文 12月23日 / 数字 12/23）。"""
    out: set[tuple[int, int]] = set()
    for alias, month in _MONTH_ALIASES.items():
        pat = re.compile(
            rf"\b{re.escape(alias)}\.?\s+(\d{{1,2}})(?:st|nd|rd|th)?\b",
            re.IGNORECASE,
        )
        for m in pat.finditer(text):
            out.add((month, int(m.group(1))))
    for month, day in _CJK_DATE_RE.findall(text):
        out.add((int(month), int(day)))
    for month, day in _NUM_DATE_RE.findall(text):
        out.add((int(month), int(day)))
    return out


def assert_evaluate(
    query: str,
    expected: str,
    actual: str,
    threshold: float = 0.7,
) -> JudgeResult:
    """本地确定性判定：检查实际答案是否包含期望文本的关键信息点。

    - 有信息点（金额/编号/长数字）时按命中率打分，>= threshold 通过
    - 期望只有日期信息时，按“月-日”组合命中率判定（中英文写法兼容）
    - 期望无可校验信息点时默认通过
    """
    if not (actual or '').strip():
        return JudgeResult(
            score=0.0,
            passed=False,
            reasoning='assert 判定: 实际答案为空',
        )

    facts = _expected_facts(expected or '')
    if facts:
        norm_actual = _norm(actual)
        hit = [f for f in facts if f in norm_actual]
        missing = [f for f in facts if f not in norm_actual]
        score = len(hit) / len(facts)
        reasoning = (
            f'assert 判定: 关键信息命中 {len(hit)}/{len(facts)}'
            + (f'，缺失: {missing}' if missing else '')
        )
        return JudgeResult(score=score, passed=score >= threshold, reasoning=reasoning)

    exp_dates = _parse_month_days(expected or '')
    if exp_dates:
        act_dates = _parse_month_days(actual)
        hit = [d for d in exp_dates if d in act_dates]
        missing = sorted(exp_dates - act_dates)
        score = len(hit) / len(exp_dates)
        return JudgeResult(
            score=score,
            passed=score >= threshold,
            reasoning=(
                f'assert 判定: 期望仅含日期信息，月-日命中 {len(hit)}/{len(exp_dates)}'
                + (f'，缺失: {missing}' if missing else '')
            ),
        )

    return JudgeResult(
        score=1.0,
        passed=True,
        reasoning='assert 判定: 期望文本无可校验信息点，默认通过',
    )
