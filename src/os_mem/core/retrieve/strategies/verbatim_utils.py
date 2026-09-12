from typing import Any

from os_mem.extractor.utils.token_utils import fact_tokens


def _is_verbatim(hit: dict[str, Any]) -> bool:
    return (hit.get('key') or '').startswith('verbatim_')


def _hit_tokens(hit: dict[str, Any]) -> set[str]:
    fact = hit.get('fact') or ''
    return fact_tokens(f'{fact} {hit.get("value") or ""}')
