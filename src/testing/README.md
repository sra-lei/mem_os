# MemOS 评测框架（v0.1 基础版）

> 按需求文档演进，不一步到位。当前是 **v0.1 基础回忆** 的评测骨架：流程可跑通、数据可落库、接口已定义，
> 但记忆的**生成与读取**留给你自己实现（`os_mem` 包只提供接口契约 + 占位 stub）。

## 一、架构与依赖方向

```
src/
├── os_mem/                  ← 记忆系统核心（你的地盘，导入用 os_mem. 前缀）
│  __init__.py   对外公共 API：仅 provider 契约 + 两个能力
│  provider.py   MemoryProvider 协议（ingest/retrieve）+ register_provider / build_memory_provider
│  memory.py     Memory 数据模型（需求文档 1.2）
│  core/         核心实现：generator（提取 1.3）← 你实现 / retriever（检索 1.4）← 你实现
│                / prompt（注入 1.5）/ stub（占位，通过 build_memory_provider('stub') 获取）
│  storage/      存储隔离：生产 os_mem.db / 评测临时库
│  guide/        sanitizer 日志脱敏（1.1）← 你实现
└── testing/                   ← 评测域（导入用 testing. 前缀）
   ├── judge.py / llm.py       LLM 判分（Moonshot）/ 答案生成（DeepSeek）
   ├── services/               store_service：评测数据上报（pytest --record-db）
   ├── api/                    评测 dashboard API（testing.api）
   └── db/                     评测数据存储 memos.db（testing.db）
memos.db                     ← 评测数据（test_runs / test_case_results / test_case_definitions）
os_mem.db                    ← 未来记忆系统的独立库（与 memos.db 完全解耦）
```

**对外契约**（`os_mem` 只暴露 provider 体系）：
```python
from os_mem import Memory, MemoryProvider, build_memory_provider, register_provider
from os_mem import format_injection, temp_db_path, default_db_path   # 评测必需能力
# 内部实现（os_mem.core / os_mem.storage / os_mem.guide）不对外承诺 API
```

**导入方式**（无 `src.` 前缀）：
```python
import os_mem                      # 记忆系统
from testing.db import get_session # 评测数据
from testing.api.main import app   # FastAPI 应用
```

**导入机制**：`.venv/Lib/site-packages/memos_project.pth` 把项目根和 `src/` 加进搜索路径
（等价 editable 安装；pyproject 的 hatchling 配置已就位，之后跑 `uv sync` 即可正式安装，
.pth 可删可留）。

**规则**：`os_mem` 不 import `testing` 及任何外部包；评测数据（memos.db）与
记忆数据（os_mem.db）是两个库文件，互不接触。评测产生的记忆进**临时库**（每 run 即建即删），
生产记忆走 `os_mem.db`。

## 二、评测流水线（每个用例）

```
test_case (test_case_definitions 表)
  │
  ├─ 1. ingest    对话历史逐段送入记忆系统生成记忆   ← os_mem provider.ingest（你实现）
  ├─ 2. retrieve  用 user_question 检索记忆 Top-K    ← os_mem provider.retrieve（你实现）
  ├─ 3. answer    agent 把记忆注入上下文后生成回答     ← src/testing AnswerGenerator + LLMClient
  ├─ 4. judge     用 evaluation_criteria 判分        ← src/testing JudgeProvider
  └─ 5. record    写 test_case_results，更新进度
```

**隔离**：每用例独立 provider 实例（`user_id = case_id`）；单环节记忆测试经 pytest
`tmp_path` 隔离，不碰 `memos.db` 和生产记忆库。

## 三、运行（评测入口已收敛为 pytest，无 mock 冒烟）

```bash
# 先确保测试集已入库（60 条 YAML → memos.db，供 Dashboard 用例库；可选，评测直接读 YAML）
.venv\Scripts\python.exe tests\load_test_cases.py

# layer1 全链路：base provider + DeepSeek 回答 + assert 本地判定（默认）
.venv\Scripts\python.exe -m pytest tests/test_memory_eval.py -m layer1

# 全量 + Moonshot 判分 + 结果落库看板
.venv\Scripts\python.exe -m pytest tests/test_memory_eval.py --judge moonshot --record-db

# 查看结果
.venv\Scripts\python.exe -m uvicorn testing.api.main:app --port 8000   # 打开 http://localhost:8000
```

评测参数：`--memory-provider base|struct|full`、`--llm deepseek`、
`--judge assert|moonshot`（默认 assert）、`--top-k`、`--threshold`、`--record-db`。
需要 `.env` 配置 DEEPSEEK_API_KEY（回答生成）与 MOONSHOT_API_KEY（`--judge moonshot`）。

单环节验证走独立单测，不依赖外部服务：
```bash
.venv\Scripts\python.exe -m pytest tests/test_struct_mem_sqlite.py tests/test_struct_mem_extract.py
```

## 四、接入你自己的记忆系统（你的学习任务）

按需求文档 v0.1 实现，然后在 `os_mem/provider.py` 注册：

```python
# 1. 在 os_mem 内实现（参考需求文档 1.2~1.4）
from os_mem.memory import Memory
from os_mem.provider import register_provider

class MyMemoryProvider:
    def __init__(self, user_id: str, db_path=None):   # db_path: 评测临时库或 os_mem.db
        ...

    def ingest(self, conversation: dict) -> list[Memory]:
        # 1.3：会话结束后提取事实（LLM 提取，≤3 条/会话，sanitizer 脱敏日志）
        ...

    def retrieve(self, query: str, top_k: int = 3) -> list[Memory]:
        # 1.4：BM25 检索（k1=1.5, b=0.75）
        ...

# 2. 注册
register_provider("mine", MyMemoryProvider)

# 3. 跑真实评测（pytest 入口）
python -m pytest tests/test_memory_eval.py -m layer1 --memory-provider mine
```

v0.1 建议顺序：① `Memory` 数据模型 + `memories` 表（1.2，参考 `os_mem/storage.py` 的 DDL 注释）
→ ② BM25 检索（1.4）→ ③ LLM 提取（1.3）→ ④ 日志脱敏（1.1）。注入（1.5）已在 `os_mem/prompt.py`，
可直接复用。

## 五、演进路线（不要跳到终局）

| 阶段 | 内容 | 依赖 |
|---|---|---|
| **v0.1（当前）** | base 20 条单会话回忆；框架 + mock 跑通；记忆系统由你实现 | 你完成 ingest/retrieve |
| v0.2 | multi_session 20 条跨会话检索（`--phase multi_session`） | 记忆系统支持多会话累积 + 冲突检测 |
| v0.3 | proactive 20 条主动服务（`--phase proactive`） | 记忆系统支持合成检索 + 时间加权 |
| 后续 | 真实 LLM provider（Ollama/OpenAI 兼容）、真实 Judge、judge 缓存与审计、进度 SSE、报告导出 | 见 EvalView需求文档.md 第十一章 |

## 六、评测数据模型（沿用现有表）

- `test_runs`：一次运行（version/phase/pass_rate/config_snapshot/status/progress）
- `test_case_results`：单用例结果（score/passed/actual_answer/retrieved_memories/error_message/latency_ms）
- `test_case_definitions`：用例定义（含 `conversation_histories_raw` 多会话快照、`evaluation_criteria` 判分标准）

**判分依据是 `evaluation_criteria`**（41/60 用例没有 expected_answer，判分标准全部在 criteria 里）。
