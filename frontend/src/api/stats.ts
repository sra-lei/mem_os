import { apiClient } from './client';
import type { DashboardStats, CategoryStat, TrendPoint, FailingCase, RunSummary } from '@/types';

/**
 * Ensure every FailingCase carries the legacy compat aliases that React cards use.
 * The backend fills these too; we keep a client-side fallback so data survives
 * even if older code-paths / future endpoints omit the compat keys.
 */
function fillFailingCaseCompat(fc: FailingCase): FailingCase {
  if (!fc) return fc;
  const raw = fc as unknown as Record<string, unknown>;
  raw.case_name ??= fc.name;
  raw.last_run_name ??= fc.last_run_version ?? null;
  raw.phase ??= fc.category;
  return fc;
}

/**
 * Ensure every RunSummary carries the 4 compat aliases. Backend routes/runs.py &
 * routes/stats.py already fill these; keep a client-side backup for robustness.
 */
function fillRunSummaryCompat(r: RunSummary): RunSummary {
  if (!r) return r;
  const raw = r as unknown as Record<string, unknown>;
  raw.name ??= `${r.version} · ${r.phase}`;
  if (raw.failed == null) raw.failed = Math.max(0, (r.total_cases ?? 0) - (r.passed_count ?? 0));
  raw.passed ??= r.passed_count ?? 0;
  raw.start_time ??= r.run_at;
  // end_time: we only derive it when duration_seconds is numeric; leave null otherwise
  if (raw.end_time == null && r.run_at && typeof r.duration_seconds === 'number') {
    try {
      const endMs = new Date(r.run_at).getTime() + r.duration_seconds * 1000;
      if (Number.isFinite(endMs)) raw.end_time = new Date(endMs).toISOString();
    } catch { /* ignore */ }
  }
  return r;
}

function adaptDashboard(d: DashboardStats): DashboardStats {
  if (!d) return d;
  d.failing_cases = (d.failing_cases ?? []).map(fillFailingCaseCompat);
  d.recent_runs = (d.recent_runs ?? []).map(fillRunSummaryCompat);
  // trend & by_category are already built with TS-friendly keys; no adapt needed
  return d;
}

export const statsApi = {
  dashboard: (signal?: AbortSignal) =>
    apiClient.get<DashboardStats>('/api/stats/dashboard', undefined, { signal }).then(adaptDashboard),

  trend: (limit?: number, signal?: AbortSignal) =>
    apiClient.get<TrendPoint[]>('/api/stats/trend', limit ? { limit } : undefined, { signal }),

  byCategory: (runId?: number | string, signal?: AbortSignal) =>
    apiClient.get<CategoryStat[]>(
      runId != null ? `/api/stats/by-category/${runId}` : '/api/stats/by-category',
      undefined,
      { signal },
    ),
};
