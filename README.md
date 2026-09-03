# MemOS（Memory Operating System）

MemOS 是一个从零构建的 Agent 用户记忆系统：**跨会话记住用户信息并提供个性化服务**。
项目采用"评测驱动"的渐进式演进——`tests/test_cases/` 下有 60 个多领域评测用例（银行、保险、医疗、航空、旅行协调等），
通过真实的 LLM 链路（记忆写入 → BM25 检索 → DeepSeek 回答 → Moonshot 判分）验证记忆系统的每个版本。

> 版本路线：v0.1 基础回忆 → v0.2 多会话检索 → v0.3 主动服务 → v0.4 系统化自主化（详见 [docs/MemOs需求文档.md](docs/MemOs需求文档.md)）

## 特性

- **记忆系统核心（`src/os_mem/`）**：独立的记忆存储（SQLite）与检索（BM25），支持注入式问答
- **评测框架（`src/testing/`）**：可插拔 LLM Provider（DeepSeek 回答 / Moonshot 判分 / Mock 兜底），逐用例记录答案、判分、Token 消耗
- **评测数据看板（Dashboard）**：FastAPI + React（Vite）前端，运行记录、通过率趋势、失败用例对比、Token 统计
- **库隔离**：评测数据（`memos.db`）与记忆数据（`memories.db`）分库存储，表互不串建

## 快速开始

环境要求：Python ≥ 3.12、[uv](https://docs.astral.sh/uv/)

```bash
# 1. 安装依赖并构建项目（editable 安装 os_mem / testing）
uv sync

# 2. 配置 API Key：从模板创建 .env 并填入 Key
#    cp .env.example .env   （模板见下方"配置"章节）

# 3. 加载测试集到评测库（60 个 YAML 用例 → memos.db，供 Dashboard 用例库；评测本身直接读 YAML，可选）
uv run python tests/load_test_cases.py
```

## 运行评测（全链路 pytest）

评测用例直接来自 `tests/test_cases/**/*.yaml`；记忆写入 → 检索 → DeepSeek 回答 → 判分全为真实链路，无 mock。

```bash
# layer1 全链路：base provider + DeepSeek 回答 + assert 本地判定（默认）
uv run pytest tests/test_memory_eval.py -m layer1

# layer2 结构化记忆：struct provider，fact 粒度细用更宽 top-k
uv run pytest tests/test_memory_eval.py -m layer2 --memory-provider struct --top-k 15

# layer3 + Moonshot LLM 判分 + 结果写回看板库
uv run pytest tests/test_memory_eval.py -m layer3 --judge moonshot --record-db

# 只跑单条（调试）
uv run pytest tests/test_memory_eval.py -k bank_account
```

| 参数 | 说明 | 默认 |
|---|---|---|
| `--memory-provider` | 记忆实现：`base`（全文存储+BM25）/ `struct`（LLM 提取+SQLite 双写+向量库）/ `full` | `base` |
| `--llm` | 答案生成：`deepseek` | `deepseek` |
| `--judge` | 判分：`assert`（本地确定性数字/关键词判定）/ `moonshot`（LLM-as-Judge） | `assert` |
| `--top-k` | 检索返回记忆条数 | `5` |
| `--threshold` | 判定通过阈值 | `0.7` |
| `--record-db` | 评测结果写回 `memos.db`（EvalView Dashboard 可见） | 关 |

> 需要 `.env` 配置 `DEEPSEEK_API_KEY`（答案生成）与 `MOONSHOT_API_KEY`（`--judge moonshot` 时）。

**单环节验证**不依赖外部服务，独立 pytest 执行（无需任何 key）：

```bash
uv run pytest tests/test_struct_mem_sqlite.py    # 结构化记忆 SQLite 双写（INSERT/冲突 UPDATE/归档）
uv run pytest tests/test_struct_mem_extract.py   # 事实提取纯逻辑（校验/去重/数字兜底/分段）
```

**手动真实验证工具**（非 pytest 用例，已在 conftest 排除自动收集；需在线 Milvus / .env，按需执行）：

```bash
uv run python tests/load_test_cases.py            # YAML 用例 → memos.db（Dashboard 用例库）
uv run python tests/test_hybrid_retrieval.py      # 检索召回诊断：每条用例召回了什么
uv run python tests/test_hybrid_retrieval.py --case layer1_01_bank_account
uv run python tests/test_vec_storage.py           # 向量存储链路验证（写入 test_001~003 测试数据）
```

评测结果（`--record-db`）与用例库（`tests/load_test_cases.py`）写入 `src/testing/data/memos.db`。

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

```
src/
├── os_mem/                 # 记忆系统核心（导入用 os_mem. 前缀）
│   ├── configs/            # 配置（mem_settings：MEMORY_DB_PATH 等）
│   ├── core/
│   │   ├── models/         # 数据模型（Conversation / Memory）
│   │   ├── mem_provider/   # BaseProvider：ingest（写记忆）/ retrieve（读记忆）
│   │   └── services/       # 存储与检索服务（save_user_memories / BM25 检索）
│   ├── entries/            # SQLModel 表（conv_memories / conv_messages）
│   ├── guide/              # 实现指南骨架（sanitizer 日志脱敏）
│   └── infra/              # 基础设施：storage（SQLite）、retriever（SimpleBM25）、logger
└── testing/                # 评测域（导入用 testing. 前缀）
    ├── llm.py              # LLMClient（DeepSeek）+ Completion（含 token）
    ├── judge.py            # JudgeProvider（Moonshot，JSON Schema 输出约束 + 限流节流）
    ├── services/           # 评测数据存取（store_service，供 --record-db 上报）
    ├── db/                 # 评测库（src/testing/data/memos.db）
    └── api/                # FastAPI 看板（runs / cases / stats）
tests/                      # pytest 评测（test_memory_eval + 单环节单测）+ 手动验证脚本
│   ├── conftest.py         # 评测 harness / 命令行参数 / collect_ignore（排除手动脚本）
│   ├── test_memory_eval.py # 全链路评测（60 YAML 参数化，-m layer1/2/3）
│   ├── test_struct_mem_*.py# 单环节单测（入库 / 事实提取，离线）
│   ├── load_test_cases.py  # 工具：YAML 用例 → memos.db（uv run python 运行）
│   ├── test_hybrid_retrieval.py  # 诊断：混合检索召回（uv run python 运行）
│   ├── test_vec_storage.py       # 诊断：向量存储链路（uv run python 运行）
│   └── test_cases/          # 60 个 YAML 评测用例
frontend/                   # Vite + React + TS 看板
```

### 评测流水线（每条用例，pytest tests/test_memory_eval.py）

```
测试用例 YAML（tests/test_cases/**/*.yaml）
  └─ 1. ingest      逐会话写入记忆（user + assistant 消息 → SQLite/向量库）
  └─ 2. retrieve    用 user_question 检索，Top-K 记忆注入
  └─ 3. answer      DeepSeek 注入记忆生成答案（记录 token 输入/输出）
  └─ 4. judge       assert（默认，本地判定）或 Moonshot 按 criteria 判分
  └─ 5. record      仅 --record-db 时写 test_case_results（score/passed/actual/tokens/error）
```

### 数据库隔离

| 库 | 位置 | 表 |
|---|---|---|
| 评测库 `memos.db` | `src/testing/data/` | `test_runs` / `test_case_results` / `test_case_definitions` |
| 记忆库 `memories.db` | `src/os_mem/data/` | `conv_memories` / `conv_messages` |

两个库的建表均限定各自表集合（`create_all(tables=[...])`），互不串建；数据库文件在 `.gitignore` 中忽略。

## 配置（.env）

```bash
# 从模板创建配置（模板见 .env.example，键值与下方表格一致）
cp .env.example .env
# 然后编辑 .env 填入各 API Key
```

| 变量 | 说明 | 默认 |
|---|---|---|
| `DEEPSEEK_API_KEY` | DeepSeek API Key（答案生成） | 无 |
| `DEEPSEEK_BASE_URL` | DeepSeek API 地址 | `https://api.deepseek.com` |
| `DEEPSEEK_MODEL` | 答案生成模型 | `deepseek-v4-flash` |
| `DEEPSEEK_TIMEOUT` | DeepSeek 请求超时（秒） | `60` |
| `MOONSHOT_API_KEY` | Moonshot API Key（判分） | 无 |
| `MOONSHOT_BASE_URL` | Moonshot API 地址 | `https://api.moonshot.cn/v1` |
| `MOONSHOT_MODEL` | 判分模型 | `kimi-k3` |
| `MEMORY_DB_PATH` | 记忆库相对路径（相对 `src/os_mem/`） | `data/memories.db` |

> `.env` 已被 `.gitignore` 忽略，Key 不会进版本库；`.env.example` 作为模板入库。

## 演进路线

| 版本 | 主题 | 评测 phase | 记忆系统要求 |
|---|---|---|---|
| **v0.1（当前）** | 基础回忆 | `base`（layer1，20 条） | 全量文本存储 + BM25 检索 |
| v0.2 | 结构化与向量检索 | `multi_session`（layer2） | 多会话累积 + 冲突检测 |
| v0.3 | 高级检索与压缩 | `proactive`（layer3） | 混合检索（RRF）+ 时间加权 |
| v0.4 | 系统化与自主化 | 全量回归 | 审核机制 + 智能体 RAG |

## 开发说明

- **导入约定**：`os_mem` 不依赖评测侧；`testing` 消费 `os_mem`（依赖方向单向）
- **日志**：统一走 `os_mem` 的 `LoggerHelper`（`os_mem.infra.logger.logger`），格式 `时间 - 模块 - 级别 - 消息`
- **调试开关**：`testing/services/store_service.py` 的 `_db_off = True` 可关闭评测结果落库（纯跑流程调试）
- **限流提示**：Moonshot 账号 RPM 限制下，judge 有 20s 请求节流，全量评测较慢，调试用 `--limit`
