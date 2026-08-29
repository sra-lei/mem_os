/**
 * 用例列表：分页 + 筛选 + 排序。
 */
import { useMemo } from 'react';
import { useAsync } from './useAsync';
import { casesApi } from '@/api/cases';
import type { CasesListQuery } from '@/api/cases';

export function useCases(query: CasesListQuery) {
  const { data, loading, error, refresh } = useAsync(
    (signal) => casesApi.list(query, signal),
    [query.page, query.limit, query.search, query.phase, query.tag, query.sortBy, query.sortDir],
  );

  const tagsAsync = useAsync((signal) => casesApi.tags(signal), []);

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
    tags: tagsAsync.data ?? [],
    tagsLoading: tagsAsync.loading,
    loading,
    error,
    refresh,
  };
}
