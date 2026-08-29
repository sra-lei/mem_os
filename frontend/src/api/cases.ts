import { apiClient } from './client';
import type {
  CaseDefinition,
  CaseHistoryItem,
  CaseHistoryResponse,
  Paginated,
  PhaseKey,
} from '@/types';

export interface CasesListQuery {
  page?: number;
  limit?: number;
  search?: string;
  /** UI "阶段" — maps to backend param `category` (PhaseKey) */
  phase?: PhaseKey | null;
  /** UI "目标版本" — maps to backend param `version_target` */
  version?: string | null;
  /** UI "用例层级" (layer1/layer2/layer3) — maps to backend param `source_layer` */
  layer?: string | null;
  /** Single tag filter (JSON array containment) */
  tag?: string | null;
  sortBy?: string;
  sortDir?: 'asc' | 'desc';
}

/** Backend returns flat CaseDefinition[]; UI contract expects Paginated. */
type BackendCaseList = CaseDefinition[];

function parseTagsStr(tags: string | null | undefined): string[] {
  if (!tags) return [];
  try {
    const parsed = JSON.parse(tags);
    if (Array.isArray(parsed)) return parsed.map((x: unknown) => String(x));
  } catch { /**/ }
  return [];
}

const LAYER_RE = /layer[1-3]/;
/** Derive layer1/2/3 label from CaseDefinition.source_path (compat field for table rendering). */
function layerOfSource(sp: string | null | undefined): string | null {
  if (!sp) return null;
  const m = LAYER_RE.exec(sp);
  return m ? m[0] : null;
}

/** Fill compat aliases on a CaseDefinition so legacy UI keys keep working.
 *  NEW: also fills `version` compat = version_target (table col label "Version")
 *       and `layer` compat = derived from source_path */
function fillCaseCompat(c: CaseDefinition): CaseDefinition {
  if (!c) return c;
  const raw = c as unknown as Record<string, unknown>;
  raw.id ??= 0; // legacy number-id placeholder — UI uses case_id for routing anyway
  raw.case_name ??= c.name;
  raw.phase ??= c.category;
  raw.retrieval_memory ??= null;
  raw.user_input ??= c.query ?? null;
  raw.expected_output ??= c.expected_answer ?? null;
  // tags: replace JSON string with a real array for the legacy TS tags: string[]
  raw.tags = parseTagsStr(c.tags as unknown as string);
  // New display helpers (for new table cols)
  raw.version ??= c.version_target ?? null;
  raw.layer ??= layerOfSource(c.source_path);
  return c;
}

function adaptCaseList(
  raw: BackendCaseList,
  query: CasesListQuery,
): Paginated<CaseDefinition> {
  const page = query.page ?? 1;
  const limit = query.limit ?? 20;
  const items = raw.map(fillCaseCompat);
  const total = items.length;
  // Backend /cases currently has no server-side pagination — it returns all rows.
  // We keep the full list as `items` to support client-side sorting/filtering and
  // only trim the paginated slice info on Paginated pages/points for UI rendering:
  return {
    items,
    total,
    page,
    limit,
    pages: limit > 0 ? Math.ceil(total / limit) : 0,
  };
}

/** Map CaseHistoryEntry (backend) → CaseHistoryItem (legacy UI contract). */
function adaptHistoryItem(
  entry: CaseHistoryResponse['history'][number],
  index: number,
): CaseHistoryItem {
  return {
    id: index + 1,
    run_id: entry.run_id,
    run_name: `${entry.version}`,
    run_version: entry.version,
    start_time: entry.run_at,
    passed: entry.passed,
    latency_ms: entry.latency_ms ?? null,
    actual_output: entry.actual_answer ?? null,
    judge_reason: entry.error_message ?? entry.retrieved_memories ?? null,
    tokens_input: null,
    tokens_output: null,
  };
}

export const casesApi = {
  list: (q: CasesListQuery, signal?: AbortSignal) => {
    const backendQuery: Record<string, unknown> = {
      category: q.phase ?? undefined,          // phase (UI) → category (DB col)
      version_target: q.version ?? undefined,  // version (UI) → version_target (DB col)
      source_layer: q.layer ?? undefined,      // layer (UI) → source_layer (derived LIKE)
      search: q.search ?? undefined,
      tag: q.tag ?? undefined,
    };
    return apiClient
      .get<BackendCaseList>('/api/cases', backendQuery, { signal })
      .then((raw) => adaptCaseList(raw, q));
  },

  tags: (signal?: AbortSignal) =>
    apiClient.get<string[]>('/api/cases/tags', undefined, { signal }),

  versions: (signal?: AbortSignal) =>
    apiClient.get<string[]>('/api/cases/versions', undefined, { signal }),

  layers: (signal?: AbortSignal) =>
    apiClient.get<string[]>('/api/cases/layers', undefined, { signal }),

  history: (caseDefId: number | string, signal?: AbortSignal) =>
    apiClient
      .get<CaseHistoryResponse>(`/api/cases/${caseDefId}/history`, undefined, { signal, cache: false })
      .then((resp) => (resp.history ?? []).map((h, idx) => adaptHistoryItem(h, idx))),

  detail: (caseDefId: number | string, signal?: AbortSignal) =>
    apiClient
      .get<CaseDefinition>(`/api/cases/${caseDefId}`, undefined, { signal })
      .then(fillCaseCompat),
};
