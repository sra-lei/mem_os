# MemOS 评测展示系统需求文档

## 一、系统概述

### 1.1 目标

为 MemOS 各版本的测试集运行提供一个**可视化展示面板**，让开发者能够：
- 查看每次测试运行的完整记录
- 对比不同版本、不同阶段的通过率变化
- 深入分析失败用例的具体原因
- 追踪记忆系统的演进趋势

### 1.2 架构原则

- **共用同一 SQLite 数据库**：评测数据与 MemOS 主数据存储在同一个 SQLite 文件中（不同表）
- **轻量级前端**：单页 HTML + 原生 JS + Chart.js 图表库，无需额外构建工具
- **开箱即用**：后端用 Python 提供 API，前端直接访问

---

## 二、数据库设计（新增表）

在现有 `memories.db` 中新增以下表：

### 2.1 `test_runs` — 测试运行主表

记录每一次完整的测试集执行

```sql
CREATE TABLE test_runs (
    id TEXT PRIMARY KEY,                -- UUID
    version TEXT NOT NULL,              -- 'v0.1' | 'v0.2' | 'v0.3' | 'v0.4'
    phase TEXT NOT NULL,                -- 'base' | 'multi_session' | 'proactive'
    run_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    total_cases INTEGER NOT NULL,
    passed_count INTEGER NOT NULL,
    pass_rate REAL NOT NULL,            -- 0.0 ~ 1.0
    duration_seconds REAL,              -- 总耗时
    config_snapshot TEXT,               -- JSON: 当前版本的配置参数
    notes TEXT,                         -- 手动备注
    triggered_by TEXT DEFAULT 'manual'  -- 'manual' | 'ci' | 'scheduled'
);
```

### 2.2 `test_case_results` — 单条用例结果表

```sql
CREATE TABLE test_case_results (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    case_id TEXT NOT NULL,              -- 用例编号: 'R-001', 'M-001', 'A-001'
    case_name TEXT NOT NULL,
    category TEXT NOT NULL,             -- 'base' | 'multi_session' | 'proactive'
    version TEXT NOT NULL,
    passed INTEGER NOT NULL,            -- 0 | 1
    score REAL,                         -- LLM-as-Judge 评分 (0-1)
    expected_answer TEXT,               -- 期望答案
    actual_answer TEXT,                 -- Agent 实际输出
    retrieved_memories TEXT,            -- JSON: 检索到的记忆列表
    error_message TEXT,                 -- 如果有错误
    latency_ms INTEGER,                 -- 该用例耗时
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (run_id) REFERENCES test_runs(id)
);
```

### 2.3 `test_case_definitions` — 用例定义表（元数据）

```sql
CREATE TABLE test_case_definitions (
    case_id TEXT PRIMARY KEY,           -- 'R-001'
    name TEXT NOT NULL,
    category TEXT NOT NULL,             -- 'base' | 'multi_session' | 'proactive'
    version_target TEXT NOT NULL,       -- 首次引入的版本
    description TEXT,
    setup_dialog TEXT,                  -- JSON: 建立阶段的对话
    query TEXT,                         -- 查询问题
    expected_answer TEXT,               -- 期望答案
    tags TEXT,                          -- JSON: ['旅行', '护照', '国际航班']
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME
);
```

---

## 三、后端 API 设计

### 3.1 技术栈
- **框架**：FastAPI（轻量、自动生成 OpenAPI 文档）
- **ORM**：SQLAlchemy（可选，或直接用 sqlite3）

### 3.2 接口列表

#### GET `/api/runs`
获取所有测试运行记录（列表）

**响应**：
```json
{
    "runs": [
        {
            "id": "run_001",
            "version": "v0.1",
            "phase": "base",
            "run_at": "2026-08-29T10:30:00",
            "total_cases": 20,
            "passed_count": 18,
            "pass_rate": 0.90,
            "duration_seconds": 45.2
        }
    ],
    "total": 12
}
```

**查询参数**：
- `version`：过滤版本
- `phase`：过滤测试阶段
- `limit`：默认 20
- `offset`：分页

#### GET `/api/runs/{run_id}`
获取单次运行的详细信息

**响应**：包含 run 信息 + 该次运行的所有用例结果列表

#### GET `/api/runs/{run_id}/chart`
获取该次运行的图表数据（用于通过率可视化）

#### GET `/api/cases`
获取所有用例定义

**查询参数**：
- `category`：过滤分类
- `version`：过滤版本

#### GET `/api/cases/{case_id}`
获取单个用例的完整定义

#### GET `/api/cases/{case_id}/history`
获取某个用例在所有运行中的历史表现

**响应**：
```json
{
    "case_id": "R-001",
    "history": [
        {"run_id": "run_001", "version": "v0.1", "passed": true, "score": 0.95, "run_at": "..."},
        {"run_id": "run_005", "version": "v0.2", "passed": true, "score": 0.98, "run_at": "..."}
    ]
}
```

#### GET `/api/stats/overview`
获取统计概览（仪表盘数据）

**响应**：
```json
{
    "total_runs": 12,
    "latest_run": {"version": "v0.4", "pass_rate": 0.92, "run_at": "..."},
    "by_version": {
        "v0.1": {"runs": 4, "avg_pass_rate": 0.82},
        "v0.2": {"runs": 3, "avg_pass_rate": 0.86}
    },
    "case_categories": {
        "base": 20,
        "multi_session": 20,
        "proactive": 20
    },
    "failing_cases": [
        {"case_id": "A-003", "name": "护照过期主动预警", "last_result": "failed"}
    ]
}
```

#### POST `/api/runs`
触发一次新的测试运行（由测试脚本调用）

**请求体**：
```json
{
    "version": "v0.2",
    "phase": "multi_session",
    "config": {"k": 5, "retrieval_mode": "hybrid"},
    "notes": "首次测试混合检索"
}
```

**响应**：`{ "run_id": "run_013", "status": "running" }`

#### GET `/api/runs/{run_id}/progress`
获取运行进度（用于长耗时测试）

**响应**：`{ "status": "running", "completed": 12, "total": 20, "percent": 60 }`

---

## 四、前端页面设计

### 4.1 技术选型
- **纯 HTML + CSS + JavaScript**（一个文件）
- **Chart.js**：图表渲染（CDN 引入）
- **Vanilla JS**：无框架依赖，无构建工具

### 4.2 页面布局

```
┌─────────────────────────────────────────────────────┐
│  MemOS 评测面板                                     │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐           │
│  │ 总运行次数 │ │ 最新通过率 │ │ 总用例数  │           │
│  │   12      │ │   92.3%   │ │   60     │           │
│  └──────────┘ └──────────┘ └──────────┘           │
├─────────────────────────────────────────────────────┤
│  📈 版本通过率趋势                                  │
│  [Chart.js 折线图]                                 │
│  v0.1 ── v0.2 ── v0.3 ── v0.4                     │
├─────────────────────────────────────────────────────┤
│  📊 各分类通过率对比                                │
│  [Chart.js 柱状图]                                 │
│  基础回忆 | 多会话检索 | 主动服务                   │
├─────────────────────────────────────────────────────┤
│  🔍 最近运行记录                                    │
│  ┌──────────────────────────────────────────────┐   │
│  │ 版本 │ 阶段 │ 日期 │ 通过率 │ 操作 │          │   │
│  │ v0.4 │ 全量 │ 08/29 │ 92.3% │ [详情] │          │   │
│  │ v0.3 │ 主动 │ 08/28 │ 75.0% │ [详情] │          │   │
│  └──────────────────────────────────────────────┘   │
├─────────────────────────────────────────────────────┤
│  ❌ 持续失败用例列表                                 │
│  ┌──────────────────────────────────────────────┐   │
│  │ 用例ID │ 名称 │ 最近结果 │ 失败次数 │          │   │
│  │ A-003  │ 护照预警 │ ❌ 失败 │ 3 │          │   │
│  │ A-007  │ 药物冲突 │ ❌ 失败 │ 2 │          │   │
│  └──────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────┘
```

### 4.3 路由视图

| 路径 | 内容 |
|------|------|
| `/` | 仪表盘（默认视图） |
| `/#runs` | 运行记录列表 |
| `/#run/{run_id}` | 单次运行详情 |
| `/#cases` | 用例管理 |
| `/#case/{case_id}` | 用例历史趋势 |

### 4.4 单次运行详情页

```
┌─────────────────────────────────────────────────────┐
│  运行详情: run_013                                  │
│  版本: v0.4  |  阶段: 全量  |  日期: 2026-08-29   │
│  通过率: 92.3% (55/60)  |  耗时: 12分34秒         │
├─────────────────────────────────────────────────────┤
│  📊 通过/失败分布                                   │
│  [饼图: 55 通过, 5 失败]                           │
├─────────────────────────────────────────────────────┤
│  ❌ 失败用例明细                                    │
│  ┌──────────────────────────────────────────────┐   │
│  │ 用例: A-003 护照过期主动预警                  │   │
│  │ 期望: "护照将在旅行后一个月过期，建议检查..." │   │
│  │ 实际: "已为你预订机票"                       │   │
│  │ 检索到的记忆: [...]                          │   │
│  │ [查看完整对比]                              │   │
│  └──────────────────────────────────────────────┘   │
│  ...                                              │
├─────────────────────────────────────────────────────┤
│  ✅ 通过用例列表                                     │
│  [折叠面板，可展开查看详情]                         │
└─────────────────────────────────────────────────────┘
```

---

## 五、核心前端功能模块

### 5.1 数据加载函数

```javascript
// 初始化加载概览
async function loadDashboard() {
    const stats = await fetch('/api/stats/overview').then(r => r.json());
    const runs = await fetch('/api/runs?limit=10').then(r => r.json());
    renderStats(stats);
    renderTrendChart(runs);
    renderRecentRuns(runs);
    renderFailingCases(stats.failing_cases);
}

// 加载运行详情
async function loadRunDetail(runId) {
    const run = await fetch(`/api/runs/${runId}`).then(r => r.json());
    renderRunSummary(run);
    renderPassFailChart(run);
    renderFailedCases(run.results.filter(r => !r.passed));
    renderPassedCases(run.results.filter(r => r.passed));
}
```

### 5.2 图表渲染

**趋势图**（Chart.js 折线图）：
```javascript
function renderTrendChart(runs) {
    const ctx = document.getElementById('trendChart').getContext('2d');
    new Chart(ctx, {
        type: 'line',
        data: {
            labels: runs.map(r => r.run_at.slice(0, 10)),
            datasets: [
                {
                    label: '通过率',
                    data: runs.map(r => r.pass_rate * 100),
                    borderColor: '#4a6cf7',
                    fill: false,
                    tension: 0.1
                }
            ]
        },
        options: {
            scales: {
                y: { min: 0, max: 100, ticks: { callback: v => v + '%' } }
            }
        }
    });
}
```

**通过/失败饼图**：
```javascript
function renderPassFailChart(run) {
    new Chart(ctx, {
        type: 'doughnut',
        data: {
            labels: ['通过', '失败'],
            datasets: [{
                data: [run.passed_count, run.total_cases - run.passed_count],
                backgroundColor: ['#28a745', '#dc3545']
            }]
        }
    });
}
```

### 5.3 失败用例对比模态框

```html
<!-- 点击失败用例后弹出 -->
<div class="modal" id="caseCompareModal">
    <div class="modal-content">
        <h3 id="compareCaseName">A-003: 护照过期主动预警</h3>
        <div class="compare-grid">
            <div class="expected">
                <h4>期望答案</h4>
                <p id="expectedAnswer">你的护照将在旅行后一个月过期...</p>
            </div>
            <div class="actual">
                <h4>实际输出</h4>
                <p id="actualAnswer">已为你预订机票</p>
            </div>
        </div>
        <div class="retrieved-memories">
            <h4>检索到的记忆</h4>
            <pre id="retrievedList">[...]</pre>
        </div>
        <button onclick="closeModal()">关闭</button>
    </div>
</div>
```

---

## 六、测试运行脚本（集成）

### 6.1 测试运行器接口

在 MemOS 主代码中，测试运行器需要：

```python
# test_runner.py
def run_test_suite(version: str, phase: str, config: dict) -> str:
    """
    运行指定版本和阶段的测试集
    返回: run_id
    """
    run_id = uuid.uuid4().hex
    test_cases = load_cases(version, phase)
    results = []
    
    for case in test_cases:
        # 1. 重置 Agent 上下文（建立阶段）
        agent.reset()
        for turn in case.setup_dialog:
            agent.chat(turn.user, turn.assistant_expected)
        
        # 2. 执行查询
        start_time = time.time()
        actual = agent.chat(case.query)
        latency = int((time.time() - start_time) * 1000)
        
        # 3. 获取检索到的记忆（从 Agent 内部状态）
        retrieved = agent.get_last_retrieved_memories()
        
        # 4. 评估（LLM-as-Judge）
        score = evaluate_answer(actual, case.expected_answer)
        passed = score >= 0.7
        
        # 5. 记录结果
        result = {
            "case_id": case.case_id,
            "case_name": case.name,
            "category": case.category,
            "passed": passed,
            "score": score,
            "expected_answer": case.expected_answer,
            "actual_answer": actual,
            "retrieved_memories": retrieved,
            "latency_ms": latency
        }
        results.append(result)
        
        # 6. 更新进度
        update_progress(run_id, len(results), len(test_cases))
    
    # 7. 汇总并入库
    save_run_results(run_id, version, phase, config, results)
    
    return run_id
```

### 6.2 进度上报

```python
# 使用 sqlite 记录进度
def update_progress(run_id: str, completed: int, total: int):
    conn = get_db()
    conn.execute(
        "UPDATE test_runs SET progress = ? WHERE id = ?",
        (completed / total, run_id)
    )
    conn.commit()
```

---

## 七、页面设计规范

### 7.1 颜色方案
- **主色**：`#4a6cf7`（蓝色）
- **成功/通过**：`#28a745`（绿色）
- **失败**：`#dc3545`（红色）
- **背景**：`#f8f9fa`（浅灰）
- **卡片**：白色 + 阴影

### 7.2 响应式布局
- 宽屏：多列网格
- 移动端：堆叠布局，缩小图表

### 7.3 交互反馈
- 数据加载：显示骨架屏或加载动画
- 图表切换：点击图例可显示/隐藏数据系列
- 表格排序：点击表头按列排序

---

## 八、实现优先级

### Phase 1：基础数据 + API（先做）
1. 创建评测数据库表
2. 实现核心 API（/runs, /runs/{id}, /stats/overview）
3. 测试脚本写入数据库

### Phase 2：前端仪表盘（能做）
4. 单页 HTML + CSS 布局
5. Chart.js 趋势图和分类对比图
6. 最近运行记录列表

### Phase 3：运行详情（做得好）
7. 单次运行详情页
8. 失败用例对比模态框
9. 用例历史趋势

### Phase 4：迭代优化（做得美，预留详细设计见第十一章）
10. 实时进度轮询（SSE + Polling 双模式，运行过程实时可见）
11. 真实 LLM-as-Judge 评测接入（可插拔 Provider、调用记录、缓存、重评测）
12. 用例定义 CRUD 表单（新增/编辑/删除、批量导入 YAML、编辑审计、版本对比 diff）
13. 导出报告（CSV 明细 + PDF 正式报告，支持单次运行 & 仪表盘汇总）

---

## 九、开发建议

### 9.1 前后端一体的简单启动方式

```python
# main.py
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse

app = FastAPI()

# 挂载前端
app.mount("/static", StaticFiles(directory="frontend"), name="static")

@app.get("/")
async def root():
    with open("frontend/index.html", "r") as f:
        return HTMLResponse(f.read())

# API 路由...
```

### 9.2 目录结构

```
memOS/
├── src/
│   ├── memory/          # 核心记忆逻辑
│   ├── testing/         # 测试运行器
│   │   ├── runner.py
│   │   ├── cases/       # 用例定义 YAML/JSON
│   │   └── evaluator.py
│   └── api/             # FastAPI 应用
│       ├── main.py
│       └── routes/
├── frontend/
│   ├── index.html       # 单页应用
│   ├── styles.css
│   └── app.js
├── memos.db             # 共用数据库
└── run_test.py          # 测试触发脚本
```

---

## 十、使用示例

### 运行一次测试并查看结果

```bash
# 1. 运行测试（自动写入数据库）
python run_test.py --version v0.2 --phase multi_session

# 2. 启动 Web 服务
uvicorn src.api.main:app --reload --port 8000

# 3. 打开浏览器访问 http://localhost:8000
# 仪表盘自动显示最新运行结果
```

### 前端页面功能预览

```
访问 /  → 仪表盘
   ├── 统计卡片: 总运行数、最新通过率、用例总数
   ├── 趋势图: 所有运行的通过率变化
   ├── 分类对比柱状图: 各阶段通过率
   ├── 最近运行列表: 点击进入详情
   └── 失败用例列表: 点击弹出对比模态框

访问 /#run/run_013  → 运行详情页
   ├── 运行摘要: 版本、阶段、日期、通过率、耗时
   ├── 通过/失败饼图
   ├── 失败用例明细（含期望 vs 实际对比）
   └── 通过用例折叠列表
```

---

这份需求文档包含了数据库设计、API 接口、前端界面和集成方案。Phase 1~3 已实现，Phase 4 细化见下一章。

---

## 十一、Phase 4 详细设计（待实现）

> 适用场景：测试集单次运行耗时数十分钟量级，人工需要观察进度、事后导出正式报告、持续维护用例定义、并使用真实 LLM 做质量评估。
> 遵循现有架构原则：单库 SQLite、FastAPI + SQLModel、纯前端（无构建）。并发控制见 **11.5 并发与幂等**。

### 11.1 实时进度轮询（SSE + Polling 双模式）

#### 11.1.1 数据库字段补充（复用现有，不新增表）

`test_runs` 表的 `status` / `progress` 字段已存在，新增以下语义约定：

| 字段 | 取值 | 说明 |
|---|---|---|
| `status` | `queued` / `running` / `completed` / `failed` / `canceled` | runner 写入；前端据此展示状态徽标 |
| `progress` | `0.0 ~ 1.0` | 由 runner 每完成一条 case 写入，SSE/Polling 均读此字段 |
| `duration_seconds` | `NULL` 或数值 | 运行中允许增量更新（预估耗时），完成后写真实值 |

> **避免重复对象复制**：不要为了更新 `progress` 每次把整行对象 clone；使用 `UPDATE test_runs SET progress = ?, status = ? WHERE id = ?` 直接按列更新即可。
> **提前返回**：进度接口在 `status == completed || failed || canceled` 时，立即返回 100% 并关闭 SSE 通道，避免空轮询。

#### 11.1.2 新增 API

```
# 已有接口增强（不改路径）
GET  /api/runs/{run_id}/progress        (复用，polling 模式，推荐 2s 间隔)

# 新增
GET  /api/runs/{run_id}/progress/stream  SSE 流式（text/event-stream），事件：progress | done | error
POST /api/runs/{run_id}/cancel           优雅停止（将 status=running 的标记为 canceled，runner 下一个 case 前检查）
```

**SSE 响应示例**：
```
retry: 2000
event: progress
data: {"status":"running","completed":12,"total":20,"percent":60.0,"eta_seconds":82,"last_case":"A-003"}

event: done
data: {"status":"completed","percent":100,"run_id":"run_013"}
```

后端实现建议：基于内存的简易 EventBus（`asyncio.Queue` per run_id），DB 更新后主动 push，无事件时 5s 心跳 `: ping`。
进程重启或多 worker 场景下，前端无缝降级为 polling 模式（JS 端 15s 无 SSE 数据自动切 polling）。

#### 11.1.3 前端接入

- **仪表盘顶部 StatusBar**：若存在任一 `status=running` 的 run，显示一条 `正在运行 run_xxx 62% ▶ 查看详情` 的可点击横幅，点击跳转到详情页。
- **运行详情页**：`status=running` 时顶部显示进度条（与骨架屏同款 shimmer 动画）、已完成/总数/ETA、实时"最近一条用例"mini 卡片；`percent 100%` 时触发 Toast "运行完成 🎉"，并重新拉取整页数据。
- **JS 策略**：先 SSE，失败/超时 15s 自动切 polling（2s 间隔）；离开页面 `AbortController.abort()` 立即释放连接（避免多层嵌套：事件监听器全部挂在 `AbortSignal` 上，离开时一次 `abort()` 清干净）。

---

### 11.2 真实 LLM-as-Judge 评测接入

#### 11.2.1 新增表 `test_judge_records`（Judge 调用审计 + 缓存）

```sql
CREATE TABLE test_judge_records (
    id TEXT PRIMARY KEY,                 -- UUID
    result_id TEXT NOT NULL,             -- FK -> test_case_results.id
    case_id TEXT NOT NULL,               -- 冗余，便于按用例查询
    provider TEXT NOT NULL,              -- 'openai' | 'anthropic' | 'dashscope' | 'mock'
    model TEXT NOT NULL,                 -- 'gpt-4o-mini' etc
    prompt_version TEXT NOT NULL,        -- 'judge-v1'  — 便于评估指标漂移时回溯
    input_hash TEXT NOT NULL,            -- sha256(expected_answer + actual_answer + query) — 缓存命中键
    score REAL NOT NULL,                 -- 0~1 标准化分数
    passed INTEGER NOT NULL,             -- 0|1（阈值 0.7 硬编码在 evaluator 可配置）
    reasoning TEXT,                      -- Judge 的思维链（可选，超长截断 65535）
    prompt_tokens INTEGER,
    completion_tokens INTEGER,
    latency_ms INTEGER,
    cache_hit INTEGER DEFAULT 0,         -- 0/1，命中不计费
    error_message TEXT,                  -- 重试失败时记录
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (result_id) REFERENCES test_case_results(id)
);
CREATE INDEX idx_judge_result ON test_judge_records(result_id);
CREATE INDEX idx_judge_cache  ON test_judge_records(input_hash, provider, model, prompt_version);
```

#### 11.2.2 代码模块（新增 `src/testing/evaluator.py`）

```python
# src/testing/evaluator.py —— 可插拔 Provider + 缓存 + 重试（指数退避）
from dataclasses import dataclass
from typing import Protocol

@dataclass
class JudgeResult:
    score: float
    passed: bool
    reasoning: str | None
    prompt_tokens: int
    completion_tokens: int
    latency_ms: int
    cache_hit: bool = False

class JudgeProvider(Protocol):
    def evaluate(self, query: str, expected: str, actual: str, *, version: str) -> JudgeResult: ...

class CachedJudge:
    """装饰器：先查 DB 缓存；命中直接返回。"""
    def __init__(self, inner: JudgeProvider, *, cache_db): ...

class RetryJudge:
    """装饰器：最多 3 次指数退避；最后一次失败抛给上层记录 error_message。"""
    ...
```

> **并发控制**：同一 `result_id` 的重评测加 DB 级乐观锁：
> `UPDATE test_case_results SET judge_status='judging' WHERE id=? AND judge_status<>'judging'`
> 返回 0 行说明有其他 worker 已在执行，提前返回（避免重复扣费）。

#### 11.2.3 新增 API

```
POST /api/cases/evaluate          手动触发：对指定 case_id 所有历史结果重新 Judge（后台任务）
POST /api/runs/{id}/reevaluate    对单次运行所有失败/全部 case 重新 Judge（参数 scope=failed|all）
GET  /api/runs/{id}/reevaluate/progress   后台任务进度（复用 progress 通道语义）
GET  /api/judge/stats             仪表盘面板页用：本月调用量、Token、缓存命中率、Top-3 最慢 Provider
```

#### 11.2.4 前端入口

- 运行详情页：每个失败用例卡片右下角加 `🔄 重新评测（Judge）` 按钮；按 `Shift` 批量勾选后"批量重评"。
- 失败对比模态框：底部增加 `【Judge 推理过程】` 可折叠块，展示 `reasoning` 和 provider/model/token 成本微数据。
- 仪表盘：新增一个统计小卡（可选），显示 `本月 Judge 成本 / 缓存命中率 XX%`。

---

### 11.3 报告导出（CSV 明细 + PDF 正式报告）

#### 11.3.1 新增 API

```
GET  /api/exports/runs/{id}.csv           下载：单次运行所有用例结果明细（含 score / latency / error）
GET  /api/exports/cases.csv               下载：所有用例定义（含 tags / query / expected_answer）
POST /api/exports/runs/{id}/pdf           触发 PDF 生成任务（后台，文件落盘后返回 download_token）
GET  /api/exports/download/{token}        下载生成好的 PDF（一次性短 token，1 小时过期）
POST /api/exports/dashboard/pdf           触发"全版本汇总仪表盘报告"PDF 生成
```

> **CSV 并发安全**：流式响应 `StreamingResponse` + `sqlite3` 按 200 行分块 yield；不一次性把全量结果 load 进内存，避免大数据库 OOM。

#### 11.3.2 PDF 报告结构（A4 纵向 · 带封面页）

```
[封面] MemOS 评测报告 · 版本 X · 阶段 X · 生成日期 YYYY-MM-DD
[第 1 页] 执行摘要
         - 通过率大数字 / 总用例 / 通过 / 失败 / 总耗时
         - 与上一版本（同阶段）对比 delta（例如 v0.4 vs v0.3 ↑+6.2% 📈）
[第 2 页] 图表
         - 通过/失败环形图（Chart.js → toBase64Image 嵌入）
         - 分类通过率柱状图
         - 历史趋势线（最近 10 次同阶段运行）
[第 3 页起] 失败用例全量附录
         - 每张：Case ID / 名称 / Score / 期望 / 实际 / 检索记忆截断版
[末页] 元数据
         - 配置快照 JSON（折叠段）、备注 notes、触发人 triggered_by
```

**技术选型**：后端使用 `reportlab` 或 `weasyprint`（二者择一，推荐 weasyprint 复用现有 CSS 能力，直接把运行详情页 HTML 转为 PDF）。依赖放在 `pyproject.toml` 的 optional group `[project.optional-dependencies]` 中，非默认安装。

#### 11.3.3 前端入口

- 运行详情页标题旁：`⬇ 导出` 下拉按钮 → CSV / PDF。
- 运行列表页：表头上方批量复选框 → 多选运行后 `导出汇总 PDF`。
- 用例管理页：表格上方 `⬇ 导出 CSV`。
- PDF 生成进度：Toast "报告生成中… 0%" → "完成，点击下载"；失败时"生成失败，请重试"并附错误 id 供排查。

---

### 11.4 用例定义 CRUD 表单

#### 11.4.1 新增 API

```
POST   /api/cases                     创建
PUT    /api/cases/{case_id}           全量更新（注意：最小改动原则，PATCH 可选）
DELETE /api/cases/{case_id}           软删除（加 deleted_at，避免历史 result 外键混乱）
POST   /api/cases/import              批量导入：multipart/form-data 上传 YAML 或 JSON（格式对齐 tests/test_cases/layer1/*.yaml）
GET    /api/cases/{case_id}/diff?version_from=v0.1   返回 name/query/expected_answer/tags 的 JSON diff
```

#### 11.4.2 数据库变更

1. `test_case_definitions` 新增软删除列：
   ```sql
   ALTER TABLE test_case_definitions ADD COLUMN deleted_at DATETIME;
   ```
   现有读取接口（`GET /api/cases`、`GET /api/cases/{id}`）默认 `WHERE deleted_at IS NULL`。

2. 新增审计表（任何编辑动作都留痕，后续合规回溯）：
   ```sql
   CREATE TABLE case_edit_logs (
       id TEXT PRIMARY KEY,
       case_id TEXT NOT NULL,
       action TEXT NOT NULL,           -- 'create' | 'update' | 'delete' | 'import'
       field_name TEXT,
       old_value TEXT,
       new_value TEXT,
       operator TEXT DEFAULT 'unknown', -- 后续接入登录体系
       created_at DATETIME DEFAULT CURRENT_TIMESTAMP
   );
   ```

#### 11.4.3 前端 UI 规范

- **新增/编辑模态框**（抽屉式从右侧滑出，宽度 560px，避免模态框嵌套）：
  - 基本信息：Case ID（自动生成如 `R-021`，允许手改但唯一校验）、名称、分类（phase chip 三选一）、引入版本
  - 标签区：`✕ 旅行  ✕ 护照  ✕ 国际航班  + 添加标签`（Chip 组件，回车生成）
  - 描述：多行 textarea
  - Setup Dialog：对话编辑器，轮播增删每轮 `user/assistant` 气泡
  - Query / 期望答案：两个大 textarea，旁侧 `🧪 预览 Judge 评分` 按钮（弹小面板跑一次 evaluator，可选 mock）
  - 底部：`取消 / 保存草稿 / 保存并发布`

- **列表页增强**：
  - 行操作列：`✏️ 编辑` / `📜 历史` / `🗑 删除`
  - 顶部 `＋ 新建用例` 主按钮 + `📥 批量导入` 副按钮
  - 批量导入向导：拖拽 YAML 文件 → 预览解析出的前 5 条 → 显示冲突（已存在 case_id）→ 选择"跳过/覆盖/自增 ID"→ 执行

- **版本对比 diff**：用例详情页顶部下拉 `对比版本 v0.1 ▾`，选中后三栏并列：
  `v0.1 的内容  ║  diff 高亮 +-  ║  v0.4 的内容`

---

### 11.5 并发与幂等（贯穿 11.1~11.4）

| 模块 | 问题 | 机制 |
|---|---|---|
| 进度 | 多 runner 并发写同一 run | `UPDATE test_runs SET progress=GREATEST(progress, ?) WHERE id=?`（用 GREATEST 防回退） |
| Judge | 重复触发重复扣费 | 乐观锁 `judge_status='judging'` + `input_hash` 缓存索引 |
| PDF | 多次点导出 | POST 先 upsert 任务表：若同一 run_id 有 `status=generating` 的任务直接返回同一 task_id |
| 用例编辑 | 多人同时改同一 case | PUT 带 `If-Match: <updated_at>` 头，服务器比对不一致返回 409 Conflict + 当前最新 |
| 导入 | 重复导入同一 YAML | `ON CONFLICT(case_id) DO NOTHING` 或按用户选择覆盖 |

> **全局约定**：
> - 所有后台任务（PDF 生成、批量重评测、批量导入）统一写 `background_tasks` 表（一表通用，不复用 test_runs 的 status），避免每个子系统各造一套。
> - `progress` 写入节流：前端期望 2 秒刷新，但 DB 写操作最多 500ms 一次——在 runner 循环内加 `time.monotonic()` 时间窗节流，而不是每个 case 都写（避免高频 SQLite 锁冲突）。

---

## 十二、Phase 4 验收清单（勾选式，待完成）

- [ ] 运行进度 SSE 10 次以上连续推送无断连；关闭连接后 3s 内 DB 不再有写入
- [ ] 同一 result_id 重复点击"重新评测"只触发 1 次 LLM 调用；缓存命中时 0 token
- [ ] CSV 导出 10000 行数据库文件，Web 进程 RSS 不超过 500MB 峰值
- [ ] PDF 报告包含封面 / 图表 / 失败附录 三部分；打印分页整齐，不切行
- [ ] 用例编辑：同一 case 两人同时保存，后提交者返回 409 并看到冲突 diff
- [ ] 软删除的 case 在所有列表不再出现，但历史运行结果仍能正常按 case_id 查询
- [ ] 审计日志 `case_edit_logs` 至少记录 `create/update/delete/import` 四种动作
- [ ] 无 JS 控制台 Error（含重复声明、缓存失效、空数据三类）
