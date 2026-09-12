# MemOS 评测展示系统需求文档

> **文档状态（2026-09-12 整理）**
> - 本文件是 EvalView（评测看板）**唯一的需求文档**。原先另存于方案目录的
>   `方案-EvalView记忆管理.md`（记忆管理能力需求）已整合为**第十三章**并从方案目录删除，
>   不再两处维护。
> - 实现现状（与本文早期章节的差异；早期章节保留为历史设计稿）：
>   - **双库分离**：评测记录在 `src/testing/data/memos.db`，业务记忆在
>     `src/os_mem/data/memories.db`，两库表不互建——早期"共用同一 SQLite 文件"的说法已废弃；
>   - **前端是 Vite + React + TypeScript**（`frontend/src`），构建产物 `frontend/dist` 由后端挂载，
>     不是"单页 HTML + 原生 JS + Chart.js"；
>   - 后端在 `src/testing/api`，记忆数据访问统一经 `src/os_mem/admin` 管理窗口（第十三章）。
> - 章节现状：一~三（按上述现状读）｜四~七 已实现但技术选型被 React 取代（历史设计稿）｜
>   八 Phase 1~3 已实现、Phase 4 未做｜九~十 历史启动/使用示例｜十一~十二 Phase 4 待实现｜
>   **十三 记忆管理能力（已实施）**。

## 一、系统概述

### 1.1 目标

为 MemOS 各版本的测试集运行提供一个**可视化展示面板**，让开发者能够：
- 查看每次测试运行的完整记录
- 对比不同版本、不同阶段的通过率变化
- 深入分析失败用例的具体原因
- 追踪记忆系统的演进趋势

### 1.2 架构原则

- **双库分离**：评测记录存 `src/testing/data/memos.db`，业务记忆存 `src/os_mem/data/memories.db`，
  两库表不互建（早期"共用同一 SQLite 文件"的设计已废弃，2026-09 起分库）。
- **前端**：Vite + React + TypeScript（`frontend/src`），构建产物 `frontend/dist` 由后端挂载
  （早期"单页 HTML + 原生 JS + Chart.js"已废弃，第四~七章保留为历史设计稿）。
- **后端**：FastAPI（`src/testing/api`）；记忆数据访问统一经 `src/os_mem/admin` 管理窗口（第十三章）。
- **开箱即用**：本地开发工具，无鉴权；`uv run uvicorn testing.api.main:app --port 8000 --reload` 启动。

---

## 二、数据库设计（新增表）

在**评测库** `src/testing/data/memos.db` 中新增以下表（与业务记忆库 `src/os_mem/data/memories.db` 分离；
SQLModel 的 metadata 是全局的，任何 `create_all` 必须用 `tables=[...]` 限定表集合，防跨库串建）：

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

> 记忆管理接口（`/api/memories/*`，含事实 CRUD、会话原文、清空与重建投影）见**第十三章**。

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

### 4.1 技术选型（历史设计稿 —— 实现已改为 Vite + React + TS，见文档状态）

- **纯 HTML + CSS + JavaScript**（一个文件）
- **Chart.js**：图表渲染（CDN 引入）
- **Vanilla JS**：无框架依赖，无构建工具

> 实际实现为 `frontend/src`（React + TS + Vite，组件按域拆分，API client 见 `frontend/src/api/`）。
> 本章以下的布局与交互描述仍可作为信息架构参考。

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
11. 真实 LLM-as-Judge 评测接入（可插拔 Provider、调用记录、缓存）
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

#### 11.2.3 新增 API

```
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
> - 所有后台任务（PDF 生成、批量导入）统一写 `background_tasks` 表（一表通用，不复用 test_runs 的 status），避免每个子系统各造一套。
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

---

## 十三、记忆管理能力（EvalView 记忆管理，已实施）

> 本章由原 `docs/方案/方案-EvalView记忆管理.md`（2026-09-08 记录，批 1/批 2 已实施）整合而来；
> 2026-09-12 起并入本文件，方案目录不再保留该文件。
> 上游原则：`docs/方案/方案-记忆更新收敛与Milvus投影一致性.md`（SQLite 权威源 / Milvus 投影收敛）、
> `docs/方案/方案-D4-实体归属与as-of版本裁决.md`（实体归属与版本裁决）。
> 涉及代码：`src/os_mem/admin/`（管理窗口）、`src/testing/api/routes/memories.py`、`frontend/src/pages/Memory*Page.tsx`。

### 13.1 目标与范围

在 EvalView 中新增**记忆管理**能力：查看某「用户」（当前数据即评测 case，`user_id = test_id`）
已沉淀的记忆数据，并对其做管理操作——修正/删除提取错误的事实、手动补充缺失事实、清空后允许重新提取。
做完改动后重跑评测，即可验证检索注入与回答的改善（管理入口闭环到评测验证）。

| 现状 | 缺什么 | 加了什么 |
|---|---|---|
| EvalView 只读 `memos.db`（评测记录），看不到记忆本体 | 记忆在 `memories.db`（2026-09-08 快照：struct_memories 1130 行 / 20 用户 / 每用户 33~95 条），只能 sqlite3 CLI 或脚本查改 | 记忆浏览/检索 + 增删改管理界面 |
| 修正一条错误记忆需手工写 SQL（还容易漏投影） | 无可视化、无投影同步 | 编辑/删除/新增自动同步向量投影（尽力），失败可一键重建 |
| 清空记忆想重跑 = 还要记得清 conv_meta 门禁 | 门禁（COMPLETED 跳过 ingest）与事实耦合 | 「清空并允许重新提取」一站式操作 |

**非目标**：不做后台 reconcile 守护进程（按需用户级 rebuild 已够，见 13.9）；不做重提取的自动调度
（重提取 = 跑 ingest/评测，需 LLM 费用，由用户显式发起）；不动检索策略/评测语义；不做生产级鉴权
（本地开发工具，维持现状）。

### 13.2 数据与代码现状（设计的事实基础）

#### 13.2.1 数据与代码位置

- 评测记录库：`src/testing/data/memos.db`（test_runs / test_case_results / test_case_definitions）——
  EvalView 现有 runs/cases/stats 路由只读它。
- 业务记忆库：`src/os_mem/data/memories.db`（conv_messages 原文 / conv_meta 会话+状态机 / struct_memories 事实）。
  权威源锚定在 `os_mem.infra.storage.mem_storage.MemoryDatabase`（相对路径恒落 `src/os_mem/` 下，不随 cwd 漂移）。
- 评测 case 的 `test_id`（如 `layer1_01_bank_account`）与 struct_memories / conv_meta 的 `user_id` **同名同值**
  → 用例页 ↔ 记忆页可互相链接（本机 memos.db 的用例定义表当前为空，不影响记忆页本身，链接在定义存在时才有意义）。
- EvalView 前端 = React + TS（Vite，`frontend/src`），现有 UI 组件：Button / Badge / Toast / RateBar / Skeleton；
  API 客户端按域分文件（`api/{runs,cases,stats,memories}.ts`）；导航在 `components/Layout/AppLayout.tsx` NAV 数组。

#### 13.2.2 必须遵守的约束与陷阱

1. **分层方向**：`os_mem` 不得 import `testing/eval`；`testing`（管理侧）可反向 import `os_mem`，但只经管理窗口（13.7）。
2. **禁止顶层 import `os_mem.core.services.struc_mem_service`**：该模块 import 即构造
   `get_llm_client() / get_vectorizer() / get_memory_vector_store()`（= 建 Milvus 客户端、DashScope embedder）。
   EvalView 进程若顶层引它会启动即连云端。**投影/向量相关对象一律路由内 lazy 构造 + try/except**
   （离线时浏览/编辑 SQLite 照常，Milvus 失败只警示）。
3. **双库不互建表**：SQLModel.metadata 全局，任何 create_all 必须显式 `tables=[...]`。
   记忆路由**只读现成表、不 init_db 业务库**（表由管线保证存在）。
4. **向量投影行 id = 随机 uuid，与 SQLite struct_memories.id 无关**（`add_structured_memories` 内部新生成）。
   投影删改只能按 `(user_id, category, key)` 过滤（`vec_storage.delete_memories` 已实现），不能按事实 id。
5. **投影不变量**（A 批后；D4-2 起收敛键升级）：Milvus 恒为每 `(user, category, 收敛键)` 一条 current，
   收敛键 = `projection_key(entity_ref, attribute)`（SELF 实体即裸 attribute，非 SELF 带 `<实体>|<属性>` 前缀）。
   手工管理操作不得破坏它——故**编辑不允许改身份字段**（改身份 = 先删旧事实、再新增新键，两步显式完成）。
   手工管理路径已对齐（2026-09-12 修复：删改按收敛键、非 current 行不进投影、重建只投 current），见 13.9-7。
6. 投影行 `updated_at` 是 **naive UTC ISO 字符串**（`datetime.utcnow().isoformat()`）；SQLite 时间戳同为 naive UTC。
   手工写入投影必须沿用同一格式，前端显示经 `schemas._utc_iso` 标注 UTC。
7. conv_meta 状态机：COMPLETED = 该会话已提取入库，重跑评测**跳过 ingest**（记忆缓存门禁）。
   清空记忆后要能重新提取，必须**同时把该用户 conv_meta 行重置为 PENDING**（claim 对 PENDING 可 CAS 接管；
   原文 conv_messages 保留，重提取从原文再生）。verbatim 兜底句 key=内容指纹，重提取幂等不重复。

### 13.3 设计原则

1. **SQLite 是权威源，先写后投影**：一切管理写操作先提交 memories.db（确定性成功），再尽力同步 Milvus 投影。
2. **投影尽力同步 + 可重建**：单条写操作后按收敛键删旧向量 → embed → 插新；同步失败**不阻断也不回滚**
   SQLite，接口返回明确警示，UI Toast 提示「可点『重建投影』修复」；每用户提供「重建投影」按钮
   （读 SQLite 全量 → 删该用户全部向量 → 批量 embed → 重插），兼作任何历史漂移的兜底。
3. **身份字段不可经编辑修改**：身份字段构成裁决/投影唯一性，编辑只允许改 fact / value / confidence。
4. **危险操作显式语义 + 二次确认**：删除、清空（尤其「清空并允许重新提取」涉及后续 LLM 费用）UI 必确认。

### 13.4 管理操作语义（服务层）

实现在 `src/os_mem/admin/mem_admin_service.py`（**依赖注入**：engine + 可选 vector_store/vectorizer，
纯逻辑可离线单测；不 import struc_mem_service）。

| 操作 | SQLite（权威，先做） | 投影（尽力，后做） | 备注 |
|---|---|---|---|
| 新增事实 | INSERT（新 uuid，显式落 D4 身份字段） | delete(user,cat,[收敛键]) 幂等 → embed → add | key/category 允许与既有键共存（同键即覆盖旧值语义）；`attribute=key`、实体 SELF、lifecycle current |
| 编辑事实 | UPDATE fact/value/confidence；`previous_fact`=旧 fact；updated_at=now；created_at 不动 | delete(user,cat,[收敛键]) → embed 新 fact → add | 身份字段不可改（13.3-3）；非 current 行只落 SQLite（projection=skipped） |
| 删除单条 | DELETE 行 | delete(user,cat,[收敛键])；同收敛键还有存活 current 行则以存活行重写 | 幂等（投影无该键=0 删除，无害）；非 current 行不进投影 → 只删 SQLite |
| 清空（仅事实） | DELETE 该 user 全部 struct_memories | delete(user) 全量 | 保留 conv_messages 原文与 conv_meta 状态 |
| 清空并允许重提取 | 同上 + conv_meta 该 user 行 status→PENDING | delete(user) 全量 | 下次 ingest/评测从 conv_messages 原文重新抽取（LLM 费用，UI 警示） |
| 重建投影 | 无 | delete(user) → embed_batch（**仅 current 行**）→ add | 每用户手动入口；修复一切投影漂移 |

> **收敛键** = `projection_key(entity_ref, attribute)`，与落库管线共用同一实现（`os_mem.extractor.utils.normalize`）；
> `attribute` 为空的历史行退回裸 `key`。superseded / historical 行不进投影（D4-3）。

写接口统一返回：`{ ok, operation, sqlite, projection: "synced"|"failed"|"noop", warning? }`，前端据此 Toast。

### 13.5 后端 API 设计（`src/testing/api/routes/memories.py`，prefix `/api/memories`）

- `GET /users` → 用户列表（user_id / fact_count / 类别分布 / message_count / conv_meta 状态聚合 / last_updated）
- `GET /users/{user_id}/facts?category=&q=&offset=&limit=` → 事实分页（q 匹配 fact/key/value 子串）
- `GET /users/{user_id}/facts/{fact_id}` → 单条详情（含 previous_fact / source 引用）
- `GET /users/{user_id}/conversations/{conversation_id}/messages` → **只读原文**（conv_messages 按 seq）
- `POST /users/{user_id}/facts` · `PATCH /users/{user_id}/facts/{fact_id}` · `DELETE /users/{user_id}/facts/{fact_id}`
- `POST /users/{user_id}/clear` body `{reset_conv_meta: bool}`
- `POST /users/{user_id}/rebuild-projection`

工程要点：
- 内存库会话：复用 `MemoryDatabase.get_engine()`（锚定单一来源），`Session(engine)`；
  **不调用** `MemoryDatabase.init_db()`（防止 API 启动对业务库做迁移/建表副作用）。
- lazy 依赖：路由内函数级 import `vec_storage.get_memory_vector_store / vectorizer.get_vectorizer`，
  try/except 捕获构造与调用异常 → projection=failed + warning 文本回给前端。
- 时间戳：SQLite naive UTC → schema 复用 `schemas.UtcDateTime`（`_utc_iso` 输出带 +00:00）。
- `main.py` include `memories_router`；schemas 增补 MemoryUser / MemoryFact / MemoryMessages / 写响应信封。

### 13.6 前端设计（React，Vite）

- AppLayout NAV 新增「记忆管理」（记忆块图标），路由：`/memories`（用户列表页）、`/memories/:userId`（事实管理页）。
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
- 可选互链：Case 详情/历史页加「查看该用户记忆」入口（user_id=case_id 时）；记忆页反向显示 case 关联
  （memos.db 有定义时）。

### 13.7 分层重构：os_mem 对外管理窗口（2026-09-08 追加）

**问题**：初版管理服务放在 `src/testing/services/`，直接 import os_mem ORM、复用 MemoryDatabase engine
并对 struct_memories 表做 SELECT/UPDATE —— 管理面对记忆库的权限过宽（越权绕过领域层，schema/约束一变就漏）。

**改动**：管理能力收进 **`src/os_mem/admin/`（MemAdminService + get_mem_admin_service）**
作为 os_mem 对外唯一管理窗口；`testing/api/routes/memories.py` 瘦身为纯 HTTP 适配
（参数校验 → 调窗口 → 组响应），testing 侧直连实现（mem_admin_service / mem_projection.py）删除。

| 边界 | 说明 |
|---|---|
| 对外契约 | 窗口返回 **纯 dict**（不泄露 ORM/engine）；异常只抛 LookupError（外部转 404） |
| 投影封装 | 尽力同步 + 失败警示 + 按用户重建兜底全部在窗口内部；`MemAdminService(projection=fake)` 注入测试，`allow_live=False` 离线纯 SQLite |
| import 无副作用 | 窗口放 `os_mem.admin` 顶层包（**不放 core/services**：`os_mem.core/__init__` 会级联 import struct_provider，模块 import 即构造 LLM/Milvus client，破坏 EvalView 启动轻量） |
| 会话细节 | `expire_on_commit=False`：commit 后属性保留，写方法在会话外读行字段做投影同步不抛 DetachedInstanceError |

验证：`tests/unit/test_mem_admin_service.py` 重写为窗口视角 14 passed；全量 unit 122 passed；
uvicorn 冒烟 create/update/delete 投影均 synced、零残留。

### 13.8 测试与实施批次

**测试计划（无 LLM，可自由跑）**：`tests/unit/test_mem_admin_service.py`（tmp sqlite 建业务表 +
fake vector_store/vectorizer 注入）：
- 编辑：previous_fact 归档 / updated_at 刷新 / 投影 delete(user,cat,[收敛键]) + embed 1 次 + add 1 次，参数正确；
- 新增 / 删除单条：SQLite 行与投影调用对应；删除不存在的键幂等；
- clear：仅事实 vs + conv_meta→PENDING（断言 PENDING、原文行保留）；
- rebuild：delete(user) → embed_batch(仅 current 行) → add，顺序与数量正确；
- 投影失败分支：fake raise → 返回 ok + projection=failed + warning，SQLite 已提交不回滚。
- **D4 收敛键一致性（2026-09-12 追加 10 例，27 passed）**：编辑/删除按
  `projection_key(entity_ref, attribute)`（含非 SELF 实体前缀、`attribute` 空行退回裸 key）；
  非 current 行只落 SQLite 且返回 `skipped` + 警示；删除 current 行时同收敛键的存活行被重写回投影；
  `rebuild` 只投 current；手工新增显式落 D4 身份字段；同键多行时 `upsert_fact` 优先改 current 行。

FastAPI 层薄（组装参数 → 调服务），以单测覆盖服务为主；真实 uvicorn 冒烟在联调批做（curl 本机 8765，
只读/写测试用户数据，不碰真实 case 记忆，或先在临时 `MEMORY_DB_PATH` 上起一个验证进程）。
**评测一律不跑**：本改动不动检索/提取语义；若改完记忆想验证注入修复，由用户显式发起 layer1 重跑。

| 批 | 内容 | 验证 |
|---|---|---|
| 1 | `mem_admin_service` + `routes/memories.py` + schemas + 单测 | ✅ 14 passed（全量 unit 122 passed） |
| 2 | 前端：NAV + 用户列表页 + 事实管理页（CRUD/原文/危险操作）+ api client | ✅ `npm run build` 0 error；uvicorn 冒烟：读接口 200 / 写链路 create·update·rebuild·delete 均 projection=synced（真实 Milvus）/ 残留 0 / SPA 直链 fallback 200 |
| 3（可选） | case 页 ↔ 记忆页互链；conv_meta 状态展示（**已在用户页概览条实现**）；修正过时的需求文档（单页 HTML/同库说法） | 文档修正已随本次整合完成（见文档状态）；**case↔记忆互链仍未做** |

### 13.9 风险与开放问题

1. **Milvus 可达性/凭证**：EvalView 进程跑在哪台机器，就用那台机器的 `.env`（MILVUS_URI/KEY、DASHSCOPE）。
   失败已设计为警示 + 重建兜底，不阻塞 SQLite 管理；但「改完立刻重跑评测」前需投影一致（重建按钮兜底）。
2. **SQLite 并发写**：若评测 ingest 与记忆管理同时写 memories.db，SQLite 锁可能让一边短暂等待/重试；
   单执行者开发场景概率低，遇错提示重试即可（不为此上 WAL，避免超范围改动）。
3. **原文含合成 PII**：conv_messages 展示用 content；本地工具可接受（数据为评测合成人物）。
4. **重提取成本**：UI 警示前置；verbatim 指纹幂等，重复提取不堆积。
5. **本机 memos.db 用例定义表为空**（test_case_definitions 0 行）→ 用例页当前没数据；不影响记忆页，
   互链功能在定义存在（开发机）时才生效。
6. open：事实编辑后 confidence 由人给（0-1 默认保留原值）；是否要审计「管理操作日志」（v1 不做，靠 git/回忆）。
7. **【已修 2026-09-12】管理窗口与 D4 收敛键不一致**（原症状：投影同步按**原始 key 列**操作）：
   - 修复前：`_row_to_projection_record()` 写 `key=row.key`、`update_fact`/`delete_fact` 按 `[row.key]`
     删、`rebuild_projection()` 不做 `lifecycle` 过滤 → 编辑/删除删不掉管线留下的 canonical 投影行
     （新旧值并存注入）；「重建投影」把投影键退化为裸 key 并把 superseded / historical 行一起投回，
     撤销 D4-1/D4-3 的收敛。影响面只限「手工管理过 / 点过重建的用户」，评测管线路径不受影响。
   - 修法（`src/os_mem/admin/mem_admin_service.py`）：
     - `_projection_key_of(row)` 统一收敛键 = `projection_key(entity_ref, attribute)`，**与管线共用同一实现**
       （`os_mem.extractor.utils.normalize`，纯函数、不建连，不另造口径）；`attribute` 为空的历史行退回裸
       `key`，避免收敛键退化成空串；
     - 新增/编辑/删除的投影删改全部改用收敛键；`upsert_fact` 新增行显式落 `attribute=key`、实体 SELF、
       lifecycle current；
     - 非 current 行（superseded/historical）**不进投影**：编辑/删除只落 SQLite，返回 `projection="skipped"`
       + 警示文案（避免把旧值写进 current 的向量）；
     - 同键多行（版本链）时 `upsert_fact` 优先改 current 行；删除 current 行时若同收敛键还有存活的
       current 行（D4 前脏数据），以存活行重写该键，而不是把向量删掉；
     - `rebuild_projection()` 加 `lifecycle='current'` 过滤。
   - 验证：`tests/unit/test_mem_admin_service.py` 追加 10 例（收敛键/canonical attribute、非 SELF 实体前缀、
     历史行兜底、非 current 跳过、存活行重写、重建只投 current、新增行 D4 字段、同键优先 current），
     27 passed；全量 `tests/unit` 272 passed。
8. **【open】镜像导出/导入不带 D4 身份字段**：`export_user_data()` 的 `struct_memories` 载荷仍是旧列
   （无 `entity_ref`/`attribute`/`lifecycle`/`source_started_at`/`version`/`supersedes_id`），跨机
   `import_memory_batch()` 后这些列回到默认值 → 收敛键退化为原始 key、实体归属与版本链丢失
   （13.9-7 的兜底保证「不写错键」，但跨机一致性仍会退化）。建议载荷升版（带 D4 列 + `schema_version`）
   并在导入侧原样恢复；改前需确认 `方案-评测记录跨机同步.md` 与现有导出文件格式的兼容策略。
9. **【open】界面不显示 lifecycle / entity / attribute**：`list_facts` / `_fact_to_dict` 不返回 D4 字段
   （也不按 lifecycle 过滤），用户看到的是「扁平事实列表」，其中可能混着 superseded / historical 行；
   此时编辑会返回 `projection="skipped"` + 警示（行为正确），但界面无法解释原因、也无法定位是版本链的哪一版。
   建议把 `lifecycle`/`entity_ref`/`attribute` 透出到 `MemoryFactItem`，事实表格加版本标记 / 筛选 / 默认只看 current。

### 13.10 决策记录（含被否方案）

| 方案 | 结论 | 理由 |
|---|---|---|
| 直接 import struc_mem_service 复用其写路径 | **被否** | 模块级 import 副作用（构造 Milvus/LLM singleton）；`add_structured_memory` 面向整段会话 LLM 提取，不是单事实管理原语 |
| 写操作只改 SQLite + 「脏标记」，由用户记得重建 | **被否** | 「改完即忘重建」→ 重跑评测拿到旧投影，管理闭环断裂；尽力同步 + 失败警示成本低 |
| 编辑允许改 category/key | **被否** | 破坏投影唯一性不变量；改身份 = 删旧建新两步显式完成，语义清楚 |
| 单条写操作即时逐条同步投影 | **采用** | delete(user,cat,[key]) + embed + add，与 A 批收敛同一套原语，常数成本 |
| 每用户「重建投影」按钮（用户级 reconcile） | **采用** | 承接 A 批「reconcile 不做」的结论：仍不做守护/全局工具，但管理工具需要用户级按需修复入口 |
| 清空记忆 = 连带自动重提取 | **被否** | 重提取触发 LLM 费用与 ingest，须用户显式发起评测/跑批；工具只负责「开门」（PENDING） |

### 13.11 机制术语学习笔记

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
