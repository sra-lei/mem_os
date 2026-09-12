from typing import Any

from .verbatim_utils import _is_verbatim


# ---------------------------------------------------------------------- #
#  2. 结构化去重 —— 同 (category, key) 保首见
# ---------------------------------------------------------------------- #
class StructuredKeyDedup:
    """单一职责：结构化事实按 (category, key) 去重，保留首见（最相关版）。

    治同主题多版本扎堆（如多条同一 key 的 balance 变体占满窗口）。
    verbatim 每句唯一指纹 key，天然不受影响。
    """
    def apply(
        self,
        query: str,
        hits: list[dict[str, Any]],
        top_k: int,
    ) -> list[dict[str, Any]]:
        seen: set[tuple[str, str]] = set()
        out: list[dict[str, Any]] = []
        for hit in hits:
            if _is_verbatim(hit):
                out.append(hit)
                continue
            sig = ((hit.get('category') or ''), (hit.get('key') or ''))
            if sig in seen:
                continue
            seen.add(sig)
            out.append(hit)
        return out
