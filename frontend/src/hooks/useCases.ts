/**
 * 用例列表：分页 + 筛选 + 排序 + 字典（tags / versions / layers）。
 */
import { useMemo } from 'react';
import { useAsync } from './useAsync';
import { casesApi } from '@/api/cases';
import type { CasesListQuery } from '@/api/cases';

export function useCases(query: CasesListQuery) {
  const { data, loading, error, refresh } = useAsync(
    (signal) => casesApi.list(query, signal),
    [query.page, query.limit, query.search, query.phase, query.version, query.layer, query.tag, query.sortBy, query.sortDir],
  );

  const tagsAsync = useAsync((signal) => casesApi.tags(signal), []);
  const versionsAsync = useAsync((signal) => casesApi.versions(signal), []);
  const layersAsync = useAsync((signal) => casesApi.layers(signal), []);

  const meta = useMemo(() => {
    const total = data?.total ?? 0;
    const limit = query.limit ?? 20;
    const page = query.page ?? 1;
    const pages = Math.max(1, Math.ceil(total / limit));
    return { total, pages, page, limit };
  }, [data, query.limit, query.page]);

  const anyDictLoading = tagsAsync.loading || versionsAsync.loading || layersAsync.loading;

  return {
    items: data?.items ?? [],
    meta,
    // Dictionaries for filter dropdowns
    tags: tagsAsync.data ?? [],
    versions: versionsAsync.data ?? [],
    layers: layersAsync.data ?? [],
    tagsLoading: tagsAsync.loading,
    versionsLoading: versionsAsync.loading,
    layersLoading: layersAsync.loading,
    anyDictLoading,
    loading,
    error,
    refresh,
  };
}
