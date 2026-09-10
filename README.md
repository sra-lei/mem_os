# MemOS（Memory Operating System）

MemOS 是一个从零构建的 Agent 用户记忆系统：**跨会话记住用户信息并提供个性化服务**。
项目采用"评测驱动"的渐进式演进——`tests/test_cases/` 下有 60 个多领域评测用例（layer1/2/3 各 20：银行、保险、医疗、航空、旅行协调等），
通过真实 LLM 链路（会话原文落库 → LLM 事实提取 → SQLite + 向量双写 → 混合检索 → 策略链注入 → DeepSeek 回答 → assert/Moonshot 判分）验证每个版本。

> 需求与版本路线：v0.1 基础回忆 → v0.2 结构化与向量检索 → v0.3 高级检索与双轨编排 → v0.4 系统化自主化（详见 [docs/需求/MemOs需求文档.md](docs/需求/MemOs需求文档.md)）；
> 实现方案与工程决策见 `docs/方案/方案-*.md`（[状态机与双轨编排](docs/方案/方案-会话处理状态机与原子入库.md)、[检索注入 verbatim 区分策略](docs/方案/方案-检索注入verbatim区分策略.md)）。

**当前进度（2026-09）**：v0.1 base 已完成；**v0.2 struct 为主线上**（struct+layer1 基线 14/20，与开发机跨机复现失败集完全一致）；
v0.3 双轨 full 编排 A 批已落地（conv_meta 状态机 + 会话原文必落库），B 批（异步 worker）待实现。

## 系列文章（公众号「一文·AI账」）

项目演进过程以《MemOS》系列同步记录在公众号「一文·AI账」，用真实评测数据复盘每一轮"改了什么、为什么、代价是什么"。
合集入口：[MemOS 系列合集](https://mp.weixin.qq.com/mp/appmsgalbum?__biz=Mzg3MzE4MjMxNw==&action=getalbum&album_id=4659590571668488194)（共 6 篇，建议按序阅读）：

1. [MemOS01-纯净版](https://mp.weixin.qq.com/s?__biz=Mzg3MzE4MjMxNw==&mid=2247483697&idx=1&sn=5053f0db7219a63e3469b1ae400ea1d3&chksm=cee2a433f9952d25081e50d07a8688b8cd3c2d09a732288205bcd694ae19b66abb3cc408f29f)
2. [MemOS 02，我换了混合检索，反而比 BM25 低 10 个点](https://mp.weixin.qq.com/s?__biz=Mzg3MzE4MjMxNw==&mid=2247483711&idx=1&sn=89e9a3021a7003c69d122d13abedd172&chksm=cee2a43df9952d2b31e6d2d8de741db847b74404142db7d83d50bdd17149e0db8eb61eaafbd6)
3. [评测跑一轮要 50 分钟，让评测记住哪些已经入库，存过就跳过，变 12 分钟了](https://mp.weixin.qq.com/s?__biz=Mzg3MzE4MjMxNw==&mid=2247483721&idx=1&sn=fce09c97098ca768ef2a04f0610894bf&chksm=cee2a44bf9952d5d7022e2651d8c677ba2ab10636329ceae55346cc1a43c8466e0e0a71a00e7)
4. [MemOS 04，新旧信息傻傻分不清，因为存的是流水账不是档案卡](https://mp.weixin.qq.com/s?__biz=Mzg3MzE4MjMxNw==&mid=2247483735&idx=1&sn=3185444643a0ec873b39aac09cb01334&chksm=cee2a455f9952d434802dbe61cfe7f1e1b84b3f8cf0ca33a9e97d6f6727b652c5b7cb02e595d)
5. [MemOS 05，记忆更新终于做成，分数却没涨反降](https://mp.weixin.qq.com/s?__biz=Mzg3MzE4MjMxNw==&mid=2247483747&idx=1&sn=230a3397cda3135d6fa2b886193c9e05&chksm=cee2a461f9952d77b3cf68ec3a933564b7b5333824e7765475920c074367892c79224fbc643e)
6. [MemOS 06，检索不是捞得越多越好——窗开大了一倍，答全率反而掉了](https://mp.weixin.qq.com/s/KJ6qSR9n92VvBYa_SBpHdw)

## 特性

- **记忆实现三 provider（`src/os_mem/core/mem_provider/`）**
  - `base`：会话原文全文 + BM25 检索（v0.1 基础回忆）
  - `struct`：LLM 事实提取 → SQLite `struct_memories` 先写（**权威源**）→ Milvus/Zilliz 混合向量（dense + sparse BM25 → RRF）后写
  - `full`：双轨编排（同步快通道 <1s 返回 + struct 异步 worker，B 批待实现）
- **会话原文必落库**：`conv_messages` 逐条持久化（冲突键 user+session+seq、旧值归档）+ `conv_meta` 处理状态机（CAS 认领 / 租约 / 崩溃重试，零新依赖——无 Redis/Celery）
- **检索注入策略链**（`core/retrieval_strategies.py`）：固定链 v2（verbatim 区分准入——结构化优先，仅放行携带窗口未覆盖数值的 verbatim 兜底句），无开关、无条件生效
- **评测框架**：pytest 全链路 60 YAML（`tests/eval/` 运行库）+ 100+ 项离线单测（`tests/unit/`，无需任何 key）+ 离线三层归因审计工具
- **可复现性**：`--record-db` 时每次 run 落 `config_snapshot`（4 处 prompt 内容指纹），跑分 ↔ prompt 版本一一挂钩，改 prompt 无需手维护版本号
- **评测看板（EvalView）**：FastAPI + React（Vite），运行记录 / 通过率 / 失败对比 / Token 统计
- **库隔离**：评测库 `memos.db` 与记忆库 `memories.db` 完全分离、互不串建；两库均移出版本控制（`*.db` 已 gitignore）

## 快速开始

环境要求：Python ≥ 3.12、[uv](https://docs.astral.sh/uv/)

```bash
# 1. 安装依赖（editable 安装 os_mem / testing）
uv sync

# 2. 配置 API Key：cp .env.example .env 并填入（默认 judge 为 assert，仅 struct 提取+回答需 DEEPSEEK_API_KEY）
cp .env.example .env

# 3.（可选）加载测试集到评测库，供看板用例库；评测本身直接读 YAML
uv run python tests/load_test_cases.py
```

## 运行评测（全链路 pytest）

评测用例直接来自 `tests/test_cases/**/*.yaml`，写入 → 检索 → 回答全为真实链路；判分默认本地确定性规则（assert），可选 Moonshot LLM 判分。

```bash
# 0) 离线单测（无需任何 key / 外部服务；模块级单测均在 tests/unit/）
uv run pytest tests/unit/

# 1) layer1 全链路：base provider + DeepSeek 回答 + assert 本地判定（默认 judge）
uv run pytest tests/test_memory_eval.py -m layer1 --memory-provider base

# 2) 当前主线 struct：LLM 事实提取 + 向量双写；结果落库供看板（历史参数对齐用 --top-k 15）
uv run pytest tests/test_memory_eval.py -m layer1 --memory-provider struct --top-k 15 --record-db

# 3) Moonshot LLM 判分（需 MOONSHOT_API_KEY；有 20s 请求节流，全量较慢）
uv run pytest tests/test_memory_eval.py -m layer3 --judge moonshot --record-db

# 4) 只跑单条（调试）
uv run pytest tests/test_memory_eval.py -k layer1_11_mortgage_application
```

| 参数 | 说明 | 默认 |
|---|---|---|
| `--memory-provider` | 记忆实现：`base`（全文+BM25）/ `struct`（LLM 提取+SQLite+向量双写）/ `full`（双轨编排） | `base` |
| `--llm` | 答案生成：`deepseek` | `deepseek` |
| `--judge` | 判分：`assert`（本地：期望信息点抽取 + 归一化命中判定）/ `moonshot`（LLM-as-Judge） | `assert` |
| `--top-k` | 检索注入记忆条数上限 | `5` |
| `--threshold` | assert 判定通过阈值 | `0.7` |
| `--record-db` | 评测结果写回 `memos.db`（看板可见；run 元信息含 config_snapshot） | 关 |

> 复跑对齐参数前先查 `test_runs.config_snapshot`：历史 struct run 均用 `--top-k 15`；`test_runs.total_cases` 是收集期总数（`-m layer1` 仍计 60），实际跑数看 `test_case_results` 行数。

**手动工具**（非 pytest 用例，conftest 排除自动收集；按需执行）：

```bash
uv run python tests/load_test_cases.py             # YAML 用例 → memos.db（看板用例库）
uv run python tests/test_hybrid_retrieval.py [--case layer1_01_bank_account]  # 混合检索召回诊断
uv run python tests/test_vec_storage.py            # 向量存储链路验证
uv run python tests/audit_run_attribution.py --run run_xxx [--case <id>] [--all] [--window]
    # 离线三层归因审计（只读 memos.db）：提取漏 / 检索覆盖漏 / 回答漏，无需重跑与 LLM
```

**跨机评测记录同步**（memos.db 纯本地、不入 git，见 [docs/方案/方案-评测记录跨机同步.md](docs/方案/方案-评测记录跨机同步.md)）：

```bash
bash scripts/run_eval_record.sh -m layer1 --memory-provider struct --top-k 15
    # 跑评测的默认入口：pytest(--record-db) → 导出 run JSON → git commit → push
uv run python tests/import_run.py evals/runs/      # 把对侧 pull 下来的 run 镜像并入本地 memos.db（幂等）
uv run python tests/compare_runs.py <runA> <runB>  # 对照两 run 失败集（run id 前缀或 json 路径）
```

### 提示词指纹与版本回溯

评测 4 处人工 prompt（事实提取 system/repair、回答 system、Moonshot 判分 system）由 `src/os_mem/utils/prompt_fp.py`
对模板全文计算 **SHA-1 前 12 位内容指纹**——文本一变指纹即变；`{max_facts}` 等占位不参与（配置值变化不误判为 prompt 迭代）。
指纹随 `--record-db` 写入 `test_runs.config_snapshot`，把每次跑分与当时 prompt 精确挂钩，无需手工维护版本号（`tests/unit/test_prompt_fp.py` 锁定当前值）。

指纹是单向哈希、不存 prompt 全文 → **回溯某次跑分所用 prompt 的原文走 git**：prompt 全文都在这几个模块里、每次改动即一次提交，
`git log -p -- <prompt 模块>` 看 diff 即得上一版内容，`git show <commit>:<file>` / `git checkout <commit> -- <file>` 可取回或恢复旧版。

## 评测看板（Dashboard）

```bash
# 前端构建（产物在 frontend/dist，后端直接挂载）
cd frontend && npm install && npm run build && cd ..

# 启动 API + 看板
uv run uvicorn testing.api.main:app --host 127.0.0.1 --port 8000 --reload
# 打开 http://127.0.0.1:8000
```

看板功能：运行记录列表（通过率/耗时/Token 列）、运行详情（通过/失败 tab、失败对比、Token 消耗 card）、用例库、统计概览。

## 架构

```text
src/
├── os_mem/                     # 记忆系统核心（导入用 os_mem. 前缀）
│   ├── configs/                # mem_settings（DB 路径 / Milvus 端点 / 模型 / 提取上限…）
│   ├── core/
│   │   ├── mem_provider/       # base_provider / struct_provider / full_provider（记忆实现三档）
│   │   ├── services/           # note_mem_service（原文 upsert）/ struc_mem_service（事实入库）/ conv_meta_service（状态机）
│   │   ├── state_machine.py    # conv_meta 线性状态机（CAS 认领 + 租约 + 重试）
│   │   ├── retrieval_strategies.py   # 检索注入策略链（verbatim 区分准入，固定链 v2）
│   │   └── guide/              # sanitizer（日志脱敏）
│   ├── entries/                # SQLModel 表：conv_messages / struct_memories / conv_meta（conv_memories 已退役）
│   ├── extractor/              # 记忆提取域：fact_extractor（LLM 结构化任务执行器）/ callers（provider 无关：协议+恢复循环+分发工厂）/ deepseek_caller（DeepSeek caller + prompt 模板/渲染/指纹）/ regular_extractor（正则兜底+R1 剪枝）/ common（共享纯函数：fact_tokens 数值口径等）/ models（数据类）/ profile（默认画像）/ normalize（D4 key 归一）
│   ├── models/                 # 领域数据模型
│   ├── infra/                  # llm（base_client/deepseek_client/factory/failover）· storage（mem/vec/vectorizer）· retriever（BM25）· logger · p2check
│   └── utils/                  # prompt_fp（通用 prompt 指纹；提取域已迁至 extractor/）
└── testing/                    # 评测管理侧（导入用 testing. 前缀）
    ├── db/                     # memos.db 表模型（test_runs / test_case_results / test_case_definitions）
    ├── services/               # store_service（--record-db 落库）
    └── api/                    # FastAPI EvalView 看板（runs / cases / stats）

tests/                          # 评测域
├── eval/                       # 评测运行库：cases（用例加载）/ harness（执行编排）/ llm（回答）/ judge（assert_judger + moonshot_judger）
├── unit/                       # 离线单测（conv_meta / conv_messages / fact_extraction / struct_mem_sqlite / prompt_fp / retrieval_strategies / …）
├── test_memory_eval.py         # 全链路评测入口（60 YAML 参数化，-m layer1/2/3）
├── conftest.py                 # pytest 胶水：参数 / fixture / --record-db 上报 / collect_ignore（排除手动脚本）
├── audit_run_attribution.py    # 离线三层归因审计工具（只读）
├── load_test_cases.py          # 工具：YAML 用例 → memos.db
├── test_hybrid_retrieval.py    # 诊断：混合检索召回（uv run python 运行）
├── test_vec_storage.py         # 诊断：向量存储链路（uv run python 运行）
└── test_cases/                 # 60 个 YAML 评测用例（layer1/2/3 各 20）

frontend/                       # Vite + React + TS 看板（构建产物挂载于 FastAPI）
```

### 评测流水线（每条用例，pytest tests/test_memory_eval.py）

```text
测试用例 YAML（tests/test_cases/**/*.yaml）
  └─ 1. ingest      会话原文逐条落库 conv_messages + conv_meta 登记；
                    struct：claim → LLM 事实提取 → struct_memories 写 SQLite（权威）→ Milvus 向量后写
  └─ 2. retrieve    混合检索（dense + sparse RRF）→ 检索策略层固定链 v2 过滤排序 → Top-K 注入
  └─ 3. answer      DeepSeek 注入记忆生成答案（记录 token 输入/输出）
  └─ 4. judge       assert（默认，本地信息点命中判定）或 Moonshot 按 criteria 判分
  └─ 5. record      仅 --record-db：写 test_case_results；run 元信息 config_snapshot 含 4 处 prompt 指纹
```

### 数据库隔离

| 库 | 位置 | 表 |
|---|---|---|
| 评测库 `memos.db` | `src/testing/data/` | `test_runs` / `test_case_results` / `test_case_definitions` |
| 记忆库 `memories.db` | `src/os_mem/data/` | `conv_messages` / `struct_memories` / `conv_meta` |

建表均限定各自表集合（`create_all(tables=[...])`），互不串建；`*.db` 在 `.gitignore` 中（纯本地运行产物，评测库**不清理**）。

## 配置（.env）

```bash
cp .env.example .env   # 键值与下方一致，编辑填入 API Key
```

| 变量 | 说明 | 默认 |
|---|---|---|
| `DEEPSEEK_API_KEY` | DeepSeek API Key（事实提取 + 答案生成共用） | 无 |
| `DEEPSEEK_BASE_URL` | DeepSeek API 地址 | `https://api.deepseek.com` |
| `DEEPSEEK_MODEL` | 提取/回答模型 | `deepseek-v4-flash` |
| `DEEPSEEK_TIMEOUT` | DeepSeek 请求超时（秒） | `60` |
| `MOONSHOT_API_KEY` | Moonshot API Key（`--judge moonshot` 时） | 无 |
| `MOONSHOT_BASE_URL` | Moonshot API 地址 | `https://api.moonshot.cn/v1` |
| `MOONSHOT_MODEL` | 判分模型 | `kimi-k3` |
| `MEMORY_DB_PATH` | 记忆库相对路径（相对 `src/os_mem/`） | `data/memories.db` |

> struct provider 需要可用的 Milvus 兼容端点：默认 Zilliz serverless（`MILVUS_URI`/`MILVUS_API_KEY` 可在 mem_settings 覆盖）。
> `.env` 已被 `.gitignore` 忽略，Key 不会进版本库；`.env.example` 作为模板入库。

## 演进路线（现状）

| 版本 | 主题 | 进度 |
|---|---|---|
| v0.1 | 基础记忆（base：全文 + BM25） | ✅ 已完成（layer1 基础跑分） |
| **v0.2** | 结构化与向量检索（struct：LLM 提取 → SQLite+向量双写 → 混合检索 → 策略链准入） | 🚧 **当前主线**：layer1 struct 基线 14/20（verbatim 区分策略链 v2，跨机复现一致） |
| v0.3 | 双轨编排 / 主动服务（full = 同步快通道 + struct 异步 worker） | 🚧 A 批已落地（conv_meta 状态机 / 原文必落库）；B 批（异步 worker）待实现 |
| v0.4 | 系统化与自主化（审核机制 + 智能体 RAG + 程序记忆） | ⏳ 规划中 |

## 开发说明

- **依赖方向**：`os_mem` 不依赖 `testing`/`eval`；`eval` 只依赖 `os_mem` + 第三方；`tests` 是唯一同时碰两者的胶水（pyproject `pythonpath=["src","tests"]`）
- **judge 结构**（拆包于 `tests/eval/judge/`）：判分 token 规则在 `assert_judger.py`，与 `retrieval_strategies.py`、`audit_run_attribution.py` 三处有同步义务（口径注释内写明）
- **日志**：`os_mem` 统一 logger（stderr INFO + `logs/app-*.jsonl` DEBUG 按天轮转）；含 PII 内容入日志前必须 sanitize/mask
- **评测库纪律**：`memos.db`/`memories.db` 均不入 git；本地改动过 db 时 pull 会被拒（冲突）——备份到 /tmp → `git checkout -- <db>` → pull → 还原
- **调试开关**：`testing/services/store_service.py` 的 `_db_off` 可关闭评测结果落库（纯跑流程调试）
- **限流提示**：Moonshot 判分有 20s 请求节流，调试用 `-k` / `--limit` 收窄
- **多设备同步**：origin/main 为权威（开发机完成评测写 docs → push → 本机 pull 拉齐）；本地落后时先 `git fetch` 看差距再改，改动语义以远端为准；提交即推送
