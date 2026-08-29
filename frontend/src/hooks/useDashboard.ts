/**
 * 仪表盘：一次性聚合 + 懒加载 trend / by-category 备用接口。
 */
import { useAsync } from './useAsync';
import { statsApi } from '@/api/stats';
import type { DashboardStats } from '@/types';

export interface UseDashboardResult {
  stats: DashboardStats | null;
  loading: boolean;
  error: Error | null;
  refresh: () => Promise<DashboardStats | undefined>;
}

export function useDashboard(): UseDashboardResult {
  const { data, loading, error, refresh } = useAsync<DashboardStats>(
    (signal) => statsApi.dashboard(signal),
    [],
  );
  return { stats: data, loading, error, refresh };
}
