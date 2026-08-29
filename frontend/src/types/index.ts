// 与后端 src/api/schemas.py 对齐的前端类型定义
// 命名保持后端 key 一致，避免来回转换

export type PhaseKey = 'memory_retrieval' | 'prompt_generation' | 'answer_generation' | 'all';

export type RunStatus = 'pending' | 'running' | 'completed' | 'failed' | 'cancelled';

export interface RunSummary {
  id: number;
  name: string;
  version: string;
  phase: PhaseKey | null;
  start_time: string;
  end_time: string | null;
  total_cases: number;
  passed: number;
  failed: number;
  pass_rate: number;
  status: RunStatus;
  progress: number | null;
  error_message: string | null;
}

export interface TestCaseResult {
  id: number;
  run_id: number;
  case_id: number;
  case_name: string;
  phase: PhaseKey;
  tags: string[];
  setup: string;
  retrieval_memory: string;
  system_prompt: string;
  user_input: string;
  expected_output: string;
  actual_output: string;
  passed: boolean;
  judge_reason: string;
  latency_ms: number;
  tokens_input: number;
  tokens_output: number;
  created_at: string;
}

export interface CaseDefinition {
  id: number;
  case_id: string;
  case_name: string;
  phase: PhaseKey;
  tags: string[];
  setup: string;
  retrieval_memory: string;
  system_prompt: string;
  user_input: string;
  expected_output: string;
  created_at: string;
  updated_at: string;
  total_runs: number;
  pass_count: number;
  fail_count: number;
}

export interface RunDetail {
  run: RunSummary;
  results: TestCaseResult[];
}

export interface FailingCase {
  case_id: number;
  case_name: string;
  phase: PhaseKey;
  fail_count: number;
  pass_count: number;
  last_run_id: number | null;
  last_run_name: string | null;
  last_run_version: string | null;
  last_run_time: string | null;
  last_passed: boolean | null;
}

export interface TrendPoint {
  run_id: number;
  name: string;
  version: string;
  start_time: string;
  pass_rate: number;
  total_cases: number;
  passed: number;
  failed: number;
  phase: PhaseKey | null;
}

export interface CategoryStat {
  phase: PhaseKey;
  total: number;
  passed: number;
  failed: number;
  pass_rate: number;
  avg_latency_ms: number;
}

export interface DashboardStats {
  total_runs: number;
  total_cases: number;
  total_pass_rate: number;
  recent_7_days: number;
  longest_phase: {
    phase: PhaseKey;
    avg_latency_ms: number;
  } | null;
  failing_cases: FailingCase[];
  recent_runs: RunSummary[];
  trend: TrendPoint[];
  by_category: CategoryStat[];
}

export interface Paginated<T> {
  items: T[];
  total: number;
  page: number;
  limit: number;
  pages: number;
}

export interface CaseHistoryItem {
  id: number;
  run_id: number;
  run_name: string;
  run_version: string;
  start_time: string;
  passed: boolean;
  latency_ms: number;
  actual_output: string;
  judge_reason: string;
  tokens_input: number;
  tokens_output: number;
}
