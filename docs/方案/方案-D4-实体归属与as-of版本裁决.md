# D4 方案：实体归属与 as-of 版本裁决

> 状态：**D4-0/1/3 已实施，D4-4 已回放验证**；**D4-2「实体维度」已独立成册并落地 v1**
> （2026-09-12，见 [`方案-D4-2-实体解析器.md`](方案-D4-2-实体解析器.md)——形式化线索 + 多值属性 + 实例实体）；
> D4-1.5（提取期 attribute 锚定）与 D4-5（全量复测）待做。收敛签名已升级为
> `(entity_ref, attribute, lifecycle)`（`extractor/utils/normalize.py` 的 `normalize_key` +
> `core/services/memory_versioning.py` 的 `plan_versioning`）；投影只镜像 SQLite 回读的
> lifecycle=current 赢家、投影 key=`projection_key(entity, attribute)`；旧的批内 `_converge_by_key`
> 已删除（2026-09-10，commit 8f82058）。
> **本文只负责「签名模型 / as-of 裁决 / 投影」主干；实体解析的细则一律进 D4-2 分册，避免重复。**
> **建议先读 §0「机制分解」**——三层十环节的调用链地图 + 逐环节状态（D4 的全貌图）。
> 关联：layer2=5/20 最大结构性瓶颈；`docs/方案/方案-记忆更新收敛与Milvus投影一致性.md`（批内收敛已做，跨会话裁决=本方案）
> 红线：**裁决逻辑必须是确定性系统代码，不依赖 LLM 思考能力**（用户 2026-09-09 明确要求）

---

## 0. 机制分解（读这篇文档的地图）

> D4 常被简化成「版本裁决 + 切实体」两块，实际是**三层十环节**。
> 分层依据是**一条事实从产生到被用要连续回答的问题**：
> **归位**（把 LLM 的自由文本映射到确定性坐标）→ **裁决**（比时间、定动作）
> → **传导**（把裁决结果一路传到注入窗口，三层不打架）。

### 0.1 调用链（函数级）

```
LLM 提取输出 (category, key, fact, value)
    │
    ▼
normalize_key()  ─── 归位 ──────────────────────────────────────────
    ├─ ① lifecycle 前缀解析   original_/previous_/former_/old_ → historical
    │                        current_/new_/latest_/updated_  → current
    ├─ ② 属性归一             _CANONICAL_ALIASES 别名表 → canonical attribute
    └─ ③ 实体解析             resolve_entity: P0 编号 → P1 产品码
                              → P3 key 内实例名 → P3a 专名 → SELF
    ▼  NormalizedKey(entity_ref, attribute, lifecycle) ≡ 签名
collapse_same_signature()  ─── 批内收敛（Fix B 的判据视图）──────────
    ▼
plan_versioning()  ─── 裁决 ────────────────────────────────────────
    ├─ 批内二次收敛   同签名取时间最新 → batch_collapsed
    ├─ historical 分支 同值跳过 / 否则插入（**永不取代、也不被取代**）
    ├─ 无 existing    → INSERT v1
    ├─ 同值           → skipped_same
    ├─ 更新           → INSERT v+1 + supersede_ids
    └─ 更早           → ignored_older
    ▼  VersioningPlan
SQLite 落库  ─── 传导① 权威源 + 版本链（version / supersedes_id）───
    ▼
Milvus 投影  ─── 传导② 只镜像 lifecycle=current；删旧插新；
                 投影键 = projection_key(entity_ref, attribute)
    ▼
检索/注入    ─── 传导③ StructuredKeyDedup → struct_provider 渲染
    ▼
answer
```

### 0.2 逐环节与状态

| 层 | 环节 | 回答的问题 | 载体 | 状态 |
|---|---|---|---|---|
| 归位 | ① 生命周期判定 | 这是**现在**的还是**过去**的？ | `_HISTORICAL/_CURRENT_PREFIXES` | ✅ D4-1 |
| 归位 | ② **属性归一** | 这是**什么性质**的？ | `_CANONICAL_ALIASES` | ⚠️ **唯一未解决**（D4-1.5） |
| 归位 | ③ 实体解析（切实体） | 这是**谁**的事？ | `resolve_entity` | ✅ D4-2 v1/v2 |
| 裁决 | ④ 签名比对 | 和库里已有的**是不是同一件事**？ | `signature` 三元组 | ✅ D4-1 |
| 裁决 | ⑤ 时间序 as-of | 哪个**更新**？ | `_is_newer` / `source_started_at` | ✅ D4-1 |
| 裁决 | ⑥ 动作分支 | **该怎么办**（覆盖/忽略/并存）？ | `plan_versioning` | ✅ D4-1 |
| 裁决 | ⑦ 批内收敛 | **同一批内**怎么办？ | `collapse_same_signature` | ✅ Fix B/C |
| 传导 | ⑧ 权威落库 | **存哪**、版本链怎么留？ | SQLite `struct_memories` | ✅ D4-3 |
| 传导 | ⑨ 投影收敛 | 索引**怎么不跟权威源打架**？ | `projection_key` | ✅ D4-3 |
| 传导 | ⑩ 检索注入 | **怎么取出来**不拿到过期值？ | `StructuredKeyDedup` | ✅ D4-3（已弱化） |

### 0.3 四个关键认识

**① 难度分布极不均匀：难在「归位」，不在「裁决」。**

裁决层是**纯逻辑**（比时间、定动作），已做完且 D4-4 五例回放**全对**。
难的是归位层——它要把 LLM 的**自由文本**映射到**确定性坐标**，
而 LLM 每次可以用不同的词表达同一个属性（§8.2 的 `amount` vs `wire_amount`）。
**归位层三列里，属性列是唯一还没解决的。**

**② 三列不是平等的，是相乘的。**

| 列 | 本质作用 | 归错了会怎样 |
|---|---|---|
| `entity_ref` | **防覆盖** | 不同主体互相覆盖（丢事实） |
| `attribute` | **防漂移** | 本该打架的两条**认不出对方** → 旧值赖着不走 |
| `lifecycle` | **准入资格** | 历史快照被当成 current 注入 |

任何一列归位错了，裁决层就**永远看不到**它们该打架——
这也是 §8.2 case12 的病灶：事实是同一桩电汇，但属性列把它们分到了两个命名空间。

**③ 「批内收敛」是裁决的退化情形，本质是归位失败的症状。**

同一 ingest 内所有事实共享同一个 `source_started_at` → `_is_newer` 恒 False
→ 时间序失效 → 只能退化成「先到先得」（`plan_versioning` L134-152）。
**若实体/属性归位正确，同一批内同签名本就该只留一条**；
出现大批量批内折叠，说明归位把不同主体/不同属性挤进了同一个签名。

**④ 传导层有一个反直觉设计：投影键 = 收敛键，不是标识键。**

见 §3.4：`projection_key(entity, attribute)` 让**多版本在 Milvus 里收敛成一条向量**。
这不是为了去重，而是为了**让注入窗口天然只看到 current**。
这层不做的后果：SQLite 里版本链完美，注入窗口里 3 个金额照样并存。

---

## 1. 现状诊断（run_9b8256bd5f 实测数据）

### 1.1 已有的版本原语（但失效）

- SQLite `struct_memories` 落库按 `(user_id, category, key)` upsert：同键新值覆盖、旧值归档 `previous_fact`（struc_mem_service.py L59-101）。
- Milvus 按 category 删旧插新，批内按 `(category, key)` 投影收敛（`_converge_by_key`，**已删**：D4-3 起改为只投影 SQLite 回读的 lifecycle=current 赢家，见 §3.4 与 8f82058）。
- **失效原因：收敛签名是 key 字符串精确相等，而 LLM 提取的 key 严重漂移。**

### 1.2 实锤：同一事实被提成 6 个 key（case 12，电汇金额演进 $85k→$100k→$95k）

| key | value | 来源会话 | 会话时间 |
|---|---|---|---|
| final_transfer_amount | $85,000 | initial_transfer_001 | 09-15 |
| gift_amount | $100,000 | husband_changes_001 | 09-25 |
| original_wire_amount | $85,000 | husband_changes_001 | 09-25 |
| transfer_amount | **$95,000** | wife_reversal_001 | **09-26（最新）** |
| wire_amount | **$95,000** | wife_reversal_001 | 09-26 |
| wire_transfer_amount | **$95,000** | wife_reversal_001 | 09-26 |

6 条全部独立存活 → 检索 top-20 里 3 个不同金额并存，answer 无法判断 current。
同类漂移：日期 `wire_date/transfer_date/original_wire_date/gift_date/deposit_date/funds_needed_by`；
reference `wire_reference/wire_reference_number/transfer_reference_number/reference_number`；
routing `routing_number/daughter_routing_number`（同值 021000021）。

### 1.3 三类必须区分的情况（不能粗暴合并）

1. **真版本演进**：wire $85k→$100k→$95k —— 同实体同属性，**最新值是 current**，旧值进历史。
2. **历史值被显式命名**：`original_wire_amount=$85,000` 语义就是"原始金额"，与 current 是**两个不同属性**，不能覆盖也不能当 current 注入。
3. **多实体并存**：case 10 中 user 与同事 Jennifer 的座位/航班；`recipient_bank=Chase`（电汇接收行）vs 其他主体银行——key 必须带实体维度才能区分。

### 1.4 时间戳条件已具备

- `conv_meta.started_at` 存的是**对话内时间**（case 12：2024-09-15 / 09-25 / 09-26），由用例 timestamp 解析而来（harness `_parse_ts`）；生产环境即真实会话时间。
- struct 行有 `source_conversation_id` 可 join 回会话时间——**但表本身没存时间，裁决需 join 或落列**。

---

## 2. 设计目标与非目标

**目标**
1. 同「实体 + 属性」的事实跨会话只保留一条 current，旧值可追溯（版本链）。
2. 注入 answer 的记忆默认只含 current；"历史/变更过程"类问题能取到版本链。
3. 裁决确定性、可审计、可单测；LLM 只做一次性的信息抽取，不做跨会话推理。
4. 对提取侧 key 漂移有韧性（不指望 LLM 每次输出同一个 key）。

**非目标（本方案不做）**
- 不做全局实体知识库/实体消义大模型；实体域限定"用户生活中的可数主体"（人、账户、保单、行程、工单…）。
- 不解决多用户共享实体；不做软删除以外的 GDPR 能力。
- 不动 layer1 已通过用例的行为（回归保护）。

---

## 3. 核心模型：实体域 + 规范属性 + 版本链

### 3.1 事实身份签名（收敛键升级）

```
旧： (user_id, category, key)
新： (user_id, entity_ref, attribute)
```

- **entity_ref**：事实归属的实体标识。缺省实体 = `SELF`（用户本人，绝大多数事实）。
  非本人实体从值/上下文规范化：`PERSON:Sarah Thompson-Miller`、`PERSON:Jennifer Walsh`、
  `CASE:WT-89089`（一桩电汇）、`POLICY:<号>`、`ITINERARY:<去/回方向>`…
- **attribute**：规范属性名（canonical key），如 `wire.amount`、`wire.date`、
  `wire.reference`、`flight.seat`。层级命名天然区分实体/属性。
- `original_wire_amount` 这类**历史快照**不映射到 `wire.amount`，映射到
  `wire.amount@origin`（或打 `lifecycle=historical` 标记，见 3.3），与 current 共存不冲突。

### 3.2 key 规范化怎么来（三层防线，都不靠运行时思考）

| 层 | 手段 | 性质 |
|---|---|---|
| L1 词表 | 建 `fact_key` 受控词表（仿现有 `fact_category` 表）：category 下的 canonical attribute 列表 + alias 映射（wire_amount/transfer_amount/wire_transfer_amount/final_transfer_amount → wire.amount）。prompt 渲染词表，要求复用 | 数据，可管理演进 |
| L2 入库归一 | 入库前确定性 normalizer：精确 alias 表 + 规则（去前缀修饰词后查 canonical、`new_/current_/latest_` 前缀剥离视为 current；`original_/previous_/old_/former_` 保留为 historical 变体） | 纯代码 |
| L3 离线聚类（可选，后置） | 新增 alias 不从零写：跑一个**离线批处理** LLM 任务，把历史全部 distinct key 聚类成 canonical 组，人工 review 后灌词表。一次性、可审计，不在请求链路 | 离线人工把关 |

### 3.3 版本链存储

`struct_memories` 增列（init_db 轻迁移，旧行默认值回填）：

| 新列 | 说明 |
|---|---|
| `entity_ref TEXT DEFAULT 'SELF'` | 实体标识 |
| `attribute TEXT` | 规范属性名（回填 = 旧 key） |
| `lifecycle TEXT DEFAULT 'current'` | current / historical / superseded |
| `source_started_at DATETIME` | 来源会话时间（落列免 join；从 conv_meta 带入） |
| `version INT DEFAULT 1` | 同签名版本号 |
| `supersedes_id TEXT` | 上一版本行 id（版本链可追） |

跨会话入库裁决（替换现 L59 的同键 upsert）：
1. 计算新事实签名 `(user, entity_ref, attribute)`；
2. 查同签名 current 行：
   - 不存在 → INSERT current；
   - 存在且 `source_started_at` 更新（或同会话内后批）→ 旧行置 `superseded`（保留行不删），新行 INSERT current，version+1，supersedes_id 指旧行；
   - 更早或同期同值 → 忽略/只补 confidence；
3. **historical 变体独立签名**，永不被覆盖，也不参与 current 注入。

> 为什么不沿用"一行 UPDATE + previous_fact 单字段"：演进超过 2 次（$85k→$100k→$95k）时单字段链断裂，且无法回答"变更过程"。独立版本行 + 链指针支持任意长度历史。

### 3.4 Milvus 投影

- 投影只写 **lifecycle=current** 的行；superseded 行从 Milvus 删除（保持"每签名一条 current"）。
- 投影收敛键同步换成 `(user, entity_ref, attribute)`。
- 历史检索（"how did X change"）走 SQLite 版本链，不走向量——历史问题是精确的时间线查询，不需要语义召回。

### 3.5 检索/注入

默认不变（向量召回 + 现有策略链），加两条确定性策略：
1. **CurrentOnlyFilter**：命中行 superseded 一律过滤（投影层已保证，双保险）。
2. **实体消歧保序**：查询含实体线索（"my daughter's house"）时，同 attribute 的 SELF 行与该实体行同时候选时优先实体行——轻量规则（实体名命中 fact/value 加权），不上 LLM。

---

## 4. entity_ref 从哪来（不依赖思考的实用主义方案）

不要求 LLM 在提取时输出完美实体（那等于把裁决推回模型）。分两步：

1. **提取 prompt 增量字段（可选）**：输出 `entity_hint`（自由文本，如 "Sarah's house wire" / "Jennifer's flight" / 空=本人）。只是提示，不是权威。
2. **入库前确定性实体解析器 `resolve_entity(fact, category, key, value)`**：
   - 从 key 前缀线索映射：`daughter_*`→已知女儿实体；`recipient_*`+人名值→对应 PERSON；
   - 从 value 命名词典：本 user 已提取的人名集合（PERSON 实体注册表，新表或从 family/personal category 现取现建）；
   - 命中不了 → SELF（安全默认：宁归本人不悬空）。
   - 全部规则可单测；case 10/12 所需规则 ≤10 条，随用例迭代。

---

## 5. 分阶段实施（每阶段可独立验证/回滚）

| 阶段 | 内容 | 验收 |
|---|---|---|
| **D4-0** | struct_memories 加 5 列 + init_db 迁移 + 回填（entity_ref=SELF, attribute=key, lifecycle=current, version=1, source_started_at=join 回填） | 176 单测绿；旧数据可查；零行为变化 |
| **D4-1** | L1/L2 key 规范化：fact_key 词表 + alias 映射（先手工覆盖 case 10/12/17 实测漂移的 ~40 个 key 簇）；入库签名换 (entity,attribute)；时间序 latest-wins + 版本链写入 | 新增单测：$85k→$100k→$95k 重演后 current=$95k 且历史可追；original_* 不被覆盖 |
| **D4-2** | 确定性实体解析器（§4，先 SELF + key 前缀 + 人名命中三类） | case 10 user/Jennifer 座位不互相覆盖 |
| **D4-3** | Milvus 投影只写 current + 删旧换新签名；检索 CurrentOnlyFilter | 投影一致性检查脚本通过 |
| **D4-4** | 小批量回放：12/10/11/20/17 五例重置提取，不跑 judge，直接验库（current 值正确、版本链完整、注入快照含正确值） | 人工/断言核对 |
| **D4-5** | 全量 layer2 复测 + 多一轮看噪声带 | 对比 5/20 基线 |

**每阶段一个 commit，单测先行；D4-1 完成即可见主要收益（12 类金额/日期矛盾题）。**

---

## 6. 风险与对策

| 风险 | 对策 |
|---|---|
| alias 误并（把两个不同属性归一） | L2 只归一高置信精确 alias；不确定的不归一（保留独立行=现状，不会更差）；alias 入词表可管理 |
| latest-wins 误判（迟到的旧会话后处理） | 裁决按 source_started_at（对话时间）不是入库时间；同值幂等；版本链可人工修正 |
| entity 误归（把 Jennifer 的事归成 SELF） | 命不中安全归 SELF；D4-2 规则保守；误归可观测（注入快照审计） |
| 历史行膨胀 | 历史不入 Milvus；SQLite 体量可控；后续可加保留窗口 |
| prompt 改动重新引入指纹漂移 | entity_hint 为可选追加字段；v1.5 主体不动；D4 先靠系统侧归一，prompt 改动放 D4-1 验证后 |

---

## 7. 与既有架构的位置

- 新模块建议：`os_mem/extractor/normalize.py`（key 规范化 + 实体解析，纯函数，单测密集）
  与 `os_mem/core/services/memory_versioning.py`（版本裁决/入库事务）。
- FactExtractor 职责不变（抽取）；StructuredMemService 的入库段改为调版本裁决。
- 词表走 `fact_key` 表（仿 fact_category，os_mem.admin 管理窗口后续补）。
- 符合既定分层：任务语义 / 确定性系统策略 / 模型数据画像——**裁决是系统策略，不是模型策略**。

---

## 8. D4-4 五例真实回放验证结果（2026-09-09）

脚本 `scripts/d4_replay_5.py`（重置 10/11/12/17/20 → 仅提取入库 → 验库/验投影，不跑 judge）。

### 8.1 机制层：完全正确 ✅

- 版本链在全部 5 例触发：10 例 11 superseded / 7 historical；12 例 11 / 2；11 例 4；20 例 5。
- 跨会话同属性 latest-wins 正确，且不限于 wire 簇：
  - 10 例 `confirmation_number` KJMN89→LMPQ72→**NPRS45**（v1/v2 superseded，v3 current）；
    `departure_date` Nov15→Nov12→**Nov22**；`seat_assignment` 21C/21D→21A。
  - 11 例 `medication` Methotrexate→prednisone→**CBD**；`symptom`→**psoriasis patches**。
  - 20 例 `insurance_change` PPO→**HMO**；`medication` Ozempic,Metformin→**Ozempic**。
- historical 快照独立共存（original_wire_amount=$85k 与 current 并列，不被覆盖）。
- **Milvus 投影干净**：每 (user, canonical attribute) 恰好一条 current 向量（实测 query 确认），
  superseded/historical 不投影。case12 wire_reference 三版本正确收敛到 **WT-89089**。
- 迟到旧会话不覆盖、同值幂等：单测 + 真实数据均验证。

### 8.2 剩余缺口：attribute 语义归一不足（case12 金额/日期未收敛）❌

case12 最终会话（09-26）模型把金额提成**泛词 `amount`**（fact 句 "User will send $95,000
on October 1st"，不含 "wire"），而非任何 wire_* 变体：

```
amount        current = $95,000   wife_reversal(09-26)   ← 真值，但在独立命名空间
wire_amount   current = $100,000  husband_changes(09-25) ← 停在旧值（簇内 latest）
wire_date     current = Sept 27   husband_changes(09-25) ← 同因（Oct1 在 amount 句中）
```

- 静态 alias 表追不上：同一事实，模型在会话1/2 用 `wire_amount`，会话3 退化为 `amount`，
  纯字符串别名无法预知每次漂移到哪个泛词。
- 注入后果：top-20 召回含 wire_amount=$100k / wire_date=Sept27（合法 current，非投影脏数据），
  而 $95k 的 fact 句无 "wire" 词、语义相关性低被挤出 → answer 仍看到旧值。
- 这是**语义归一/实体属性锚定**问题，不是版本裁决问题。

### 8.3 下一步方向（待评审，红线：不靠运行时模型推理）

1. **提取时携带已有 attribute 词表（推荐，确定性上下文锚定，非思考）**：处理实体的后续会话时，
   把该 user 已存在的 canonical attributes（如 wire_amount/wire_date/…）组装进提取 prompt，
   要求"同实体同属性必须复用下列 key"。模型本就能产出 wire_amount，缺的是跨会话一致性约束。
2. 扩充 L1 alias（case-by-case，脆，作为补充不做主力）。
3. L3 离线聚类灌词表（原方案已列，周期性维护）。

结论：**D4-0/1 的版本裁决与投影收敛目标达成且可复现**；layer2 矛盾题要拿分，关键转到
§8.3-1「提取期跨会话 attribute 锚定」，列为 D4-1.5 / 下一迭代。
