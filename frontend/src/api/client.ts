/**
 * 底层 fetch 封装：
 *  - 统一 BaseURL（生产阶段走同源相对路径，开发走 Vite 代理）
 *  - 统一错误解析：非 2xx → 抛出 ApiError
 *  - 可选内存级 GET 缓存：相同 URL + query 在 ttl 内不重复请求
 *  - AbortSignal 透传
 */
import type { ApiErrorBody } from '@/types/api';
import { ApiError } from '@/types/api';

// 留一层常量，未来 Phase 4 接真实环境变量时改一处即可
const BASE = (import.meta.env.VITE_API_BASE as string | undefined) ?? '';
const DEFAULT_TTL_MS = 30_000;
const DEFAULT_TIMEOUT_MS = 45_000;

interface CacheEntry {
  value: unknown;
  expiresAt: number;
}

const cache = new Map<string, CacheEntry>();

export function clearApiCache(): void {
  cache.clear();
}

export function invalidateApiCachePrefix(prefix: string): void {
  for (const k of Array.from(cache.keys())) {
    if (k.startsWith(prefix)) cache.delete(k);
  }
}

function buildUrl(path: string, query?: Record<string, unknown>): string {
  let url = BASE + path;
  if (query && Object.keys(query).length > 0) {
    const usp = new URLSearchParams();
    for (const [k, v] of Object.entries(query)) {
      if (v == null || v === '') continue;
      if (Array.isArray(v)) {
        for (const item of v) usp.append(k, String(item));
      } else {
        usp.append(k, String(v));
      }
    }
    const qs = usp.toString();
    if (qs) url += `?${qs}`;
  }
  return url;
}

async function readJsonSafe<T = unknown>(res: Response): Promise<T> {
  const text = await res.text();
  if (!text) return undefined as unknown as T;
  try {
    return JSON.parse(text) as T;
  } catch {
    // 非 JSON 响应（如 HTML 错误页）也包一层，方便 UI 呈现
    return { detail: text } as unknown as T;
  }
}

async function request<T>(
  method: 'GET' | 'POST' | 'PUT' | 'DELETE',
  path: string,
  options: {
    query?: Record<string, unknown>;
    body?: unknown;
    signal?: AbortSignal;
    cache?: boolean;
    ttlMs?: number;
  } = {},
): Promise<T> {
  const url = buildUrl(path, method === 'GET' ? options.query : undefined);
  const cacheKey = method + '::' + url;
  const useCache = method === 'GET' && options.cache !== false;

  if (useCache) {
    const hit = cache.get(cacheKey);
    if (hit && hit.expiresAt > Date.now()) {
      return hit.value as T;
    }
  }

  // 超时控制器：如果外部没传 signal，则自建一个带超时的
  let internalCtrl: AbortController | null = null;
  let signal: AbortSignal | undefined = options.signal;
  if (!signal) {
    internalCtrl = new AbortController();
    signal = internalCtrl.signal;
  }
  const timeoutId = internalCtrl
    ? setTimeout(() => internalCtrl!.abort(new DOMException('Timeout', 'TimeoutError')), DEFAULT_TIMEOUT_MS)
    : 0;

  let res: Response;
  try {
    const init: RequestInit = {
      method,
      headers:
        options.body != null
          ? { 'Content-Type': 'application/json' }
          : undefined,
      body: options.body != null ? JSON.stringify(options.body) : undefined,
      signal,
    };
    res = await fetch(url, init);
  } catch (err) {
    if (timeoutId) clearTimeout(timeoutId);
    if ((err as Error)?.name === 'AbortError' || (err as Error)?.name === 'TimeoutError') {
      throw err;
    }
    throw new ApiError(0, url, { detail: (err as Error)?.message ?? '网络错误' }, '网络请求失败');
  }
  if (timeoutId) clearTimeout(timeoutId);

  const body = await readJsonSafe<T | ApiErrorBody>(res);
  if (!res.ok) {
    const errBody = (body as ApiErrorBody) ?? {};
    throw new ApiError(
      res.status,
      url,
      errBody,
      `HTTP ${res.status} ${res.statusText}`,
    );
  }
  const typed = body as T;
  if (useCache) {
    cache.set(cacheKey, {
      value: typed,
      expiresAt: Date.now() + (options.ttlMs ?? DEFAULT_TTL_MS),
    });
  }
  return typed;
}

export const apiClient = {
  get: <T>(path: string, query?: Record<string, unknown>, opts: { signal?: AbortSignal; cache?: boolean; ttlMs?: number } = {}) =>
    request<T>('GET', path, { query, ...opts }),
  post: <T>(path: string, body?: unknown, opts: { signal?: AbortSignal } = {}) =>
    request<T>('POST', path, { body, ...opts }),
  put: <T>(path: string, body?: unknown, opts: { signal?: AbortSignal } = {}) =>
    request<T>('PUT', path, { body, ...opts }),
  delete: <T>(path: string, query?: Record<string, unknown>, opts: { signal?: AbortSignal } = {}) =>
    request<T>('DELETE', path, { query, ...opts }),
};
