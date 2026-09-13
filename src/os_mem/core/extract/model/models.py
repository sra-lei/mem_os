"""提取域数据类（纯数据，无策略/行为）—— 单一存放点。

归属：``os_mem.core.extract`` 记忆提取域。本模块收拢提取域内各模块的数据类，
与承载行为的模块分离：

- ``NormalizedKey``：D4 key 归一结果（事实身份 = 实体/规范属性/生命周期），
  归一函数本身在 ``utils/normalize.py``；
- ``CallResult``：单段提取结果契约（facts|None + 遥测 stats），恢复循环在
  ``extract_core.py``；
- ``ChunkCaps``：模型输入分段数据画像（纯数据，刻意无策略字段——
  恢复策略随各 caller 实现走）；默认值由 ``from_settings()`` 直读
  memory_settings。

依赖方向（无环）：models → utils.token_utils（stats 默认值）/ configs.mem_settings
（ChunkCaps.from_settings）；不反向 import callers/extract_core/normalize/
fact_extractor。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from os_mem.configs.mem_settings import memory_settings
from os_mem.core.extract.utils.extract_utils import empty_extraction_stats


# --------------------------------------------------------------------------- #
#  D4 归一
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class NormalizedKey:
    """归一结果：事实身份的 (实体, 规范属性, 生命周期)。"""

    entity_ref: str
    attribute: str
    lifecycle: str

    @property
    def signature(self) -> tuple[str, str, str]:
        """裁决分组签名：含 lifecycle —— historical 与 current 互不取代。"""
        return (self.entity_ref, self.attribute, self.lifecycle)


# --------------------------------------------------------------------------- #
#  提取调用契约
# --------------------------------------------------------------------------- #
@dataclass
class CallResult:
    """单段提取结果：facts|None（None = 全败/干净失败），stats = 本次调用遥测。"""

    facts: list | None
    stats: dict[str, int] = field(default_factory=empty_extraction_stats)


# --------------------------------------------------------------------------- #
#  模型数据画像
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class ChunkCaps:
    """单次提取调用的输入分段上限（纯数据；任务层分段编排取此供给）。

    对应 memory_settings 的 DEEPSEEK_EXTRACT_MAX_CHARS / MAX_MSGS / OVERLAP
    （双维分段：字符数 OR 消息数任一超限即切，段间冗余 overlap 条）。
    """

    max_chars: int
    max_msgs: int
    overlap: int

    @classmethod
    def from_settings(cls) -> ChunkCaps:
        """读 memory_settings 现值构造（settings 即分段上限的单一数据源）。"""
        return cls(
            max_chars=memory_settings.DEEPSEEK_EXTRACT_MAX_CHARS,
            max_msgs=memory_settings.DEEPSEEK_EXTRACT_MAX_MSGS,
            overlap=memory_settings.DEEPSEEK_EXTRACT_OVERLAP,
        )
