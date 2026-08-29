/**
 * 运行详情：汇总 + 结果。
 *  - 通过聚合 /api/runs/{id} 返回的 detail 直接用；不再单独再拉一次 results
 *  - 额外统计分类数据
 */
import { useMemo } from 'react';
import { useAsync } from './useAsync';
import { runsApi } from '@/api/runs';
import { statsApi } from '@/api/stats';
import type { CategoryStat, PhaseKey, TestCaseResult } from '@/types';

export interface UseRunDetailResult {
  run: ReturnType<typeof useAsync>['data'];
  results: TestCaseResult[];
  byCategory: CategoryStat[];
  byPhase: Record<PhaseKey | 'all', TestCaseResult[]>;
  loading: boolean;
  error: Error | null;
  refresh: () => Promise<void>;
}

export function useRunDetail(runId: number | string | undefined) {
  const detailAsync = useAsync(
    (signal) => (runId != null && runId !== '' ? runsApi.detail(runId, signal) : Promise.resolve(null)),
    [runId],
  );
  const categoryAsync = useAsync(
    (signal) =>
      runId != null && runId !== ''
        ? statsApi.byCategory(runId, signal)
        : Promise.resolve<CategoryStat[]>([]),
    [runId],
  );

  const loading = detailAsync.loading || categoryAsync.loading;
  const error = detailAsync.error || categoryAsync.error;

  const results: TestCaseResult[] = detailAsync.data?.results ?? [];

  const byPhase = useMemo(() => {
    const grouped = {} as Record<PhaseKey | 'all', TestCaseResult[]>;
    grouped.all = results;
    for (const r of results) {
      (grouped[r.phase] ||= []).push(r);
    }
    return grouped;
  }, [results]);

  const refresh = async (): Promise<void> => {
    await Promise.all([detailAsync.refresh(), categoryAsync.refresh()]);
  };

  return {
    run: detailAsync.data?.run ?? null,
    detail: detailAsync.data,
    results,
    byCategory: categoryAsync.data ?? [],
    byPhase,
    loading,
    error,
    refresh,
  };
}
