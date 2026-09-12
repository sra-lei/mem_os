from typing import Any

from .verbatim_utils import _is_verbatim


# ---------------------------------------------------------------------- #
#  4. verbatim 配额 —— 结构化充足时压 carrier，稀缺时放行填满
# ---------------------------------------------------------------------- #
class VerbatimQuota:
    """单一职责：限制窗口内 verbatim 数量。

    配额 = max(ceil(top_k/3), top_k - 结构化数)：
    - 结构化充足（≥ top_k）→ verbatim ≤ 1/3（防英文兜底句霸榜，835b 55% 教训）；
    - 结构化稀缺 → verbatim 可填满剩余（case 18/20 唯一载体修复点）。
    超出部分按相关性序（输入序）截断。
    """

    def apply(
        self,
        query: str,
        hits: list[dict[str, Any]],
        top_k: int,
    ) -> list[dict[str, Any]]:
        n_structured = sum(1 for h in hits if not _is_verbatim(h))
        allowance = max((top_k + 2) // 3, top_k - n_structured)
        kept = 0
        out: list[dict[str, Any]] = []
        for hit in hits:
            if _is_verbatim(hit):
                if kept >= allowance:
                    continue
                kept += 1
            out.append(hit)
        return out
