from typing import Any

from os_mem.extractor.utils.token_utils import fact_tokens

from .verbatim_utils import _is_verbatim


def _hit_tokens(hit: dict[str, Any]) -> set[str]:
    fact = hit.get('fact') or ''
    return fact_tokens(f'{fact} {hit.get("value") or ""}')


# ---------------------------------------------------------------------- #
#  3. verbatim 冗余剔除 —— 只保留携带新数值信息的 carrier
# ---------------------------------------------------------------------- #
class RedundantVerbatimFilter:
    """单一职责：剔除不携带新信息的 verbatim（无数值 token，或数值已被覆盖）。

    覆盖集按输入序累积 = 已保留结构化事实的 token ∪ 已保留 carrier 的 token；
    verbatim 的数值 token 全被覆盖即冗余（同值多版本/已被结构化表达的句子），
    不入窗——这是 v1 "信息唯一性"准入的落地。
    """

    def apply(
        self,
        query: str,
        hits: list[dict[str, Any]],
        top_k: int,
    ) -> list[dict[str, Any]]:
        coverage: set[str] = set()
        out: list[dict[str, Any]] = []
        for hit in hits:
            toks = _hit_tokens(hit)
            if not _is_verbatim(hit):
                out.append(hit)
                coverage |= toks
                continue
            if not toks or toks <= coverage:
                continue
            out.append(hit)
            coverage |= toks
        return out
