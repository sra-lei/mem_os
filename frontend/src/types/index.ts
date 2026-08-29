// 与后端 src/api/schemas.py **严格对齐**的前端类型定义。
// 规则：
//   - 所有 ID 类型统一为 string（后端使用 UUID hex）。
//   - PhaseKey / RunStatus 是真实 DB 值的联合字面量类型。
//   - 为了兼容现有组件代码，对旧 TS interface 中的 key 增加了 compat alias（同时存在两种 key）。
// --------------------------------------------------------------------------------

// ---------- Enums / literal unions ----------

// Case category / Run phase — both share the same 3 values on the real schema.
export type PhaseKey = 'base' | 'multi_session' | 'proactive';

export type RunStatus = 'pending' | 'running' | 'completed' | 'failed' | 'cancelled';

// ---------- Shared helpers ----------

type ISOString = string;

// ============================================================================
// TestRun 领域
// ============================================================================

/** 后端 schemas.RunSummary — 17 字段（真实表 + 派生 + 兼容别名） */
export interface RunSummary {
  // --- Core TestRun columns ---
  id: string;
  version: string;
  phase: PhaseKey;
  run_at: ISOString;
  total_cases: number;
  passed_count: number;
  pass_rate: number;
  duration_seconds: number | null;
  notes: string | null;
  triggered_by: string | null;
  status: RunStatus | null;
  progress: number | null;
  config_snapshot: Record<string, unknown> | null;

  // --- Derived (filled by routes) ---
  name: string;                     // "<version> · <phase>"
  failed: number;                   // total_cases - passed_count

  // --- Backward-compat aliases ---
  start_time: ISOString;            // alias of run_at (all UI components currently use this key)
  end_time: ISOString | null;       // run_at + duration_seconds
  passed: number;                   // alias of passed_count

  // --- Extra legacy fallback fields (kept optional to avoid null runtime NPE in RunSummary/RunsTable) ---
  /** Real DB column is `notes`; some legacy UI components look up `error_message`. */
  error_message?: string | null;
  /** status nullable; components that require non-null use fallback "completed" */
}

/** 后端 schemas.RunListResponse */
export interface RunListResponse {
  runs: RunSummary[];
  total: number;
}

/** 后端 schemas.RunProgress（轮询 /{run_id}/progress 使用） */
export interface RunProgress {
  status: RunStatus;
  completed: number;
  total: number;
  percent: number;
}

// ============================================================================
// TestCaseResult 领域（结果行 + JOIN 的 case 定义字段）
// ============================================================================

/** 后端 schemas.CaseResult — 真实 result 列 + 8 列来自 JOIN case_definition */
export interface TestCaseResult {
  // --- Core TestCaseResult columns ---
  id: string;
  run_id: string;
  case_id: string;
  case_name: string;
  category: PhaseKey;
  version: string;
  passed: 0 | 1;                         // 0 / 1 int on DB
  score: number | null;
  expected_answer: string | null;
  actual_answer: string | null;
  retrieved_memories: string | null;     // JSON string
  error_message: string | null;
  latency_ms: number | null;
  created_at: ISOString;

  // --- JOINed from TestCaseDefinition（仅在 run detail 接口填充）---
  query: string | null;
  description: string | null;
  tags: string[];                         // fillResultCompat always parses JSON → string[]
  evaluation_criteria: string | null;
  expected_behavior: string | null;
  conversation_histories_raw: string | null;
  source_path: string | null;

  // --- Legacy compat aliases（旧 UI 用了这些 key）---
  retrieval_memory: string | null;       // alias of retrieved_memories
  user_input: string | null;             // alias of query
  expected_output: string | null;        // alias of expected_answer
  actual_output: string | null;          // alias of actual_answer
  judge_reason: string | null;           // maps to: error_message（失败场景下 LLM 判断理由） || evaluation_criteria（若 error 为空）
  phase: PhaseKey;                       // alias of category（PhaseBadge UI 用 phase）
  tokens_input: number | null;           // 真实表里没有；渲染时显示 null
  tokens_output: number | null;          // 同上
}

/** 后端 schemas.RunDetailResponse（继承 RunSummary + results 数组） */
export interface RunDetail extends RunSummary {
  results: TestCaseResult[];
}

// ============================================================================
// CaseDefinition 领域
// ============================================================================

/** 后端 schemas.CaseDefinition — 真实 DB 的 15 列 + 3 列 computed 历史统计
 *
 *  ⚠️ 字段说明：`tags` 列 DB 里存的是 JSON 字符串，但前端 API 客户端 casesApi.ts 已经
 *     统一在 fillCaseCompat() 里把它解析成真实 string[]。这里直接声明成 string[]。
 */
export interface CaseDefinition {
  // --- Core TestCaseDefinition columns ---
  case_id: string;
  name: string;
  category: PhaseKey;
  version_target: string;
  description: string | null;
  query: string | null;
  expected_answer: string | null;
  tags: string[];                          // fillCaseCompat always parses → string[]
  conversation_histories_raw: string | null;
  evaluation_criteria: string | null;
  expected_behavior: string | null;
  source_path: string | null;
  created_at: ISOString;
  updated_at: ISOString | null;

  // --- Derived aggregate counters (filled by cases.py) ---
  total_runs: number;
  pass_count: number;
  fail_count: number;

  // --- Legacy compat aliases ---
  id: number;                            // legacy placeholder; UI uses case_id anyway
  case_name: string;                     // alias of name
  phase: PhaseKey;                       // alias of category (PhaseBadge)
  retrieval_memory: string | null;       // legacy compat, unused real col
  user_input: string | null;             // alias of query
  expected_output: string | null;        // alias of expected_answer
  // --- NEW compat aliases (CasesPage cols + new detail page metadata) ---
  version: string | null;                // alias of version_target
  layer: string | null;                  // derived: layer1/2/3 from source_path
}

// ============================================================================
// Case history 领域
// ============================================================================

/** 后端 schemas.CaseHistoryEntry（6 核心字段 + 5 新增详情字段） */
export interface CaseHistoryEntry {
  run_id: string;
  version: string;
  passed: boolean;
  score: number | null;
  run_at: ISOString;
  latency_ms: number | null;
  expected_answer: string | null;
  actual_answer: string | null;
  error_message: string | null;
  retrieved_memories: string | null;
}

/** 后端 schemas.CaseHistoryResponse */
export interface CaseHistoryResponse {
  case_id: string;
  name: string;
  history: CaseHistoryEntry[];
}

/** Legacy-compat CaseHistoryItem — 旧组件/表格用了这组 key（所有字段兼容别名填充） */
export interface CaseHistoryItem {
  id: number;                            // 序号（前端数组 index 填充）
  run_id: string;
  run_name: string;                      // "<version> · <phase>"（前端根据 version 拼接，后端没有该字段）
  run_version: string;                   // alias of version
  start_time: ISOString;                 // alias of run_at
  passed: boolean;
  latency_ms: number | null;
  actual_output: string | null;          // alias of actual_answer
  judge_reason: string | null;           // alias of error_message / retrieved_memories
  tokens_input: number | null;           // 真实表里不存在；null
  tokens_output: number | null;          // 同上
}

// ============================================================================
// Stats 领域
// ============================================================================

/** 后端 schemas.LatestRun */
export interface LatestRun {
  version: string;
  phase: PhaseKey;
  pass_rate: number;
  run_at: ISOString;
}

/** 后端 schemas.ByVersionStat */
export interface ByVersionStat {
  runs: number;
  avg_pass_rate: number;
}

/** 后端 schemas.FailingCase（真实字段 + compat 别名） */
export interface FailingCase {
  // --- Rich fields (exposed by stats.py) ---
  case_id: string;
  name: string;
  category: PhaseKey;
  last_result: 'passed' | 'failed';
  fail_count: number;
  pass_count: number;
  last_run_id: string | null;
  last_run_version: string | null;
  last_run_time: ISOString | null;
  last_passed: boolean | null;
  phase: PhaseKey;                       // compat alias = category (PhaseBadge 需要它)

  // --- Legacy compat fields (filled by both backend + statsApi adapter for safety) ---
  case_name?: string;                    // alias of name
  last_run_name?: string | null;         // compat: fill with version（没有真实 run_name 列）
}

/** 后端 schemas.OverviewStats */
export interface OverviewStats {
  total_runs: number;
  total_cases: number;
  latest_run: LatestRun | null;
  by_version: Record<string, ByVersionStat>;
  case_categories: Record<string, number>;
  failing_cases: FailingCase[];
}

/** 后端 schemas.TrendPoint */
export interface TrendPoint {
  run_id: string;
  name: string;
  version: string;
  start_time: ISOString;
  pass_rate: number;
  total_cases: number;
  passed: number;
  failed: number;
  phase: PhaseKey | null;
}

/** 后端 schemas.CategoryStat */
export interface CategoryStat {
  phase: PhaseKey;                       // name kept for legacy TS (实际存储 category 值)
  total: number;
  passed: number;
  failed: number;
  pass_rate: number;
  avg_latency_ms: number;
}

/** 后端 schemas.LongestPhaseStat */
export interface LongestPhaseStat {
  phase: PhaseKey;
  avg_latency_ms: number;
}

/** 后端 schemas.DashboardStats — Dashboard 一次拉取聚合数据 */
export interface DashboardStats {
  total_runs: number;
  total_cases: number;
  total_pass_rate: number;
  recent_7_days: number;
  longest_phase: LongestPhaseStat | null;
  failing_cases: FailingCase[];
  recent_runs: RunSummary[];
  trend: TrendPoint[];
  by_category: CategoryStat[];
}

// ============================================================================
// 通用分页（cases/runs 列表接口使用）
// ============================================================================

export interface Paginated<T> {
  items: T[];
  total: number;
  page: number;
  limit: number;
  pages: number;
}

