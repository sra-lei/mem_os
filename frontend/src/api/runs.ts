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

export const runsApi = {
  list: (q: RunsListQuery, signal?: AbortSignal) =>
    apiClient.get<RunsListResponse>('/api/runs', q as unknown as Record<string, unknown>, { signal }),

  detail: (id: number | string, signal?: AbortSignal) =>
    apiClient.get<RunDetail>(`/api/runs/${id}`, undefined, { signal, cache: false }),

  results: (id: number | string, signal?: AbortSignal) =>
    apiClient.get<TestCaseResult[]>(`/api/runs/${id}/results`, undefined, { signal, cache: false }),

  versions: (signal?: AbortSignal) =>
    apiClient.get<string[]>('/api/runs/versions', undefined, { signal }),
};
