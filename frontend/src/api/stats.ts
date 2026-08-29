import { apiClient } from './client';
import type { DashboardStats, CategoryStat, TrendPoint } from '@/types';

export const statsApi = {
  dashboard: (signal?: AbortSignal) =>
    apiClient.get<DashboardStats>('/api/stats/dashboard', undefined, { signal }),

  trend: (limit?: number, signal?: AbortSignal) =>
    apiClient.get<TrendPoint[]>('/api/stats/trend', limit ? { limit } : undefined, { signal }),

  byCategory: (runId?: number | string, signal?: AbortSignal) =>
    apiClient.get<CategoryStat[]>(
      runId != null ? `/api/stats/by-category/${runId}` : '/api/stats/by-category',
      undefined,
      { signal },
    ),
};
