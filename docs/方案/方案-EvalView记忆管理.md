# 方案：EvalView 记忆管理能力（已实施）

日期：2026-09-08 · 状态：**已实施 —— 批1（后端服务+API+14 单测）与批2（前端页面+联调冒烟）完成，
2026-09-08 冒烟全绿（含真实 Milvus 投影同步 create/update/rebuild/delete 均 synced、SPA 直链 fallback 修复）**；
批3（case 页↔记忆页互链 / 需求文档修正）未做，需要时另起。
关联：EvalView（`src/testing/api` + `frontend/`）
上游原则：`docs/方案/方案-记忆更新收敛与Milvus投影一致性.md`（SQLite 权威源 / Milvus 投影、A 批收敛）、
双库格局（`memos.db` 评测记录 / `memories.db` 业务权威，相互独立）

## 1. 目标（本次改动范围）

在 EvalView 中新增**记忆管理**能力：查看某「用户」（当前数据即评测 case，`user_id = test_id`）已沉淀的
记忆数据，并对其做管理操作——修正/删除提取错误的事实、手动补充缺失事实、清空后允许重新提取。
做完改动后重跑评测，即可验证检索注入与回答的改善（管理入口闭环到评测验证）。

| 现状 | 缺什么 | 本次加什么 |
|---|---|---|
| EvalView 只读 `memos.db`（评测记录），看不到记忆本体 | 记忆在 `memories.db`（struct_memories 1130 行 / 20 用户 / 每用户 33~95 条），只能 sqlite3 CLI 或脚本查改 | 记忆浏览/检索 + 增删改管理界面 |
| 修正一条错误记忆需手工写 SQL（还容易漏投影） | 无可视化、无投影同步 | 编辑/删除/新增自动同步向量投影（尽力），失败可一键重建 |
| 清空记忆想重跑 = 还要记得清 conv_meta 门禁 | 门禁（COMPLETED 跳过 ingest）与事实耦合 | 「清空并允许重新提取」一站式操作 |

**非目标**：不做后台 reconcile 守护进程（按需用户级 rebuild 已够，见 §11）；不做重提取的自动调度
（重提取 = 跑 ingest/评测，需 LLM 费用，由用户显式发起）；不动检索策略/评测语义；不做生产级鉴权
（本地开发工具，维持现状）。

## 2. 现状盘点（设计的事实基础）

### 2.1 数据与代码位置

- 评测记录库：`src/testing/data/memos.db`（test_runs / test_case_results / test_case_definitions）——
  EvalView 现有 runs/cases/stats 路由只读它。
- 业务记忆库：`src/os_mem/data/memories.db`（conv_messages 原文 / conv_meta 会话+状态机 / struct_memories 事实）。
  权威源锚定在 `os_mem.infra.storage.mem_storage.MemoryDatabase`（相对路径恒落 `src/os_mem/` 下，不随 cwd 漂移）。
- 评测 case 的 `test_id`（如 `layer1_01_bank_account`）与 struct_memories / conv_meta 的 `user_id` **同名同值**
  → 用例页 ↔ 记忆页可互相链接（本机 memos.db case 定义表当前为空，不影响记忆页本身，链接在定义存在时才有意义）。
- EvalView 前端 = React + TS（Vite，`frontend/src`），现有 UI 组件：Button / Badge / Toast / RateBar / Skeleton；
  API 客户端按域分文件（`api/{runs,cases,stats}.ts`）；导航在 `components/Layout/AppLayout.tsx` NAV 数组。

### 2.2 必须遵守的约束与陷阱

1. **分层方向**：`os_mem` 不得 import `testing/eval`；`testing`（管理侧）可反向 import `os_mem`。
2. **禁止顶层 import `os_mem.core.services.struc_mem_service`**：该模块 import 即构造
   `get_llm_client() / get_vectorizer() / get_memory_vector_store()`（= 建 Milvus 客户端、DashScope embedder）。
   EvalView 进程若顶层引它会启动即连云端。**投影/向量相关对象一律路由内 lazy 构造 + try/except**
   （离线时浏览/编辑 SQLite 照常，Milvus 失败只警示）。
3. **双库不互建表**：SQLModel.metadata 全局，任何 create_all 必须显式 `tables=[...]`。
   记忆路由**只读现成表、不 init_db 业务库**（表由管线保证存在）。
4. **向量投影行 id = 随机 uuid，与 SQLite struct_memories.id 无关**（`add_structured_memories` 内部新生成）。
   投影删改只能按 `(user_id, category, key)` 过滤（`vec_storage.delete_memories` 已实现），不能按事实 id。
5. **投影不变量**（A 批后）：Milvus 恒为每 `(user, key)` 一条最新值。手工管理操作不得破坏它——
   故**编辑不允许改 (category, key) 身份字段**（改身份 = 先删旧事实、再新增新键，两步显式完成）。
6. 投影行 `updated_at` 是 **naive UTC ISO 字符串**（`datetime.utcnow().isoformat()`）；SQLite 时间戳同为 naive UTC。
   手工写入投影必须沿用同一格式，前端显示经 `schemas._utc_iso` 标注 UTC。
7. conv_meta 状态机：COMPLETED = 该会话已提取入库，重跑评测**跳过 ingest**（记忆缓存门禁）。
   清空记忆后要能重新提取，必须**同时把该用户 conv_meta 行重置为 PENDING**（claim 对 PENDING 可 CAS 接管；
   原文 conv_messages 保留，重提取从原文再生）。verbatim 兜底句 key=内容指纹，重提取幂等不重复。

## 3. 设计原则

1. **SQLite 是权威源，先写后投影**：一切管理写操作先提交 memories.db（确定性成功），再尽力同步 Milvus 投影。
2. **投影尽力同步 + 可重建**：单条写操作后按 `(user, category, key)` 删旧向量 → embed → 插新；
   同步失败**不阻断也不回滚** SQLite，接口返回明确警示，UI Toast 提示「可点『重建投影』修复」；
   每用户提供「重建投影」按钮（读 SQLite 全量 → 删该用户全部向量 → 批量 embed → 重插），
   兼作任何历史漂移的兜底。
3. **身份字段不可经编辑修改**：(user_id, category, key) 构成 upsert 冲突键与投影唯一性，编辑只允许改
   fact / value / confidence。
4. **危险操作显式语义 + 二次确认**：删除、清空（尤其「清空并允许重提取」涉及后续 LLM 费用）UI 必确认。

## 4. 管理操作语义（后端服务层）

新服务模块 `src/testing/services/mem_admin_service.py`（**依赖注入**：engine + 可选 vector_store/vectorizer，
纯逻辑可离线单测；不 import struc_mem_service）。

| 操作 | SQLite（权威，先做） | 投影（尽力，后做） | 备注 |
|---|---|---|---|
| 新增事实 | INSERT（新 uuid） | delete(user,cat,[key]) 幂等 → embed → add | key/category 允许与既有键共存（同键即覆盖旧值语义） |
| 编辑事实 | UPDATE fact/value/confidence；`previous_fact`=旧 fact；updated_at=now；created_at 不动 | delete(user,cat,[key]) → embed 新 fact → add | 身份字段不可改（§3-3） |
| 删除单条 | DELETE 行 | delete(user,cat,[key]) | 幂等（投影无该键=0 删除，无害） |
| 清空（仅事实） | DELETE 该 user 全部 struct_memories | delete(user) 全量 | 保留 conv_messages 原文与 conv_meta 状态 |
| 清空并允许重提取 | 同上 + conv_meta 该 user 行 status→PENDING | delete(user) 全量 | 下次 ingest/评测从 conv_messages 原文重新抽取（LLM 费用，UI 警示） |
| 重建投影 | 无 | delete(user) → embed_batch 全量 → add | 每用户手动入口；修复一切投影漂移 |

写接口统一返回：`{ ok, operation, sqlite, projection: "synced"|"failed"|"noop", warning? }`，前端据此 Toast。

## 5. API 设计（新增 `src/testing/api/routes/memories.py`，prefix `/api/memories`）

- `GET /users` → 用户列表（user_id / fact_count / 类别分布 / message_count / conv_meta 状态聚合 / last_updated）
- `GET /users/{user_id}/facts?category=&q=&offset=&limit=` → 事实分页（q 匹配 fact/key/value 子串）
- `GET /users/{user_id}/facts/{fact_id}` → 单条详情（含 previous_fact / source 引用）
- `GET /users/{user_id}/conversations/{conversation_id}/messages` → **只读原文**（conv_messages 按 seq）
- `POST /users/{user_id}/facts` · `PATCH /users/{user_id}/facts/{fact_id}` · `DELETE /users/{user_id}/facts/{fact_id}`
- `POST /users/{user_id}/clear`  body `{reset_conv_meta: bool}`
- `POST /users/{user_id}/rebuild-projection`

工程要点：
- 内存库会话：复用 `MemoryDatabase.get_engine()`（锚定单一来源），`Session(engine)`；
  **不调用** `MemoryDatabase.init_db()`（防止 API 启动对业务库做迁移/建表副作用）。
- lazy 依赖：路由内函数级 import `vec_storage.get_memory_vector_store / vectorizer.get_vectorizer`，
  try/except 捕获构造与调用异常 → projection=failed + warning 文本回给前端。
- 时间戳：SQLite naive UTC → schema 复用 `schemas.UtcDateTime`（`_utc_iso` 输出带 +00:00）。
- `main.py` include `memories_router`；schemas 增补 MemoryUser / MemoryFact / MemoryMessages / 写响应信封。

## 6. 前端设计（React，Vite）

- AppLayout NAV 新增「记忆管理」（记忆块图标），路由：
  `/memories`（用户列表页）、`/memories/:userId`（事实管理页）。
- 用户列表页：卡片/表格列 user_id、事实数、类别小 Badge、消息数、最近更新；顶部搜索。
- 事实管理页：
  - 筛选：category dropdown（取自 distinct）+ 搜索框（fact/key/value 子串）；
  - 表格列：fact（主）| category | key | value | confidence（RateBar）| 更新时间 | 操作（编辑/删除/原文）；
  - 行内/展开显示 previous_fact（次要色）与来源会话 id；
  - 「新增记忆」Modal：当前 user 固定，填 category / key / fact / value / confidence；key 输入框给该类别
    既有 key 提示（防同义新键——呼应 key 规范化方向）；
  - 「编辑」Modal：只允许 fact / value / confidence，界面注明身份字段不可改；
  - 「原文」抽屉：只读 conv_messages（seq + content 逐条），作为「这条记忆该不该改/删」的判断依据；
  - 用户页 Danger Zone：「重建投影」「清空记忆」「清空并允许重新提取」——后两者二次确认，
    重提取项额外警示 LLM 费用与「下次 ingest/评测时生效」。
- 反馈：统一 Toast（成功）；投影同步失败黄色警示 + 指引重建；写操作后自动刷新列表。
- 可选互链：Case 详情/历史页加「查看该用户记忆」入口（user_id=case_id 时）；记忆页反向显示 case 关联（memos.db 有定义时）。

## 7. 测试计划（无 LLM，可自由跑）

`tests/unit/test_mem_admin_service.py`（tmp sqlite 建业务表 + fake vector_store/vectorizer 注入）：
- 编辑：previous_fact 归档 / updated_at 刷新 / 投影 delete(user,cat,[key]) + embed 1 次 + add 1 次，参数正确；
- 新增 / 删除单条：SQLite 行与投影调用对应；删除不存在的键幂等；
- clear：仅事实 vs + conv_meta→PENDING（断言 PENDING、原文行保留）；
- rebuild：delete(user) → embed_batch(全量) → add，顺序与数量正确；
- 投影失败分支：fake raise → 返回 ok + projection=failed + warning，SQLite 已提交不回滚。

FastAPI 层薄（组装参数 → 调服务），以单测覆盖服务为主；真实 uvicorn 冒烟在联调批做（curl 本机 8765，
只读/写测试用户数据，不碰真实 case 记忆，或先在临时 MEMORY_DB_PATH 上起一个验证进程）。
**评测一律不跑**：本改动不动检索/提取语义；若改完记忆想验证注入修复，由用户显式发起 layer1 重跑。

## 8. 实施批次（每批可独立提交验证）

| 批 | 内容 | 验证 |
|---|---|---|
| 1 | `mem_admin_service` + `routes/memories.py` + schemas + 单测 | ✅ `.venv/bin/pytest tests/unit/test_mem_admin_service.py`（14 passed；全量 unit 122 passed） |
| 2 | 前端：NAV + 用户列表页 + 事实管理页（CRUD/原文/危险操作）+ api client | ✅ `npm run build` 0 error；uvicorn 冒烟：读接口 200 / 写链路 create·update·rebuild·delete 均 projection=synced（真实 Milvus）/ 残留 0 / SPA 直链 fallback 200 |
| 3（可选） | case 页 ↔ 记忆页互链；conv_meta 状态展示（**已在用户页概览条实现**）；顺手修正过时的 docs/需求/EvalView需求文档.md（单页 HTML/同库说法） | 未做（conv_meta 状态展示除外） |

### 8.1 分层重构：os_mem 对外管理窗口（2026-09-08，追加）

**问题**：初版 mem_admin_service 放在 `src/testing/services/`，直接 import os_mem ORM、
复用 MemoryDatabase engine 并对 struct_memories 表做 SELECT/UPDATE —— 管理面对记忆库
的权限过宽（越权绕过领域层，schema/约束一变就漏）。

**改动**：管理能力收进 **`src/os_mem/admin/`（MemAdminService + get_mem_admin_service）**
作为 os_mem 对外唯一管理窗口；`testing/api/routes/memories.py` 瘦身为纯 HTTP 适配
（参数校验 → 调窗口 → 组响应），testing 侧直连实现（mem_admin_service /
mem_projection.py）删除。

| 边界 | 说明 |
|---|---|
| 对外契约 | 窗口返回 **纯 dict**（不泄露 ORM/engine）；异常只抛 LookupError（外部转 404） |
| 投影封装 | 尽力同步 + 失败警示 + 按用户重建兜底全部在窗口内部；`MemAdminService(projection=fake)` 注入测试，`allow_live=False` 离线纯 SQLite |
| import 无副作用 | 窗口放 `os_mem.admin` 顶层包（**不放 core/services**：`os_mem.core/__init__` 会级联 import struct_provider，模块 import 即构造 LLM/Milvus client，破坏 EvalView 启动轻量） |
| 会话细节 | `expire_on_commit=False`：commit 后属性保留，写方法在会话外读行字段做投影同步不抛 DetachedInstanceError |

验证：`tests/unit/test_mem_admin_service.py` 重写为窗口视角 14 passed；全量 unit 122 passed；
uvicorn 冒烟 create/update/delete 投影均 synced、零残留。

## 9. 风险与开放问题

1. **Milvus 可达性/凭证**：EvalView 进程跑在哪台机器，就用那台机器的 `.env`（MILVUS_URI/KEY、DASHSCOPE）。
   失败已设计为警示 + 重建兜底，不阻塞 SQLite 管理；但「改完立刻重跑评测」前需投影一致（重建按钮兜底）。
2. **SQLite 并发写**：若评测 ingest 与记忆管理同时写 memories.db，SQLite 锁可能让一边短暂等待/重试；
   单执行者开发场景概率低，遇错提示重试即可（不为此上 WAL，避免超范围改动）。
3. **原文含合成 PII**：conv_messages 展示用 content；本地工具可接受（数据为评测合成人物）。
4. **重提取成本**：UI 警示前置；verbatim 指纹幂等，重复提取不堆积。
5. **本机 memos.db case 定义表为空**（test_case_definitions 0 行）→ 用例页当前没数据；不影响记忆页，
   互链功能在定义存在（开发机）时才生效。
6. open：事实编辑后 confidence 由人给（0-1 默认保留原值）；是否要审计「管理操作日志」（v1 不做，靠 git/回忆）。

## 10. 机制术语 + 设计考量学习笔记

- **权威源（source of truth）vs 投影（projection）**：一份数据可写多份，但只有一份是「可重建全量真源」，
  其余是可随时从真源再生的派生副本。删/改派生副本永不丢信息 → 管理工具可以激进删投影、出错重建即可。
- **尽力同步（best-effort）**：主操作成功即算成功，附属操作失败仅记录并暴露，不回滚主操作。
  与「两阶段提交」对立——牺牲强一致换可用性；代价是窗口期不一致，用「重建」按钮收口。
- **幂等删除**：`delete(filter)` 删不存在的键返回 0 不影响正确性；重试/重跑安全。写工具偏爱幂等原语。
- **身份字段（identity）**：决定「同一条记录」的字段集合，改动它 = 删旧建新而非更新；工具 UI 上固化
  「可编辑字段」与「身份字段」的边界可防一类数据损坏。
- **状态门禁（gate）重置**：conv_meta COMPLETED 是 ingest 的幂等门禁；清记忆必须同步开门（→PENDING），
  否则「删了白删」——管理操作要理解所在状态机的「门」，不止删行。
- **reconcile（对账/重建）**：权威源存在时，「全量重投影」是最简单的对账手段；
  代价是重算成本，故只在用户级按需触发，不做常驻守护。

## 11. 决策记录（含被否方案）

| 方案 | 结论 | 理由 |
|---|---|---|
| 直接 import struc_mem_service 复用其写路径 | **被否** | 模块级 import 副作用（构造 Milvus/LLM singleton）；`add_structured_memory` 面向整段会话 LLM 提取，不是单事实管理原语 |
| 写操作只改 SQLite + 「脏标记」，由用户记得重建 | **被否** | 「改完即忘重建」→ 重跑评测拿到旧投影，管理闭环断裂；尽力同步 + 失败警示成本低 |
| 编辑允许改 category/key | **被否** | 破坏投影 (user,key) 唯一不变量；改身份 = 删旧建新两步显式完成，语义清楚 |
| 单条写操作即时逐条同步投影 | **采用** | delete(user,cat,[key]) + embed + add，与 A 批收敛同一套原语，常数成本 |
| 每用户「重建投影」按钮（用户级 reconcile） | **采用** | 承接 A 批「reconcile 不做」的结论：仍不做守护/全局工具，但管理工具需要用户级按需修复入口 |
| 清空记忆 = 连带自动重提取 | **被否** | 重提取触发 LLM 费用与 ingest，须用户显式发起评测/跑批；工具只负责「开门」（PENDING） |
