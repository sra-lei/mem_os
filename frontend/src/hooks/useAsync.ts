/**
 * 通用异步请求 hook：
 *  - 统一 loading / error / data 三元组
 *  - 自动处理 AbortController，卸载取消请求
 *  - deps 变化自动重新加载
 */
import { useEffect, useMemo, useRef, useState } from 'react';
import type { ApiError } from '@/types/api';

export interface UseAsyncResult<T> {
  data: T | null;
  loading: boolean;
  error: ApiError | Error | null;
  /** 强制刷新；可选临时覆盖一次 fetcher 依赖的 args */
  refresh: () => Promise<T | undefined>;
}

export function useAsync<T>(
  fetcher: (signal: AbortSignal) => Promise<T>,
  deps: React.DependencyList = [],
): UseAsyncResult<T> {
  const [data, setData] = useState<T | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<ApiError | Error | null>(null);
  const seqRef = useRef(0);

  const refresh = useMemo(
    () => async (): Promise<T | undefined> => {
      const mySeq = ++seqRef.current;
      const ctrl = new AbortController();
      setLoading(true);
      setError(null);
      try {
        const result = await fetcher(ctrl.signal);
        if (mySeq !== seqRef.current) return undefined;
        setData(result);
        setLoading(false);
        return result;
      } catch (err) {
        if (mySeq !== seqRef.current) return undefined;
        if ((err as Error)?.name === 'AbortError') {
          setLoading(false);
          return undefined;
        }
        setError(err as ApiError | Error);
        setLoading(false);
      }
      return undefined;
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    deps,
  );

  useEffect(() => {
    const p = refresh();
    return () => {
      seqRef.current++; // 使上一次请求的结果被丢弃
      void p; // 仅为让 linter 保持 quiet，不阻塞卸载
    };
  }, [refresh]);

  return { data, loading, error, refresh };
}
