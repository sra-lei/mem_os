# MemOS 评测框架（v0.1 基础版）

> 按需求文档演进，不一步到位。当前是 **v0.1 基础回忆** 的评测骨架：流程可跑通、数据可落库、接口已定义，
> 但记忆的**生成与读取**留给你自己实现（`os_mem` 包只提供接口契约 + 占位 stub）。

## 一、架构与依赖方向

```
src/
├── os_mem/                  ← 记忆系统核心（你的地盘，导入用 os_mem. 前缀）
│  memory.py    Memory 数据模型（需求文档 1.2）
│  provider.py  MemoryProvider 协议（ingest/retrieve）+ register_provider 注册表
│  prompt.py    format_injection 注入格式（1.5）
│  storage.py   存储隔离：生产 os_mem.db / 评测临时库
│  stub.py      占位实现（无记忆，跑通评测用）
│  sanitizer.py 日志脱敏（1.1）        ← 你实现
│  generator.py 事实提取（1.3）        ← 你实现
│  retriever.py BM25 检索（1.4）       ← 你实现
└── testing/                   ← 评测域（导入用 testing. 前缀）
   ├── runner.py / judge.py / llm.py / provider.py   评测框架
   ├── api/                    评测 dashboard API（testing.api）
   └── db/                     评测数据存储 memos.db（testing.db）
memos.db                     ← 评测数据（test_runs / test_case_results / test_case_definitions）
os_mem.db                    ← 未来记忆系统的独立库（与 memos.db 完全解耦）
```

**导入方式**（无 `src.` 前缀）：
```python
import os_mem                      # 记忆系统
from testing.db import get_session # 评测数据
from testing.runner import run_test_suite
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

**隔离**：每用例独立 provider 实例（`user_id = case_id`）；评测记忆存临时库（`--memory-store tmp`），
不碰 `memos.db` 和 `os_mem.db`。

## 三、运行

```bash
# 先确保测试集已入库（60 条 YAML → memos.db）
.venv\Scripts\python.exe -m scripts.load_test_cases

# 跑一次 mock 评测（v0.1 base 20 条）
.venv\Scripts\python.exe -m scripts.run_eval --phase base --notes "v0.1 框架验证"

# 冒烟测试（只跑前 3 条）
.venv\Scripts\python.exe -m scripts.run_eval --phase base --limit 3

# 查看结果
.venv\Scripts\python.exe -m uvicorn testing.api.main:app --port 8000   # 打开 http://localhost:8000
```

**注意**：当前默认全 mock（`--memory-provider stub` / `--llm mock` / `--judge mock`），
检索不到记忆、判分固定 0.5 → 通过率必然为 0。这是**预期基线**，不是 bug。

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

# 3. 跑真实评测
python -m scripts.run_eval --phase base --memory-provider mine
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
