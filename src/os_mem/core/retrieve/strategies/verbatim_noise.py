# ---------------------------------------------------------------------- #
#  噪声判定（verbatim 专属，留本文件）；精确信息 token 口径见
#  os_mem.extractor.utils.token_utils.fact_tokens（金额/编号/卡号/百分比/
#  时刻/日期；提取侧 R1 与本侧冗余过滤同源，通用口径不看齐判分器）
# ---------------------------------------------------------------------- #
# 疑问句/口语碎片（如 "So it would be $30 instead of $35?" / "okay" 等）
import re
from typing import Any

from .verbatim_utils import _is_verbatim

_VERBATIM_NOISE = re.compile(
    r'^.*(\?|？)$|'  # 疑问句
    r'^(yes|no|okay|ok|right|correct|exactly|great|perfect|thanks|thank you|sure|'
    r'alright|got it|could you|can you|what about|how about|is that|do you|would you|'
    r'so that|so it|and that|well|hmm|uh)[\s,.;:!?。，；：！？]*$',  # 口语碎片
    re.IGNORECASE,
)

# 比较/调整句标记：句子在对比两个值（过程性中间值/纠错），非定论句
# （例：case 20 "$308.75 instead of $617.50 for that week"）
_VERBATIM_ADJUST = re.compile(r'\binstead of\b|\brather than\b', re.IGNORECASE)

def _is_verbatim_noise(fact: str) -> bool:
    """verbatim 噪声判定：疑问句/口语碎片 ∪ 比较/调整标记。"""
    return bool(
        _VERBATIM_NOISE.match(fact.strip()) or _VERBATIM_ADJUST.search(fact)
    )

# ---------------------------------------------------------------------- #
#  1. 噪声剔除 —— verbatim 质量闸门
# ---------------------------------------------------------------------- #
class VerbatimNoiseFilter:
    """单一职责：剔除噪声 verbatim 句（疑问句/口语碎片/比较调整句）。

    治 02 新旧混入、17/19 碎片噪音与 case 20 的过程性中间值
    （"$308.75 instead of $617.50 for that week"）。非 verbatim 不受影响。
    """

    def apply(
        self,
        query: str,
        hits: list[dict[str, Any]],
        top_k: int,
    ) -> list[dict[str, Any]]:
        return [
            h
            for h in hits
            if not (_is_verbatim(h) and _is_verbatim_noise(h.get('fact') or ''))
        ]
