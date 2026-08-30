import { apiClient } from './client';
import type { RunSummary, RunDetail, TestCaseResult } from '@/types';

export interface RunsListQuery {
  page?: number;
  limit?: number;
  search?: string;
  phase?: string;
  version?: string;
  status?: string;
  sortBy?: string;
  sortDir?: 'asc' | 'desc';
}

export interface RunsListResponse {
  items: RunSummary[];
  total: number;
  page: number;
  limit: number;
  pages: number;
}

/** Flat object returned by the backend; we wrap it for the paginated UI contract. */
interface BackendRunListResponse {
  runs: RunSummary[];
  total: number;
}

/** Backend RunDetail is FLAT (extends RunSummary + results); legacy UI nests {run, results}. */
interface BackendRunDetail extends RunSummary {
  results: TestCaseResult[];
}

/** Fill every TestCaseResult row with the compat aliases legacy components reference. */
function fillResultCompat(r: TestCaseResult): TestCaseResult {
  if (!r) return r;
  const tags = (r as unknown as Record<string, unknown>).tags;
  (r as unknown as Record<string, unknown>).retrieval_memory ??= r.retrieved_memories ?? null;
  (r as unknown as Record<string, unknown>).user_input ??= r.query ?? null;
  (r as unknown as Record<string, unknown>).expected_output ??= r.expected_answer ?? null;
  (r as unknown as Record<string, unknown>).actual_output ??= r.actual_answer ?? null;
  (r as unknown as Record<string, unknown>).judge_reason ??= r.error_message ?? r.evaluation_criteria ?? null;
  (r as unknown as Record<string, unknown>).phase ??= r.category;
  (r as unknown as Record<string, unknown>).tokens_input ??= null;
  (r as unknown as Record<string, unknown>).tokens_output ??= null;
  // tags in legacy TS is string[]; the real column is a JSON string. Parse lazily.
  if (typeof tags === 'string' && tags) {
    try { (r as unknown as Record<string, unknown>).tags = JSON.parse(tags); } catch { /**/ }
  } else {
    (r as unknown as Record<string, unknown>).tags ??= [];
  }
  return r;
}

function adaptRunList(raw: BackendRunListResponse, query: RunsListQuery): RunsListResponse {
  const limit = query.limit ?? 20;
  const page = query.page ?? 1;
  const total = raw.total ?? 0;
  const items = raw.runs ?? [];
  return {
    items,
    total,
    page,
    limit,
    pages: limit > 0 ? Math.ceil(total / limit) : 0,
  };
}

function adaptRunDetail(flat: BackendRunDetail): RunDetail {
  const results = (flat.results ?? []).map(fillResultCompat);
  const run: RunSummary = {
    id: flat.id,
    version: flat.version,
    phase: flat.phase,
    run_at: flat.run_at,
    total_cases: flat.total_cases,
    passed_count: flat.passed_count,
    pass_rate: flat.pass_rate,
    duration_seconds: flat.duration_seconds,
    notes: flat.notes,
    triggered_by: flat.triggered_by,
    status: flat.status,
    progress: flat.progress,
    config_snapshot: flat.config_snapshot,
    name: flat.name,
    failed: flat.failed,
    start_time: flat.start_time,
    end_time: flat.end_time,
    passed: flat.passed,
  };
  return { ...(run as unknown as object), results } as unknown as RunDetail;
}

export const runsApi = {
  list: (q: RunsListQuery, signal?: AbortSignal) => {
    const page = q.page ?? 1;
    const limit = q.limit ?? 20;
    const backendQuery: Record<string, unknown> = {
      version: q.version,
      phase: q.phase,
      limit,
      offset: Math.max(0, (page - 1) * limit),
    };
    return apiClient
      .get<BackendRunListResponse>('/api/runs', backendQuery, { signal })
      .then((raw) => adaptRunList(raw, q));
  },

  detail: (id: number | string, signal?: AbortSignal) =>
    apiClient
      .get<BackendRunDetail>(`/api/runs/${id}`, undefined, { signal, cache: false })
      .then(adaptRunDetail),

  results: (id: number | string, signal?: AbortSignal) =>
    apiClient
      .get<TestCaseResult[]>(`/api/runs/${id}/results`, undefined, { signal, cache: false })
      .then((rows) => rows.map(fillResultCompat)),

  versions: (signal?: AbortSignal) =>
    apiClient.get<string[]>('/api/runs/versions', undefined, { signal }),

  /** Delete one run together with its case results. */
  delete: (id: number | string) =>
    apiClient.delete<{ run_id: string; deleted_results: number }>(`/api/runs/${id}`),

  /** Delete ALL runs and case results (irreversible). */
  clearAll: () =>
    apiClient.delete<{ deleted_runs: number; deleted_results: number }>('/api/runs'),
};
