"""D4：key 规范化与实体解析（纯函数，无 DB / LLM 依赖）。

设计见 ``docs/方案-D4-实体归属与as-of版本裁决.md`` §3.1-3.2、§4。

收敛签名从 ``(user_id, category, key)`` 升级为
``(user_id, entity_ref, attribute, lifecycle)``：
- ``attribute``  规范属性名：漂移别名（wire_amount / wire_transfer_amount /
  transfer_amount / final_transfer_amount）归一到同一 canonical；
- ``lifecycle``  current（当前值）/ historical（原始/历史快照，独立签名）；
- ``entity_ref`` 实体归属，D4-1 全部归 SELF，D4-2 引入真实实体解析。

三层归一防线中的 L2（确定性入库归一）。alias 词表刻意保守——只收已实测确认的
同义簇，拿不准的不归一（保留独立行=旧行为，不会更差）；泛化交 L3 离线聚类。

归一结果数据类 ``NormalizedKey`` 统一放 ``extractor/models.py``（提取域数据类
单一存放点）；本模块只放归一函数/词表/生命周期常量。
"""
from __future__ import annotations

from os_mem.extractor.models import NormalizedKey

# 缺省实体：用户本人（D4-2 前所有事实归 SELF）
SELF_ENTITY = "SELF"

LIFECYCLE_CURRENT = "current"
LIFECYCLE_HISTORICAL = "historical"
LIFECYCLE_SUPERSEDED = "superseded"

# --------------------------------------------------------------------------- #
#  生命周期修饰词（通用前缀规则，跨 category）
# --------------------------------------------------------------------------- #
# 历史快照：original_wire_amount / previous_address / old_policy_number ……
# 去前缀后归一本体属性，但打 historical —— 独立签名，永不被 current 覆盖。
# 刻意保守：不含 last_（医疗 last_fill_date 类语义是"最近一次"= current）。
_HISTORICAL_PREFIXES = ("original_", "previous_", "former_", "old_")
# 当前值强化前缀：new_flight_cost / current_balance / latest_offer ……
# 去前缀视为 current（新词面收敛到本体）。
_CURRENT_PREFIXES = ("current_", "latest_", "updated_", "new_")

# --------------------------------------------------------------------------- #
#  L1/L2 规范属性别名表：(category, 原始漂移 key) -> canonical attribute
#  canonical 沿用下划线命名（贴合下游投影 key 形态，最小扰动）。
#  只收跨会话实测确认的同义簇；语义独立的词（gift_amount / reference_number
#  泛词 / daughter_* 实体词）一律不收。
# --------------------------------------------------------------------------- #
_CANONICAL_ALIASES: dict[tuple[str, str], str] = {
    # ---- case12 电汇（wire）演进簇 ----
    ("finance", "wire_amount"): "wire_amount",
    ("finance", "wire_transfer_amount"): "wire_amount",
    ("finance", "transfer_amount"): "wire_amount",
    ("finance", "final_transfer_amount"): "wire_amount",
    ("finance", "wire_date"): "wire_date",
    ("finance", "wire_transfer_date"): "wire_date",
    ("finance", "transfer_date"): "wire_date",
    ("finance", "wire_reference"): "wire_reference",
    ("finance", "wire_reference_number"): "wire_reference",
    ("finance", "transfer_reference_number"): "wire_reference",
    # 注：recipient_name/recipient_bank/memo 是泛词（非电汇场景也可能出现），
    # 过度合并风险 > 收益，不收；模型在 case12 已自行产出 wire_recipient 专有名。
}


def _strip_one_of(prefixes: tuple[str, ...], key: str) -> tuple[str, bool]:
    for p in prefixes:
        if key.startswith(p) and len(key) > len(p):
            return key[len(p):], True
    return key, False


def normalize_key(category: str, key: str) -> NormalizedKey:
    """把 (category, 漂移 key) 归一为 (entity_ref, attribute, lifecycle)。

    顺序：先识别生命周期前缀（去前缀），再查 canonical 别名表；都不命中则
    attribute=原 key（去前缀后的本体），保持独立 = 旧行为（安全不合并）。
    """
    lifecycle = LIFECYCLE_CURRENT
    bare = key

    bare, was_historical = _strip_one_of(_HISTORICAL_PREFIXES, bare)
    if was_historical:
        lifecycle = LIFECYCLE_HISTORICAL
    else:
        bare, _ = _strip_one_of(_CURRENT_PREFIXES, bare)

    canonical = _CANONICAL_ALIASES.get((category, bare))
    if canonical is None:
        # 别名表也查原始 key（未去前缀的直接登记项）
        canonical = _CANONICAL_ALIASES.get((category, key), bare)

    return NormalizedKey(
        entity_ref=SELF_ENTITY,
        attribute=canonical,
        lifecycle=lifecycle,
    )


def resolve_entity(*_args: object, **_kwargs: object) -> str:
    """实体解析占位（D4-2）：当前一律 SELF。

    签名预留 fact/category/key/value 等线索位，D4-2 接入 key 前缀 + 人名命中规则。
    """
    return SELF_ENTITY
