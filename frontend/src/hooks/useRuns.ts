/**
 * 运行列表：分页 + 筛选 + 排序。
 * 由调用方（页面）持有 query state，hook 只做拉取。
 */
import { useMemo } from 'react';
import { useAsync } from './useAsync';
import { runsApi } from '@/api/runs';
import type { RunsListQuery } from '@/api/runs';

export function useRuns(query: RunsListQuery) {
  const { data, loading, error, refresh } = useAsync(
    (signal) => runsApi.list(query, signal),
    [
      query.page,
      query.limit,
      query.search,
      query.phase,
      query.version,
      query.status,
      query.sortBy,
      query.sortDir,
    ],
  );

  const meta = useMemo(() => {
    const total = data?.total ?? 0;
    const limit = query.limit ?? 20;
    const page = query.page ?? 1;
    const pages = Math.max(1, Math.ceil(total / limit));
    return { total, pages, page, limit };
  }, [data, query.limit, query.page]);

  return {
    items: data?.items ?? [],
    meta,
    loading,
    error,
    refresh,
  };
}
