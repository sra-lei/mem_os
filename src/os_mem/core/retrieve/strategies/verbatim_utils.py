from typing import Any


def _is_verbatim(hit: dict[str, Any]) -> bool:
    return (hit.get('key') or '').startswith('verbatim_')

