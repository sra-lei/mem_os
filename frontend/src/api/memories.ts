/**
 * 记忆管理 API 客户端（后端 routes/memories.py，prefix /api/memories）。
 * 与后端 schemas 严格对齐；写操作返回统一信封 MemWriteResponse。
 * 说明：列表 GET 一律 cache:false —— 写操作后 refresh 需拿到最新数据，
 * 不能吃 30s 内存缓存。
 */
import { apiClient } from './client';

// ---------------------------------------------------------------------------
// 类型（与 src/testing/api/schemas.py 对齐）
// ---------------------------------------------------------------------------

export interface MemoryUserSummary {
  user_id: string;
  fact_count: number;
  categories: Record<string, number>; // category -> 事实数
  message_count: number; // conv_messages 原文条数
  session_count: number; // conv_meta 会话行数
  conv_status: Record<string, number>; // status -> 会话数
  latest_activity: string | null;
}

export interface MemoryFact {
  id: string;
  user_id: string;
  fact: string;
  previous_fact: string;
  category: string;
  key: string;
  value: string;
  confidence: number;
  source_conversation_id: string;
  source_chunk_id: string;
  created_at: string;
  updated_at: string;
}

export interface MemoryFactListResponse {
  items: MemoryFact[];
  total: number;
}

export interface MemoryMessage {
  seq: number;
  content: string;
  contains_pii: boolean;
  masked_text: string;
  create_at: string;
}

export type ProjectionStatus = 'synced' | 'failed' | 'skipped';

export interface MemWriteResponse {
  operation: string;
  sqlite: boolean;
  projection: ProjectionStatus;
  warning: string | null;
  user_id: string | null;
  fact_id: string | null;
  affected: number | null;
}

export interface FactCreatePayload {
  category: string;
  key: string;
  fact: string;
  value: string;
  confidence: number;
  source_conversation_id?: string;
  source_chunk_id?: string;
}

export interface FactUpdatePayload {
  fact?: string;
  value?: string;
  confidence?: number;
}

const enc = encodeURIComponent;

// ---------------------------------------------------------------------------
// API
// ---------------------------------------------------------------------------

export const memoriesApi = {
  listUsers: () => apiClient.get<MemoryUserSummary[]>('/api/memories/users', undefined, { cache: false }),

  listFacts: (
    userId: string,
    q: { category?: string; search?: string; offset?: number; limit?: number } = {},
    signal?: AbortSignal,
  ) => {
    const query = {
      category: q.category ?? undefined,
      q: q.search ?? undefined,
      offset: q.offset ?? 0,
      limit: q.limit ?? 50,
    };
    return apiClient.get<MemoryFactListResponse>(
      `/api/memories/users/${enc(userId)}/facts`,
      query,
      { cache: false, signal },
    );
  },

  messages: (userId: string, conversationId: string) =>
    apiClient.get<MemoryMessage[]>(
      `/api/memories/users/${enc(userId)}/conversations/${enc(conversationId)}/messages`,
      undefined,
      { cache: false },
    ),

  createFact: (userId: string, payload: FactCreatePayload) =>
    apiClient.post<MemWriteResponse>(`/api/memories/users/${enc(userId)}/facts`, payload, {
      signal: undefined,
    }),

  updateFact: (userId: string, factId: string, payload: FactUpdatePayload) =>
    apiClient.patch<MemWriteResponse>(
      `/api/memories/users/${enc(userId)}/facts/${enc(factId)}`,
      payload,
    ),

  deleteFact: (userId: string, factId: string) =>
    apiClient.delete<MemWriteResponse>(`/api/memories/users/${enc(userId)}/facts/${enc(factId)}`),

  clearUser: (userId: string, resetConvMeta: boolean) =>
    apiClient.post<MemWriteResponse>(`/api/memories/users/${enc(userId)}/clear`, {
      reset_conv_meta: resetConvMeta,
    }),

  rebuildProjection: (userId: string) =>
    apiClient.post<MemWriteResponse>(`/api/memories/users/${enc(userId)}/rebuild-projection`),
};
