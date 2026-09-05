# 方案：记忆更新收敛与 Milvus 投影一致性（定稿）

日期：2026-09-05 · 状态：**设计定稿，待实施**
关联：评测 run_1615d8fb4d 失败分析（docs/调试记录-2026-09-05.md §2）；
本仓库 struct provider 双写管线（conv_meta / struct_memories / Milvus mem_os）。

## 1. 目标（本次改动范围）

1. **让"新值覆盖、旧值归档"在检索侧真正生效**：SQLite（struct_memories）已有
   (user_id, category, key) 冲突 upsert + previous_fact 归档且生效（全库 45 行归档为证），
   但评测检索只读 Milvus，而 Milvus 写入是纯 INSERT（随机 uuid）——**收敛结果到不了模型面前**。
2. **原则（本方案的根本决策）**：Milvus 是**投影/索引**，SQLite 是**权威源（全量、可重建）**；
   Milvus 侧冲突问题**优先在生成期（写入投影时）处理**，信息不会丢（SQLite 全量 + 可重建兜底），
   因此收敛可以激进；检索端**不承担**冲突消解逻辑。
3. 使 Milvus mem_os 恒为"每个 (user, 事实键) 一条最新值"的干净投影，
   消除 02（48h vs 24-48h）、20（$308.75 中间值）这类"新旧/正误版本并存注入"导致的失败。

**非目标**：不做 Milvus 多版本时间线检索（历史走 SQLite previous_fact / conv_messages 审计）；
不做 key 语义归并表/embedding 归并（见 §6 被否方案）；不动评测 judge / answer 语义。

## 2. 问题背景与证据（为什么需要这个方案）

评测 run_1615d8fb4d 失败归因发现：绝大多数 judge 抱怨缺失的事实其实**都在 struct_memories 里**，
但注入给模型的 top-15 出现了矛盾/陈旧版本：

| 现象 | 证据 |
|---|---|
| 同义事实拆成两个 key，绕过冲突键 | 02：`claims_adjuster`（"48小时内"）与 `adjuster_contact_schedule`（"24-48 小时内"）**同批 18ms 内创建**（02:14:44.230792 / .248855），SQLite 与 Milvus 双双并存 |
| 归档逻辑已生效 | 全库 45 行 previous_fact 非空（01 full_name/address、02 car_loan 等），说明 (user,key) 冲突 upsert 在跑 |
| Milvus 无收敛 | `add_structured_memories` 纯 INSERT + 随机 uuid，无按 (user,key) delete/upsert（agents.md §10 陷阱 3） |
| 检索读未收敛端 | `get_structured_memories` → `vector_store.search` 只查 Milvus；SQLite 收敛结果到不了模型 |
| verbatim 聚合 key 互踩 | `fallback_numeric_facts` 全部兜底句共享 `key='verbatim_record'` → 同 key 冲突时不同兜底句互相覆盖（归档样例可见），同段兜底句集无法完整保留 |

**根因链**：key 不稳定（LLM 自由生成 key，同义不同 key）→ 冲突键 `(user,key)` 失效 →
正误版本并存入库 → Milvus 无覆盖语义 → 检索把多版本全注入 → 模型选错。
（文档 §10.6 自述"冲突键是灵魂，选错键 = 该合的没合"。）

## 3. 术语

| 词 | 含义 |
|---|---|
| 权威源 | SQLite `struct_memories`（全量 fact + previous_fact 归档）+ `conv_messages`（原文）。可审计、可重建 |
| 投影 | Milvus `mem_os`：事实的向量化副本，仅服务检索；**可从权威源随时重建，删/覆盖不丢信息** |
| 事实键 | LLM 提取的 `(category, key)` 对；本项目 key 由 LLM 生成（不稳定，见 §5.5） |
| verbatim 句 | `fallback_numeric_facts` 从原文捞的含数字短句（key 现为聚合的 `verbatim_record`） |
| LWW | Last-Write-Wins：同键冲突时后写覆盖先写（本项目现状语义） |
| 生成期 vs 检索期 | 生成期=事实提取/入库/写投影的环节；检索期=query 命中到注入的环节。本方案把收敛全部放生成期 |

## 4. 设计（定稿）

### 4.1 数据流总览（改后）

```
LLM 提取 + 数字兜底（FactExtractor）
  └─ 生成期收敛（SQLite 侧，已有并保留）：
       LLM facts   按 (user_id, category, key) upsert；同 key 新值覆盖、旧值归档 previous_fact ✅
       verbatim    key 改为内容指纹（§4.4，B 批），同句重跑幂等、异句不互踩
  └─ 投影期收敛（Milvus 侧，A 批新增）：
       add_structured_memories 内：
         ① 本批 facts 先按 (category, key) 收敛为每键一条（confidence 高者优先，§9-3）
         ② 按 category 分组批量删旧向量（§4.2）
         ③ 再 INSERT 新向量
       ⇒ Milvus 恒为"每 (user, key) 一条最新"的干净投影
  └─ 检索期：纯检索，无任何消解逻辑（§4.3 不做防御兜底）
```

### 4.2 Milvus 投影期收敛（核心改动）

**改动点 1：`vec_storage.MemoryVectorStore` 新增删除能力**
- 新增 `delete_memories(user_id: str, category: str | None = None, key: str | None = None) -> int`
  （pymilvus `client.delete(collection, filter=...)`，按 user_id + category + key 组合 filter）。
- 保留现有 `add_structured_memories`（纯 INSERT）作为底层原语，收敛逻辑放上层 service。

**改动点 2：`struc_mem_service.add_structured_memory` 写投影前先收敛**
- 入库顺序调整：SQLite 先写（权威，已有）→ **本批 facts 先按 (category, key) 收敛为每键一条
  （confidence 高者优先，见 §9-3）** → 按 category 分组批量 `delete_memories`（key in (...) filter，
   §9-1）删旧 → 批量 embed → INSERT 新值。
- 收敛语义与 SQLite 对齐：同批同 key 多条时保留 confidence 最高的一条，
  保证"投影 = SQLite 当前态的快照"。
- 幂等效果：同一会话重跑（FAILED 重启 / 跨轮重入库）不再累积重复向量；
  同一事实新版本覆盖旧版本（旧值仅存在于 SQLite previous_fact）。

### 4.3 检索端防御兜底（不做）

**不做**。原设想"返回后按 key 取最新一条"是为兼容历史脏数据而设；开发阶段每次建基准都
清库重跑（§7），写端收敛（§4.2）保证投影无同 key 多版本，检索端保持纯净、只做检索。
若日后写端收敛被证明有漏洞，再加回这一行级兜底。

### 4.4 verbatim 句 key 唯一化

`fact_extraction.fallback_numeric_facts` 中 `key='verbatim_record'` →
改为**内容指纹**：`verbatim_<sha1(value)[:12]>`（value = 原文句）。
效果：
- 同一原文句重跑 → 同 key → 投影期删旧插新，幂等不重复；
- 不同原文句 → 不同 key → 不再因共享 verbatim_record 而互相覆盖；
- 与 LLM facts 的 (category, key) 体系共存（category 维持现 finance/other 判定）。

### 4.5 key 规范化（前置依赖，治"同义不同 key"）

`os_mem/infra/llm/llm_client.py` SYSTEM_PROMPT 增补 key 约束：
- 每个 category 给出**候选 key 枚举/命名规范**（如 finance: account_number / policy_number /
  monthly_fee / balance / claim_number / adjuster_contact_time …）；
- 强约束：**同一概念必须复用已有 key，不得为新说法发明新 key**（如"48 小时联系"与
  "24-48 小时联系"都归 `adjuster_contact_time`）；
- 输出校验：`validate_response` 对未知 key 仅 warning 不拒绝（避免提取失败），
  但 prompt 侧尽力收敛 + 投影侧 upsert 保证同 key 多版本只剩最新。

**说明**：key 规范化降低"同义不同 key"发生率（02 这类），但无法 100% 消除；
剩余偶发由"SQLite 全量权威 + 可重建"兜底，可接受（见 §6 决策记录）。

### 4.6 向量库重建工具（reconcile，不做）

**不做**。开发阶段清理基准 = 直接清 conv_meta + drop mem_os collection + 全量重跑
（mem_os 现 100% 为 layer1 评测数据，可安全 drop，`_ensure_collection` 会自动重建），
不为"避免重提取"设计 reconcile/rebuild 工具。跨机同步等场景若需要，届时再议。

## 5. 与既有机制的关系

| 机制 | 影响 |
|---|---|
| conv_meta 状态机 / claim | **无冲突**：claim 管"提取是否做过"，本方案管"投影是否最新"。开发阶段重建基准直接清 conv_meta + drop mem_os 全量重跑（§7），不引入 reconcile |
| 评测缓存（agents.md §4.3） | **改动写入侧后必须清缓存**（conv_meta + mem_os）才能验证：旧 COMPLETED 会跳过 ingest，新收敛逻辑不会执行。建新基准流程见 §7 |
| SQLite previous_fact 归档 | 保留为唯一历史来源；投影不再存历史 → Milvus 失去时间线检索（需要时查 SQLite） |
| 评测 retrieve 路径 | 检索端纯净（§4.3），不承担任何消解逻辑 |

## 5.1 运行场景与未来脏数据防线（定稿）

**运行场景假设**：提取入库是**闲时任务、单执行者顺序执行**（不实时、无多 worker 并发
ingest 同一用户）。据此：

- **无并发** → struct_memories 虽无 `UNIQUE(user_id, category, key)` DB 约束（已查证仅普通索引），
  但单写者顺序执行下应用层 select-then-upsert 无竞态窗口，**不需要补唯一约束**（不为不存在
  的并发场景增加复杂度）。
- 未来脏数据防线图景：

| 脏数据来源 | 防线 | 说明 |
|---|---|---|
| 同会话重复 ingest | conv_meta COMPLETED 跳过 | 已防 |
| 重复提取产生同 key 多版本 | A 批删旧插新（写时收敛） | A 批上线后根治 |
| 跨轮累积（旧代码纯 INSERT 残留） | drop 重建（历史问题） | 一次性，A 批上线后清理 |
| 绕过 claim 直调 add_structured_memory（测试/脚本） | A 批删旧插新仍按 key 收敛 | 兜底 |
| 并发写同 user 同 key | **场景不存在**（闲时单执行者） | 无需处理 |
| 崩溃中断（delete 后 insert 前） | conv_meta 非完成态重启重跑 | 自愈 |

- 结论：A 批实施后，本运行场景下不再产生结构性脏数据；检测/对账/定点清理工具**不引入**
  （开发期撞脏即 drop，成本可接受）。

## 6. 决策记录（含被否方案）

| 方案 | 结论 | 理由 |
|---|---|---|
| A. 检索后按 key 消解（注入前过滤） | **被否** | 每查询承担收敛成本、治标不治本；违背"冲突在生成期处理"原则；评测缓存 run 下掩盖写入侧 bug |
| B. Milvus 投影期收敛（删旧插新） | **采用（核心）** | SQLite 全量兜底 → 删旧安全；投影恒最新；跨轮重跑幂等；一处实现全局生效 |
| C. key 语义归并表 / embedding 相似度归并 | **被否（缓）** | 工程量大、误并风险；先靠 prompt 约束（§4.5）降低发生率，偶发由权威源兜底 |
| D. verbatim_record 聚合 key | **被否 → 改指纹** | 聚合 key 使同 key 冲突时兜底句互踩；指纹保幂等且不互踩 |
| E. Milvus 保留多版本 + 检索按时间权重 | **被否** | 与"投影只留最新"原则冲突；时间线需求走 SQLite |
| F. 提取失败即整轮重试（治提取稳定性） | **不在本方案** | 属提取侧另一议题（评测耗时优化），另议 |
| G. SQLite 全量对齐：每次 ingest 后读 SQLite 该 user 全部最新 facts → delete(user_id) 一次 → 全量重插投影 | **被否（缓）** | 优势是投影严格 = SQLite 快照、无需增量收敛逻辑；但每次新会话都要**全量重 embed 该用户所有历史 facts**，embed 成本随用户累积线性增长，生产不可承受（除非先做 embedding 持久化进 SQLite，属额外架构改动，另议）。删旧插新只处理本批新增/变更的 key，增量成本恒定，优先采用 |

## 7. 实施顺序（分批收敛，每批可独立验证）

| 批 | 内容 | 验证 |
|---|---|---|
| **A 批（先落地，改动最小）** | ① vec_storage 新增 delete_memories（批量删）；② add_structured_memory 写投影前收敛（confidence 优先）+ 删旧插新 | 离线单测：同 key 新旧覆盖后 Milvus 仅 1 条；重跑 ingest 不累积；→ 清缓存建新基准全量评测（§7） |
| **B 批** | ③ verbatim key 改内容指纹 | 单测：同句幂等、异句共存；评测回归（清库重建基准） |
| **C 批（按需）** | ④ 提取 prompt key 约束（§4.5） | 评测 02/20 类 case 回归 + layer2/3 抽查防过拟合 |

**建新基准流程**（开发期标准动作，写入侧改动后必做）：
1. 清 `conv_meta`（memories.db 表）；
2. **drop Milvus `mem_os` collection**（现 100% 为 layer1 评测数据，可安全 drop；
   `_ensure_collection` 首次写入自动重建）；
3. 跑全量提取入库（生成期收敛 + 删旧插新在写入时生效）；
4. `pytest tests/test_memory_eval.py -m layer1 --memory-provider struct --top-k 15 --judge moonshot --record-db`，
   对比 run_1615d8fb4d（65%）基线。

## 8. 测试计划

**离线单测（tests/unit/，无 LLM/Milvus）**
- 收敛纯函数：给定同批 facts（同 key 多版本）→ 输出每键 confidence 最高一条（可单测，不依赖 Milvus）；
- verbatim 指纹 key：同句两次生成同 key；异句不同 key；
- （可选）对 vec_storage 的 delete 用 mock client 或本地/临时 collection 验证 filter 语义与日志。

**全链路回归（真实链路）**
- struct + top_k 15 + moonshot，layer1 20 例，对比 65% 基线；
- 抽查 02/13/14/15/17/19/20 七条失败 case 的注入内容：矛盾版本是否消失、所需事实是否进 top-15；
- layer2/3 抽查，确认收敛/幂等改动不伤害多会话场景。

## 9. 风险与开放问题

1. **delete 粒度（已定）**：按 **key 集合批量删**（每 category 一次
   `category=="x" and key in ["k1","k2",...]` filter——**注意 Milvus 布尔表达式 in 用方括号
   列表，圆括号会解析失败**，曾致 168 次删旧全部失败）；不做逐 key N 次调用（避免 RTT 放大）；
   **批量删必须记录完整日志**（user_id、category、key 集合、实际 delete_count、耗时），
   出问题可逐批排查。若实测批量 filter 有误删/语法风险，回退逐 key（见 §4.2）。
2. **pymilvus delete 的 filter 语法**：需实现时验证 `category == "x" and key in ("k1","k2",...)`
   组合过滤在目标 Milvus 版本可用（对标现有 search 的 filter 用法；注意 key 值含引号等特殊
   字符时的转义，LLM 生成的 key 格式不可控——批量 filter 拼接处必须做转义）。
3. **同批收敛序的确定性（已定）**：同 key 多条时保留 **confidence 高者**（同批同刻无法用
   updated_at 区分；confidence 由提取 LLM 给出，0-1）。若 confidence 也相同，回退保留遍历序
   最后一条（实现时用稳定排序：confidence desc → 原序 last）。
4. **存量脏数据**：开发期建基准流程统一"清 conv_meta + drop mem_os + 全量重跑"（§7），
   A 批上线后旧的多版本向量随 drop 一并清除，无需 reconcile 工具。
5. **updated_at 粒度**：SQLite 与 Milvus 的时间戳粒度（秒/微秒）需一致（防御兜底已不做，
   此项降级为仅日志/审计一致性关注）。
