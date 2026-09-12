"""D4：key 规范化与实体解析（纯函数，无 DB / LLM 依赖）。

设计见 ``docs/方案/方案-D4-实体归属与as-of版本裁决.md`` §3.1-3.2、§4。

收敛签名从 ``(user_id, category, key)`` 升级为
``(user_id, entity_ref, attribute, lifecycle)``：
- ``attribute``  规范属性名：漂移别名（wire_amount / wire_transfer_amount /
  transfer_amount / final_transfer_amount）归一到同一 canonical；
- ``lifecycle``  current（当前值）/ historical（原始/历史快照，独立签名）；
- ``entity_ref`` 实体归属，D4-1 全部归 SELF，D4-2 引入真实实体解析。

三层归一防线中的 L2（确定性入库归一）。alias 词表刻意保守——只收已实测确认的
同义簇，拿不准的不归一（保留独立行=旧行为，不会更差）；泛化交 L3 离线聚类。

归一结果数据类 ``NormalizedKey`` 统一放 ``extractor/model/models.py``（提取域
数据类单一存放点）；本模块只放归一函数/词表/生命周期常量。
"""
from __future__ import annotations

import re

from os_mem.extractor.model.models import NormalizedKey

# 缺省实体：用户本人（D4-2 前所有事实归 SELF）
SELF_ENTITY = "SELF"

LIFECYCLE_CURRENT = "current"
LIFECYCLE_HISTORICAL = "historical"
LIFECYCLE_SUPERSEDED = "superseded"

# --------------------------------------------------------------------------- #
#  D4-2 实体解析：形式化线索（零词表、零误伤）
# --------------------------------------------------------------------------- #
# 编号形态：VEL-89923476 / ENT-7739482 / CLM-2024-894327 / PAC-778K4M
# 要求字母开头（排除 2024-09-15 这类日期与 1-800-XXX 这类电话）
_CODE_RE = re.compile(r"\b([A-Z][A-Z0-9]{1,7})-(\d{2,})[A-Z0-9-]*\b")
# 产品/课程码形态（更严：纯字母前缀 + ≥3 位数字）：MAT-151 / CS-101
_PRODUCT_CODE_RE = re.compile(r"\b([A-Z]{2,6})-(\d{3,})\b")
# 这些 category 下的代码形态按「产品/课程」实体归属
_PRODUCT_CATEGORIES = frozenset({"education", "work", "product", "subscription"})
# 不参与实体切分的 key 前缀：兜底/降级行的 key 本身即唯一（内容哈希），无覆盖风险
_NON_ENTITY_KEY_PREFIXES = ("verbatim_", "raw_conversation")
# 非本人实体的种类前缀（entity_ref 形态：<KIND>:<标识>）
_ENTITY_KIND_ID = "ID"
_ENTITY_KIND_PRODUCT = "PROD"

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


def normalize_key(
    category: str,
    key: str,
    *,
    fact: str = "",
    value: str = "",
) -> NormalizedKey:
    """把 (category, 漂移 key) 归一为 (entity_ref, attribute, lifecycle)。

    顺序：先识别生命周期前缀（去前缀），再查 canonical 别名表；都不命中则
    attribute=原 key（去前缀后的本体），保持独立 = 旧行为（安全不合并）。
    ``fact``/``value`` 供 D4-2 实体解析做线索匹配（不给则实体恒为 SELF）。
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
        entity_ref=resolve_entity(fact, category, key, value),
        attribute=canonical,
        lifecycle=lifecycle,
    )


def resolve_entity(
    fact: str = "",
    category: str = "",
    key: str = "",
    value: str = "",
) -> str:
    """D4-2 确定性实体解析：形式化线索命中 → 非 SELF 实体；否则 SELF（保守）。

    设计（``docs/方案/方案-D4-2-实体解析器.md`` §3）：

    - E1 编号前缀：``VEL-89923476`` → ``ID:VEL``；``ENT-7739482`` → ``ID:ENT``；
    - E4 产品/课程码：``MAT-151``（category 属产品类）→ ``PROD:MAT-151``；
    - 无线索 → ``SELF``；兜底/降级行（verbatim_*/raw_conversation*）恒 ``SELF``。

    **红线**：绝不用「值」本身做实体键——``$85k→$100k→$95k`` 是同一实体的版本
    演进，必须留在同一签名上交给 as-of 裁决；只有事实文本里**显式出现的形式化
    标识**才允许触发切分。

    尚未启用的线索（E2 机构名 / E3 人名）：需要注册表词表证据，且存在把本人事实
    误切的风险（如把用户自己的姓名切出去），留待后续迭代按用例补。
    """
    if str(key or "").startswith(_NON_ENTITY_KEY_PREFIXES):
        return SELF_ENTITY
    text = f"{fact or ''} {value or ''}"
    if not text.strip():
        return SELF_ENTITY

    if category in _PRODUCT_CATEGORIES:
        m = _PRODUCT_CODE_RE.search(text)
        if m:
            return f"{_ENTITY_KIND_PRODUCT}:{m.group(0)}"

    m = _CODE_RE.search(text)
    if m:
        return f"{_ENTITY_KIND_ID}:{m.group(1)}"
    return SELF_ENTITY


def projection_key(entity_ref: str, attribute: str) -> str:
    """投影/检索侧的收敛键（D4-2）。

    ``SELF`` 保持裸 attribute（与既有投影**逐字节一致**，无需重建）；
    非 SELF 加实体前缀 ``<entity>|<attribute>``，避免两个实体的同名属性
    在 Milvus 投影里互相删除、并被检索侧 (category,key) 去重折叠成一条。
    """
    if not entity_ref or entity_ref == SELF_ENTITY:
        return attribute
    return f"{entity_ref}|{attribute}"
