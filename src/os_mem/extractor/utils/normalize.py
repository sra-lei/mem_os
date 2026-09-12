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
from typing import Optional

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
#  D4-2 v2 属性策略：functional（单值）/ multi_valued（多值）
#  —— 决定「值是否承担实例标识职责」。缺省 functional = 与现状逐字节一致。
# --------------------------------------------------------------------------- #
POLICY_FUNCTIONAL = "functional"
POLICY_MULTI_VALUED = "multi_valued"

# 多值属性白名单：(category, attribute)。
# 只收**实证确认**「同属性下多实例并存」的项——判错的代价不对称：
# 把"并列"误判为 functional → 丢失；把"更新"误判为 multi_valued → 冗余。
# 宁可冗余不可丢失，但也不预先扩张（按用例逐步补）。
_MULTI_VALUED_PAIRS: frozenset[tuple[str, str]] = frozenset({
    # 03 医疗：lisinopril 与 atorvastatin 是两种药（实证：atorvastatin 被批内收敛丢弃）
    ("health", "medication"),
    # 15 选课：多门课并列
    ("education", "course"),
    ("education", "course_schedule"),
    ("education", "course_registration"),
    # 11 房贷：多张卡 / 多个投资账户并列
    ("finance", "credit_card"),
    ("finance", "investment_account"),
    ("finance", "bank_account"),
})

# 实例名 → 实体种类前缀（决定 entity_ref 的 <KIND>）
_INSTANCE_KIND_BY_ATTRIBUTE: dict[str, str] = {
    "medication": "DRUG",
    "course": "COURSE",
    "course_schedule": "COURSE",
    "course_registration": "COURSE",
    "credit_card": "CARD",
    "investment_account": "ACCT",
    "bank_account": "ACCT",
}
_ENTITY_KIND_INSTANCE_FALLBACK = "INST"

# P2 专名抽取：首字母大写词（词长 ≥3，避免 I/A 等噪声）
_PROPER_NOUN_RE = re.compile(r"\b[A-Z][a-zA-Z]{2,}(?:\s+[A-Z][a-zA-Z]{2,})*\b")
# 停用词（小写比较）：句首词/代词/月份/星期/常见礼貌用语——都不是实例名
_PROPER_NOUN_STOPWORDS = frozenset({
    "user", "the", "your", "our", "their", "his", "her", "its", "this", "that",
    "these", "those", "there", "then", "they", "yes", "no", "okay", "sure",
    "thanks", "thank", "please", "well", "also", "and", "but", "plus", "with",
    "what", "when", "where", "which", "while", "would", "could", "should",
    "professor", "doctor", "section", "sections", "plan", "plans",
    "january", "february", "march", "april", "may", "june", "july", "august",
    "september", "october", "november", "december",
    "monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday",
    "customer", "client", "agent", "account", "total", "monthly", "annual",
})


def attribute_policy(category: str, attribute: str) -> str:
    """查属性策略：``multi_valued`` 才允许按实例切分实体（缺省 functional）。"""
    if (category, attribute) in _MULTI_VALUED_PAIRS:
        return POLICY_MULTI_VALUED
    return POLICY_FUNCTIONAL


# 按 category 分组的**长前缀优先**列表（防 `course_` 吞掉 `course_schedule_xxx`）
_MULTI_VALUED_ATTRS_BY_CATEGORY: dict[str, tuple[str, ...]] = {
    cat: tuple(sorted((a for c, a in _MULTI_VALUED_PAIRS if c == cat), key=len, reverse=True))
    for cat in {c for c, _ in _MULTI_VALUED_PAIRS}
}


def _norm_instance(text: str) -> str:
    """实例名归一：小写、去标点（保留中日韩）、空白折叠、限长。"""
    cleaned = re.sub(r"[^\w\u4e00-\u9fff]+", " ", str(text or "").lower()).strip()
    cleaned = " ".join(cleaned.split())
    return cleaned[:40]


def _instance_from_key(key: str, attribute: str) -> Optional[str]:
    """P3：key 形如 ``<attribute>_<实例名>`` → 剥出实例名（中文场景的关键路径）。

    LLM 只提供字符串，**系统决定它放在实体维度**（裁决仍是确定性代码）。
    剩余部分为空、或本身是生命周期词 → 不剥离（防误吞）。
    """
    prefix = f"{attribute}_"
    if not key.startswith(prefix):
        return None
    rest = key[len(prefix):]
    if not rest or rest in _LIFECYCLE_WORDS:
        return None
    norm = _norm_instance(rest)
    return norm or None


def _instance_from_text(text: str) -> Optional[str]:
    """P2：从事实文本抽专名当实例名；**恰好命中一个**才采用（保守）。

    按词剔除停用词（`The Visa` → `Visa`；`The User` → 空 → 跳过），
    因此句首冠词/代词不会污染实例名。
    """
    cands: set[str] = set()
    for phrase in _PROPER_NOUN_RE.findall(text or ""):
        words = [w for w in phrase.split() if w.lower() not in _PROPER_NOUN_STOPWORDS]
        if words:
            cands.add(" ".join(words))
    if len(cands) != 1:
        return None
    norm = _norm_instance(cands.pop())
    return norm or None


def _instance_entity(attribute: str, instance: str) -> str:
    kind = _INSTANCE_KIND_BY_ATTRIBUTE.get(attribute, _ENTITY_KIND_INSTANCE_FALLBACK)
    return f"{kind}:{instance}"


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

# 生命周期词集合（P3 剥离时防误吞：`medication_current_` 这类不当作实例名）
_LIFECYCLE_WORDS = frozenset(
    p.rstrip("_") for p in (_HISTORICAL_PREFIXES + _CURRENT_PREFIXES)
)

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

    # D4-2 v2 P3：key 形如 `<多值属性>_<实例名>` → 剥出实例名，属性归到 `<多值属性>`
    # （必须先于 alias 查询：alias 表不认识带实例后缀的 key；长前缀优先防误吞）
    instance_hint: Optional[str] = None
    for _attr in _MULTI_VALUED_ATTRS_BY_CATEGORY.get(category, ()):
        inst = _instance_from_key(bare, _attr)
        if inst:
            bare, instance_hint = _attr, inst
            break

    canonical = _CANONICAL_ALIASES.get((category, bare))
    if canonical is None:
        # 别名表也查原始 key（未去前缀的直接登记项）
        canonical = _CANONICAL_ALIASES.get((category, key), bare)

    return NormalizedKey(
        entity_ref=resolve_entity(
            fact, category, canonical, key, value, instance=instance_hint
        ),
        attribute=canonical,
        lifecycle=lifecycle,
    )


def resolve_entity(
    fact: str = "",
    category: str = "",
    attribute: str = "",
    key: str = "",
    value: str = "",
    *,
    instance: Optional[str] = None,
) -> str:
    """D4-2 确定性实体解析：按优先级取第一条命中的线索，否则 `SELF`（保守）。

    优先级（见 ``docs/方案/方案-D4-2-实体解析器.md`` §3）：

    - **P0 形式化编号**：``VEL-89923476`` → ``ID:VEL``；
    - **P1 产品/课程码**：``MAT-151``（category 属产品类）→ ``PROD:MAT-151``；
    - **P3 key 内实例名**：``medication_atorvastatin`` → ``DRUG:atorvastatin``
      （**显式优于推断**，故排在 P2 之前——LLM 明确写了实例名比从文本猜更可靠）；
    - **P2 专名抽取**：仅对 ``multi_valued`` 属性，且文本中**恰好命中一个**专名；
    - 无线索 → ``SELF``。

    **作用的边界**：P2/P3 只对属性策略为 ``multi_valued`` 的属性生效；
    单值属性（金额/日期/电话…）**永不按值切分**，其版本演进（``$85k→$100k→$95k``）
    必须留在同一签名上交给 as-of 裁决——这是本方案的安全阀。
    兜底/降级行（``verbatim_*`` / ``raw_conversation*``）恒 ``SELF``。
    """
    if str(key or "").startswith(_NON_ENTITY_KEY_PREFIXES):
        return SELF_ENTITY
    text = f"{fact or ''} {value or ''}".strip()
    if not text:
        return SELF_ENTITY

    # P1 / P0：形式化代码线索（零误伤，优先级最高）
    if category in _PRODUCT_CATEGORIES:
        m = _PRODUCT_CODE_RE.search(text)
        if m:
            return f"{_ENTITY_KIND_PRODUCT}:{m.group(0)}"
    m = _CODE_RE.search(text)
    if m:
        return f"{_ENTITY_KIND_ID}:{m.group(1)}"

    # P3 / P2：仅多值属性允许按实例切分（安全阀）
    if attribute and attribute_policy(category, attribute) == POLICY_MULTI_VALUED:
        inst = instance or _instance_from_text(text)
        if inst:
            return _instance_entity(attribute, inst)

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
