# 方案：事实 category 与 key 受控词表（草案，待评审）

日期：2026-09-08 · 状态：**草案（待用户评审收敛）** · 关联：`os_mem/utils/extract_prompt.py`、
`os_mem/utils/fact_extraction.py`、评测提取链路、`os_mem/admin` 管理窗口
上游：方案-记忆更新收敛 §4.5（key 规范化，prompt 约束未治住）、§11（串键覆盖丢事实）

## 1. 目标（本次改动范围）

把事实提取链路上 **hardcode 的 category 与 key 词表**抽成数据库表统一管理，词条支持
中英文双语；提取 prompt 与校验逻辑改为**读表驱动**，改词表不再改代码/不再动 prompt 指纹。

| 现状 hardcode | 位置 | 行为 |
|---|---|---|
| allowed category 枚举（prompt 提示） | `extract_prompt.py` SYSTEM_PROMPT L43-45 | LLM 只从 10 类里选 |
| 候选 key 参考（prompt 提示，按 category） | `extract_prompt.py` SYSTEM_PROMPT L49-63 | ~45 个参考 key，LLM 可自拟 |
| `ALLOWED_CATEGORIES` 硬白名单（校验） | `fact_extraction.py` L36-47（validate_response L97） | **category 出界 → ValueError → 整批返回 [] → 丢事实** |

**数据证据（2026-09-08 探针，memories.db 1130 行）**：非 verbatim 的 LLM key 608 行 =
505 个 distinct key，**93% 的 key 全库只出现一次** → prompt 里的候选参考对 LLM 约束
基本失效（同义新 key 发散）；复用收敛的只有 full_name(19)/email(13)/address(11)/
date_of_birth(11)/phone_number(9) 等少量自然规范 key。category 全部落在 10 类内
（白名单校验有效，无出界）。

**非目标**：不做 key 自动归一/语义归并（§4.5 被否的 C 方案仍不引入）；不动
verbatim 指纹 key（`verbatim_<sha12>` 独立体系，与词表无关）；本轮不做 EvalView
词表管理前端（先落地表+读表驱动+admin 窗口，UI 二期）。

## 2. 术语

| 词 | 含义 |
|---|---|
| category | 事实大类（personal/contact/finance/…），事实的粗粒度分组 |
| key | category 内的事实字段名（英文 snake id，如 `checking_account_number`），(category,key) 构成 upsert 冲突键 |
| 受控词表（catalog） | 由表管理、可启停、可增补的 category/key 枚举；提取 prompt 与校验的数据源 |
| name_en / name_zh | 词条英文名 / 中文名（展示、prompt 双语提示用） |
| aliases | 同义表述（中文为主），供 LLM 联想与未来归一使用 |

## 3. 设计（定稿骨架）

### 3.1 两张词表（业务库 memories.db，os_mem 自有表）

```
fact_category            -- allowed category 词表
  category   TEXT PK     -- 英文规范 id（personal/contact/...）
  name_zh    TEXT        -- 中文名（个人/联系方式/财务/...）
  name_en    TEXT        -- 英文名（同 category，冗余便于展示）
  sort       INT         -- 展示顺序
  active     INT 1       -- 0=停用（不进 prompt、校验视为非法）
  created_at / updated_at

fact_key_catalog         -- category 内候选 key 词表
  category   TEXT        -- FK → fact_category.category（PK 一部分）
  key        TEXT        -- 英文 snake 规范 id（PK 一部分）
  name_zh    TEXT        -- 中文名（如「支票账户号码」）
  name_en    TEXT        -- 英文名（如 "checking account number"）
  aliases    TEXT ""     -- 逗号分隔同义表述（中英），prompt 提示用
  active     INT 1       -- 0=停用（不进 prompt 候选）
  updated_at
```

- ORM：`os_mem/entries/mem_models.py` 增两模型；登记进 `mem_storage.init_db`
  create_all 显式表清单（沿用「不建进评测库」纪律）。
- **Seed 幂等**：init_db 后检查空表则灌入内置种子（常量放 os_mem 内部独立模块
  `os_mem/utils/key_catalog_seed.py` 或 entries 旁）。种子内容见 §3.4。

### 3.2 提取 prompt 读表驱动（模板与数据分离）

`extract_prompt.py` 的 SYSTEM_PROMPT 重构为**静态模板 + 两个占位段**：

```
SYSTEM_PROMPT（模板）：
  ...
  ## 提取规则
  2. category 必须从以下列表选取：{categories_section}
  3. key 是字段名... 候选 key 参考（按 category）：{keys_section}
  列表之外... 不得为同一事实生成多个近义 key。
  ...
```

- `build_extract_messages(dialog_text)` 调用时读表渲染两段：
  - `categories_section` = active category 的 `name_en（name_zh）` 列表；
  - `keys_section` = 按 category 分组的 active key，渲染 `key（name_zh/首个 alias）`；
    词表未来膨胀时对每类限额渲染（见 §7 风险 1）。
- 读表实现：每次调用查 SQLite（廉价）+ 进程内不缓存（评测进程内词表只增不减场景简单，
  避免缓存失效问题；如实测热点再引入 ttl 缓存）。
- `{max_facts}` 占位保留（test_prompt_fp 依赖模板含该占位）。
- category/key 内部仍是英文规范 id —— **LLM 输出不变**（prompt 只是帮助它在中文
  对话里联想规范 id）。

### 3.3 校验读表（ALLOWED_CATEGORIES 去硬编码）

`fact_extraction.py`：
- `ALLOWED_CATEGORIES` 常量删除，改为从 `fact_category` 读 active 集
  （validate_response 内每次查表或模块级 lazy 读取；保持「出界即 ValueError」的
  既有强校验语义不变——只是列表来源表化）。
- **新增 key 观察校验（默认 warning）**：输出 key 不在 `fact_key_catalog` active 集 →
  记 warning 日志（含 category/key/fact 前缀），**不拒绝**（拒绝会整批丢事实，
  词表未收录 ≠ 事实无效，§4.5 原 validate 也是 warning 不拒绝）。
- 词表 → 校验闭环：停用某 key → LLM 不再被推荐且告警；新增 key → 立即生效。

### 3.4 种子（首版 catalog 内容来源）

| 来源 | 收录 | 说明 |
|---|---|---|
| 现有 prompt 候选枚举 | 全部 | ~45 条，人工书写过，语义干净 |
| DB 实测**复用≥2 次**的非 verbatim key | 收录为 active | full_name/email/address/date_of_birth/…（实测 top25 左右），自然收敛的规范 key |
| DB 仅出现 1 次的 472 个 key | **不收录** | 发散噪音（同义新 key/串键），收录进词表只会让 LLM 更发散 |

name_en/name_zh/aliases 需人工审定（种子文件手写，中文名按类别语义归纳；数量可控：
10 category + ~70 key）。后续词表演进 = 管理操作（admin 窗口/未来 UI），不再走代码。

### 3.5 管理窗口（os_mem.admin 扩展）

`MemAdminService` 增词表管理方法（只读列表 + 受控写），供 CLI/curl/未来 EvalView 页：
- `list_categories()` / `list_key_catalog(category=None, active_only=True)`；
- `upsert_key(category, key, name_zh, name_en, aliases, active)`；
- `set_category_active(category, active)` / `set_key_active(category, key, active)`；
- 外部管理词表仍只能经窗口（沿用 2026-09-08 分层纪律：禁直连 ORM 改表）。
HTTP 路由/前端本轮不做（二期），先提供窗口方法 + 单测。

### 3.6 指纹与评测对照

- 静态模板保留 → `SYSTEM_PROMPT_FINGERPRINT` 语义不变（模板指纹）。
- **渲染后实际 system 内容随词表变化** → 新增 `catalog_fingerprint`（对渲染两段
  拼接文本做 `fingerprint()`），随评测 `config_snapshot` 落库 → 「某次跑分 ↔ 当时
  词表内容」可对照；跨 run 比通过率需同 catalog 指纹（模板版本 + 词表版本双对齐）。
- 改词表后与历史基线对比 = 建新基准流程（清 conv_meta + 按需 drop mem_os 重跑，
  §7 流程；LLM 成本与是否跑由用户确认）。

## 4. 评测影响

1. 模板改动 → `SYSTEM_PROMPT` 指纹变化 → 与旧 run 对比需清缓存重建基准（用户确认才跑）；
2. seed 后词表进 prompt → 提取行为变化（更收敛的 key），struct+layer1 全量重提取成本
   ≈36min/次，由用户显式发起；
3. `validate_response` category 校验来源改表但语义不变 → 无回归风险；
4. key warning 日志新增 → 检索策略/落库不变。

## 5. 实施批次（每批可独立提交验证）

| 批 | 内容 | 验证 |
|---|---|---|
| 1 | 两表 ORM + init_db 建表/seed（幂等）+ 种子数据文件 | 单测：seed 幂等（跑两次行数不变）、表结构/active 集正确 |
| 2 | prompt 模板占位化 + 读表渲染（双语）+ ALLOWED_CATEGORIES 去硬编码 + key warning | 单测：渲染含中英双语、与表一致、category 出界仍 ValueError、key 未收录仅 warning |
| 3 | admin 窗口词表方法 + 单测；指纹/对照落库字段 | 单测：窗口 CRUD/启停后渲染变化、catalog_fingerprint 稳定 |
| （可选 4） | EvalView 词表管理页 | 二期，另行评审 |

## 6. 测试计划（无 LLM，可自由跑）

- `tests/unit/test_key_catalog.py`：seed 幂等；active 过滤；渲染段与表一致；模板占位
  （`{categories_section}`/`{keys_section}`/`{max_facts}`）；catalog_fingerprint 对
  词表变化敏感、同词表稳定。
- `tests/unit/test_fact_extraction.py` 增量：category 出界 ValueError 语义保持；
  key 未收录 warning（caplog）。
- `tests/unit/test_mem_admin_service.py` 增量：词表窗口方法 CRUD + 启停。
- 既有 unit 全量回归（108+）；`test_prompt_fp` 4 指纹唯一性/确定性保持。
- **评测不跑**（改提取语义，验证需用户显式发起 layer1 重跑 + 确认参数）。

## 7. 风险与开放问题

1. **prompt 长度膨胀**：词表增长（未来 100+ key）会撑大 system prompt。对策：
   每类候选渲染上限（如 15 条/类，超出按 sort 截断）；必要时 prompt 只给高活跃子集。
2. **词表是约束不是铁律**：LLM 仍可能自拟 key → warning 观察 + 定期从 warning 日志
   反哺词表（运营闭环，本期只留日志出口）。
3. **串键（异义同 key）不受本方案直接治理**：词表管「同义同 key、规范命名」，
   管不了「一个通用 key 塞多个事实」（§11 layer1_03 型）——根治需 key 细化/来源
   锚定，另案。词表先稳住命名层。
4. **category 扩展**：新增 category 需 seed/管理补 name_zh 等 → 表已支持；注意
   校验读表后新增即生效，评测语义随之变（同 1 的指纹对照约束）。
5. **缓存**：暂不做读缓存（每次查表）；若提取性能实测受影响再引入（注意评测进程
   内词表变更一致性）。

## 8. 机制术语 + 设计考量学习笔记

- **白名单校验 vs 提示约束**：`ALLOWED_CATEGORIES` 是硬校验（不在此列 → 整批失败），
  候选 key 只是提示（软约束）。硬校验保证结构完整性但会整批丢数据 → 放宽松的校验
  让 prompt 引导 + warning 观察是工程权衡。词表化后两类约束同源同数据，行为一致。
- **模板与数据分离**：prompt 中「结构指令」（稳定）与「领域词表」（会演进）混写导致
  每次加 key 都要改代码+指纹漂移。拆开后：模板指纹管「指令版本」、catalog 指纹管
  「词表版本」，两层可独立对照（双指纹）。
- **受控词表生命周期**：seed（首次灌入）→ 增补（管理操作）→ 停用（active=0 软删除，
  保留历史引用完整性）→ 观察（warning 反哺）。软删除优于物理删除：历史 struct_memories
  行仍引用旧 key，硬删会破坏审计。
- **枚举治理 vs 自由生成**：让 LLM 自拟枚举（key）在生成侧天然发散（93% 单次 key 为证）；
  受控词表把「发散面」从生成时收窄到「词表维护时」，发散成本从每轮评测转移到一次性的
  词表评审。

## 9. 决策记录（含待拍板项）

| 项 | 待拍板 | 推荐 |
|---|---|---|
| A. 中英文语义 | key/category 保持英文 id + 表内双语名/中文别名（prompt 双语提示）**vs** key 允许中文输出 | **英文 id**（检索/代码/投影 (user,key) 稳定；中文输出会破坏既有体系） |
| B. 表结构 | category+key 两张表 **vs** 单表 | **两张**（category 是 key 的分组维度且自身需校验白名单/双语/启停，职责不同） |
| C. key 校验强度 | 未收录 key 仅 warning **vs** reject 强制收敛 | **warning**（reject 会整批丢事实；词表未收录≠无效） |
| D. seed 范围 | prompt 枚举+复用≥2 次自动收录 **vs** 纯手工精修 | **自动收录+人工审定 name/别名**（472 个单次 key 一律不收） |

拍板后本方案状态转定稿，按 §5 批 1→3 实施。
