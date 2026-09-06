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
import re

from os_mem.infra import get_logger

from ..judge import JudgeProvider
from ..models import JudgeResult

_logger = get_logger('eval.judge.assert')

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


class AssertJudger(JudgeProvider):
    def __init__(self, threshold: float = 0.7):
        self.threshold = threshold

    def _norm(self, text: str) -> str:
        """等价归一：去格式噪音 + 忽略大小写（保留数字/字母/小数点/中文）。"""
        return _NORM_RE.sub('', text).lower()


    def _expected_facts(self, expected: str) -> list[str]:
        """从期望文本抽取需核验的“信息点”（去重保序，归一化形式）。

        日期整块先行剔除——其年份/日号属于辅助信息，不要求以字面出现
        （DeepSeek 中文回答通常写 “12月23日” 而非 “December 23, 2024”）。
        """
        text = _DATE_BLOCK_RE.sub(' ', expected)
        facts: list[str] = []
        for m in _AMOUNT_RE.finditer(text):
            facts.append(self._norm(m.group(0)))
        text = _AMOUNT_RE.sub(' ', text)
        for m in _CODE_RE.finditer(text):
            facts.append(self._norm(m.group(0)))
        text = _CODE_RE.sub(' ', text)
        for m in _CARD_RE.finditer(text):
            facts.append(self._norm(m.group(0)))
        text = _CARD_RE.sub(' ', text)
        for m in _SEAT_RE.finditer(text):
            facts.append(self._norm(m.group(0)))
        text = _SEAT_RE.sub(' ', text)
        for n in _NUM4_RE.findall(text):
            facts.append(self._norm(n))
        return list(dict.fromkeys(facts))


    def _parse_month_days(self, text: str) -> set[tuple[int, int]]:
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


    def evaluate(
        self,
        query: str,
        criteria: str|None,
        actual: str,
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

        facts = self._expected_facts(criteria or '')
        if facts:
            norm_actual = self._norm(actual)
            hit = [f for f in facts if f in norm_actual]
            missing = [f for f in facts if f not in norm_actual]
            score = len(hit) / len(facts)
            reasoning = (
                f'assert 判定: 关键信息命中 {len(hit)}/{len(facts)}'
                + (f'，缺失: {missing}' if missing else '')
            )
            return JudgeResult(
                score=score,
                passed=score >= self.threshold,
                reasoning=reasoning,
            )

        exp_dates = self._parse_month_days(criteria or '')
        if exp_dates:
            act_dates = self._parse_month_days(actual)
            hit = [d for d in exp_dates if d in act_dates]
            missing = sorted(exp_dates - act_dates)
            score = len(hit) / len(exp_dates)
            return JudgeResult(
                score=score,
                passed=score >= self.threshold,
                reasoning=(
                    f'assert 判定: 期望仅含日期信息，'
                    f'月-日命中 {len(hit)}/{len(exp_dates)}'
                    + (f'，缺失: {missing}' if missing else '')
                ),
            )

        return JudgeResult(
            score=1.0,
            passed=True,
            reasoning='assert 判定: 期望文本无可校验信息点，默认通过',
        )
