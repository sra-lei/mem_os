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

# 3. 加载测试集（60 个 YAML 用例 → 评测库）
uv run python -m scripts.load_test_cases
```

## 运行评测

```bash
# 冒烟：只跑 2 条，带完整日志
uv run python -m scripts.run_eval --phase base --llm deepseek --judge moonshot --memory-provider base --limit 2 --verbose

# 全量：v0.1 base 20 条
uv run python -m scripts.run_eval --phase base --llm deepseek --judge moonshot --memory-provider base
```

| 参数 | 说明 | 默认 |
|---|---|---|
| `--phase` | `base` / `multi_session` / `proactive`（对应 layer1/2/3） | `base` |
| `--llm` | 答案生成模型：`deepseek` / `mock` | `mock` |
| `--judge` | 判分模型：`moonshot` / `mock` | `mock` |
| `--memory-provider` | 记忆实现：`base`（真实存储+BM25）/ `stub`（占位） | `stub` |
| `--top-k` | 检索返回记忆条数 | `3` |
| `--limit` | 只跑前 N 条用例（调试用） | 全部 |
| `--verbose` | 打印逐用例日志（会话/检索/答案/判分） | 关 |

评测结果（含每条用例的 **Token 消耗**：输入/输出）写入 `src/testing/data/memos.db`。

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
    ├── runner.py           # 评测编排：ingest → retrieve → answer → judge → record
    ├── llm.py              # LLMClient（Mock / DeepSeek）+ Completion（含 token）
    ├── judge.py            # JudgeProvider（Mock / Moonshot，JSON Schema 输出约束 + 限流节流）
    ├── services/           # 评测数据存取（store_service）
    ├── db/                 # 评测库（src/testing/data/memos.db）
    └── api/                # FastAPI 看板（runs / cases / stats）
scripts/                    # CLI：load_test_cases / run_eval
tests/test_cases/           # 60 个评测用例（layer1/2/3，YAML）
frontend/                   # Vite + React + TS 看板
```

### 评测流水线（每条用例）

```
测试用例 YAML
  └─ 1. ingest      逐会话写入记忆（user + assistant 消息 → SQLite）
  └─ 2. retrieve    用 user_question 做 BM25 检索，Top-K 记忆
  └─ 3. answer      DeepSeek 注入记忆生成答案（记录 token 输入/输出）
  └─ 4. judge       Moonshot 按 evaluation_criteria 判分（JSON Schema 约束）
  └─ 5. record      写 test_case_results（score/passed/actual/tokens/error）
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
