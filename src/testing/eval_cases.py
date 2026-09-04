"""评测用例加载与分层 —— 从 tests/conftest.py 拆出的纯数据层。

职责：
- 定位用例目录（锚定仓库根，不随 cwd 漂移）
- 加载 YAML 用例（校验 test_id）
- category → layer 标记 / (phase, version) 映射

不依赖 pytest / 不触发任何 os_mem 导入副作用，可独立单测。
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

# 锚定仓库根：src/testing/eval_cases.py -> parents[2] = repo root
_REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CASES_DIR = _REPO_ROOT / "tests" / "test_cases"

# YAML category -> pytest mark 名（pytest_collection_modifyitems 自动打标）
LAYER_MARKS = {"layer1": "layer1", "layer2": "layer2", "layer3": "layer3"}

# YAML category -> (dashboard phase, version_target)
LAYER_PHASE_VERSION = {
    "layer1": ("base", "v0.1"),
    "layer2": ("multi_session", "v0.2"),
    "layer3": ("proactive", "v0.3"),
}


def _parse_ts(value: Any) -> datetime:
    """YAML timestamp -> datetime，缺失时用当前时间兜底。"""
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
            try:
                return datetime.strptime(value, fmt)
            except ValueError:
                continue
    return datetime.utcnow()


def load_yaml_case(path: Path) -> dict:
    """读取单个 YAML 用例，返回标准化 dict。"""
    import pytest
    import yaml

    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not data.get("test_id"):
        pytest.skip(f"invalid yaml (missing test_id): {path}")
    return data


def load_all_cases(cases_dir: Path | None = None) -> list[tuple[str, dict]]:
    """加载全部 YAML 用例，返回 [(case_id, data), ...]（按路径排序，结果稳定）。"""
    cases_dir = Path(cases_dir) if cases_dir else DEFAULT_CASES_DIR
    cases = []
    for path in sorted(cases_dir.glob("**/*.yaml")):
        data = load_yaml_case(path)
        cases.append((data["test_id"], data))
    return cases


def mark_name_for_case(case_data: dict) -> str | None:
    """用例 category 对应的 pytest mark 名（无则 None）。"""
    category = str(case_data.get("category", "")).lower()
    return LAYER_MARKS.get(category)


def phase_version_for_case(case_data: dict) -> tuple[str, str]:
    """layer (YAML category) -> (dashboard phase, version_target)。"""
    category = str(case_data.get("category", "")).lower()
    return LAYER_PHASE_VERSION.get(category, (category or "base", "v0.1"))


def expected_text_for_case(case_data: dict) -> str:
    """判定/落库用的期望文本：优先 expected_behavior，缺省回退 rubric。"""
    return str(
        case_data.get("expected_behavior")
        or case_data.get("evaluation_criteria")
        or ""
    )
