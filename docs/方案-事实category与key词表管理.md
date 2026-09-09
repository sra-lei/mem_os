# 方案：事实 category 受控词表（已实施）→ key 词表（已评估：layer1 阶段不立项）

日期：2026-09-08 · 状态：**已实施（category 期，2026-09-08）**：批1-3 完成（表/seed/vocab/
prompt 渲染/校验读表/admin 窗口），单测 133 passed 全绿，真实 memories.db 已幂等建表+seed；
key 词表（`fact_key_catalog`）**评估更新见 §1 下一期段（2026-09-09：layer1 阶段不立项）**
关联：`os_mem/extractor/prompt.py`、`os_mem/extractor/fact_extractor.py`、提取链路、`os_mem/admin`
上游：方案-记忆更新收敛 §4.5（key 规范化）、§11（串键覆盖）；评测数据 93% key 单次出现（发散）

## 1. 目标与范围（本次改动）

把事实提取链路上 **hardcode 的 allowed category 词表**抽成数据库表统一管理，词条支持
**中英文双语**（英文规范 id + name_zh/name_en），prompt 与校验改为**读表驱动**——
改 category 不再改代码、不动代码层。

| 现状 hardcode（category 相关） | 位置 | 处理后 |
|---|---|---|
| allowed category 枚举（prompt 提示） | `extractor/prompt.py` SYSTEM_PROMPT 规则 2 | `{categories_section}` 占位 → 读表渲染（双语） |
| `ALLOWED_CATEGORIES` 硬白名单（校验） | `extractor/fact_extractor.py` validate_response | 常量删除 → 读 `fact_category` active 集（出界 ValueError 语义不变） |

**范围外（本期不动，保持现状）**：
- 提取规则 3 的 **key 部分全部保留**：候选 key 参考列表、inline 示例
  （'email'/'seat_preference'/…）、key 行为约束与自拟条款（key 词表下一期，届时统一收编）。
- key 未收录校验（warning）本期不做（无 key 词表无从对照）。
- verbatim 指纹 key 体系（`verbatim_<sha12>`）与词表无关。
- EvalView 词表管理前端（二期）。

**下一期（key 词表）——评估更新 2026-09-09：layer1 阶段不立项，转 layer2/3 基线后再定**

原设想（用户已定策略）：`fact_key_catalog` 表；seed=prompt 枚举直接收录 active +
实测复用≥2 次 key 进观察列表（非 active）；未收录输出 key 仅 warning；词表外保留自拟 +
warning 反哺。

**评估结论与数据**（基于提取侧成果落地后，R1 覆盖去重 + 事实句英文化 + 兜底盲区，
见 git 559b081/8dbb335 与 方案-检索注入verbatim区分策略.md）：
- 对 E2 全英文新库逐期望 token 检测"多行多 key 冗余"：基本**单值单 key**；命中的多行
  多为**不同语义恰好同数值**（11 的 45000 = 薪资/储蓄/售房三笔不同事实，不可合并）或
  **子串误报**（5000 ∈ 15000）；仅 18 的 Freedom 2045 出现一例同义双 key
  （rollover_ira_fund / rollover_ira_investment）——零星冗余，可用 prompt 纪律 +
  key 观察列表反哺低成本压住，无需整套表实体化；
- 当前 layer1 分数墙（覆盖漏 31 / 回答漏 6，见 §八-续）与 key 无关，key 实体化救不了；
- key 实体化的真正买单场景 = **layer2/3 多会话跨会话收敛**：新会话换 key 提同一事实 →
  `(user, key)` upsert 收敛失效 → 旧值残留/双版本并存；届时需要的是 §10 来源锚定/新旧
  判定机制，词表只是地基之一。

**落地路径（若 layer2/3 基线确认需要）**：先最小形态——key 观察列表（实测复用≥2 次）
+ 词表外输出 warning 反哺（提取校验处小改，清缓存重提取验证），再决定是否整套
`fact_key_catalog` 实体化。

## 2. 设计（定稿）

### 2.1 单张词表 `fact_category`（业务库 memories.db，os_mem 自有表）

```
fact_category            -- allowed category 词表
  category   TEXT PK     -- 英文规范 id（personal/contact/...）
  name_zh    TEXT        -- 中文名（个人/联系方式/财务/...）
  name_en    TEXT        -- 英文名（同 category，冗余便于独立展示/排序）
  sort       INT         -- 展示顺序（prompt 与 UI 用）
  active     INT 1       -- 0=停用（不进 prompt 渲染；校验视为非法 → 整批 ValueError，与现语义一致）
  created_at / updated_at
```

- ORM：`os_mem/entries/mem_models.py` 增 `FactCategory`；登记进
  `mem_storage.init_db` create_all 显式表清单（不建进评测库纪律保持）。
- **Seed 幂等**：init_db 建表后，空表则灌入内置 10 类双语种子（常量在
  `os_mem/vocab.py`）；非空不覆盖（词表演进走管理操作，seed 只负责首次）。
- 停用某 category → LLM 不被推荐 + 输出该 category 即校验失败（与现状白名单语义一致：
  当前 10 类全部 active，行为零变化）。

### 2.2 词表读取与 prompt 渲染（模板与数据分离）

- 新增轻模块 **`os_mem/vocab.py`**（顶层文件，import 链只过 os_mem/__init__，无副作用；
  函数内 lazy 建 MemoryDatabase 会话）：
  - `list_active_categories() -> list[dict{category,name_zh,name_en}]`（按 sort）；
  - `render_categories_section() -> str`：渲染 `name_en（name_zh）` 逗号分隔列表。
- `extractor/prompt.py`：SYSTEM_PROMPT 规则 2 段改为 `{categories_section}` 占位；
  `build_extract_messages()` 调 `vocab.render_categories_section()` 替换（每会话一次 SQLite
  读，廉价；不做缓存——评测进程词表只增不减，避免缓存失效问题）。
- 规则 3 及之后文本原样保留（key 期前不动），`{max_facts}` 占位保留。

### 2.3 校验去硬编码（ALLOWED_CATEGORIES）

`extractor/fact_extractor.py`：
- `ALLOWED_CATEGORIES` 常量删除；`validate_response` 改为取
  `vocab.list_active_categories()` 的 category 集合校验（每次调用读表）。
- **校验语义不变**：category 不在 active 集 → ValueError → 整批返回 [] → 上层重试/降级
  （与现状白名单完全一致，只是来源表化）。停用类别后旧缓存/脏数据出界即拦截——符合
  白名单初衷。
- 注意：读表每次查询的异常兜底——词表表缺失/查询失败时回退内置 10 类（防校验全拒导致
  整批丢事实，见 §5 风险 1）。

### 2.4 管理窗口（os_mem.admin 扩展）

`MemAdminService` 增 category 词表方法（外部管理仍只经窗口）：
- `list_categories(active_only=True)`；
- `upsert_category(category, name_zh, name_en, sort, active)`；
- `set_category_active(category, active)`。
HTTP 路由/前端二期（可先用 CLI/python -c 调窗口管理）。

### 2.5 指纹与评测对照

- SYSTEM_PROMPT 模板改动 → `SYSTEM_PROMPT_FINGERPRINT` 变化（评测对比需新建基准，
  用户确认才跑 layer1）；模板静态指纹语义保留。
- 渲染后实际 system 随词表变化 → 增 `catalog_fingerprint`（渲染两段拼接后
  `fingerprint()`）随 config_snapshot 落库；跨 run 比通过率需同词表指纹（模板+词表双对齐）。

## 3. 实施批次（每批独立提交验证）

| 批 | 内容 | 验证 |
|---|---|---|
| 1 | `FactCategory` ORM + init_db 建表/seed + `os_mem/vocab.py`（读/渲染 + 回退常量） | 单测：seed 幂等（两次跑行数不变）、render 双语与表一致、active 过滤、表缺失回退内置 10 类 |
| 2 | extract_prompt 占位化 + build 渲染；fact_extraction 去 ALLOWED_CATEGORIES 读表 | 单测：渲染含中英双语；category 出界仍 ValueError；停用后该类别出界被拦 |
| 3 | admin 窗口 category 方法 + 单测 | 单测：upsert/启停后渲染与校验变化 |

## 4. 测试计划（无 LLM，可自由跑）

- `tests/unit/test_fact_category.py`（批1+2）：seed 幂等/渲染/校验语义/回退；
- 批3 增量进 `tests/unit/test_mem_admin_service.py`；
- 既有 unit 全量回归（122）；`test_prompt_fp` 4 指纹唯一性/确定性保持；
- **评测不跑**（改提取 prompt，验证需用户显式发起 layer1 + 确认参数）。

## 5. 风险与开放问题

1. **词表读失败的兜底**：`vocab` 查询异常 → 回退内置 10 类常量（同现状），保证校验/
   渲染永不因词表故障全拒（宁可回退旧行为，不引入新故障面）。
2. **停用 category 的存量影响**：停用 = 新提取被拦 + prompt 不再推荐；已入库旧行不受影响
   （软删除语义，与 §11 词表生命周期注记一致）。
3. **指纹/缓存**：模板改动后与历史 run 对比需新基准（用户确认）；渲染无缓存。
4. **key 期衔接**：本期模板在规则 3 前插入 `{categories_section}`；key 期到来时同法加
   `{keys_section}`，两段渲染并列，结构无需返工。
5. category 双语词条（name_zh/name_en/sort）首版由实现拟定，交付时列清单请用户核对微调。

## 6. 决策记录（2026-09-08 用户拍板）

| 项 | 结论 |
|---|---|
| 范围 | **本期仅 category**；key 词表经评估 2026-09-09 定为 **layer1 阶段不立项**（见 §1 下一期段），layer2/3 基线后再定 |
| 中英文语义 | key/category 保持英文规范 id；词条 name_zh/name_en 双语（prompt 双语提示） |
| 表结构 | category+key 两张表方向保留；**本期只建 fact_category** |
| 校验强度 | 未收录 key（未来）仅 warning；category 维持强校验（出界 ValueError）不变 |
| seed（category） | 10 类枚举全部收录 active |
| seed（key，未来） | prompt 枚举直接收录；复用≥2 次的进观察列表（active=0 观察态） |
| 自拟出口（未来 key 期） | 保留自拟 + warning 反哺词表 |
