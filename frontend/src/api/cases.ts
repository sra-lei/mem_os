import { apiClient } from './client';
import type { CaseDefinition, CaseHistoryItem, Paginated, PhaseKey } from '@/types';

export interface CasesListQuery {
  page?: number;
  limit?: number;
  search?: string;
  phase?: PhaseKey | null;
  tag?: string | null;
  sortBy?: string;
  sortDir?: 'asc' | 'desc';
}

export const casesApi = {
  list: (q: CasesListQuery, signal?: AbortSignal) =>
    apiClient.get<Paginated<CaseDefinition>>('/api/cases', q as unknown as Record<string, unknown>, { signal }),

  tags: (signal?: AbortSignal) => apiClient.get<string[]>('/api/cases/tags', undefined, { signal }),

  history: (caseDefId: number | string, signal?: AbortSignal) =>
    apiClient.get<CaseHistoryItem[]>(`/api/cases/${caseDefId}/history`, undefined, { signal, cache: false }),

  detail: (caseDefId: number | string, signal?: AbortSignal) =>
    apiClient.get<CaseDefinition>(`/api/cases/${caseDefId}`, undefined, { signal }),
};
