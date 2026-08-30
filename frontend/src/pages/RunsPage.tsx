import { useEffect, useMemo, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { useAsync } from '@/hooks/useAsync';
import { useRuns } from '@/hooks/useRuns';
import { RunsTable } from '@/components/Tables';
import { runsApi } from '@/api/runs';
import { clearApiCache } from '@/api/client';
import type { SortDir } from '@/utils/sort';
import { Button, TableSkeleton, Skeleton, useAsyncErrorToast, useToast } from '@/components/UI';
import { PageHeader, Pager } from './DashboardPage';
import type { RunsListQuery } from '@/api/runs';
import type { RunSummary } from '@/types';

const STATUS_OPTIONS: Array<{ value: string; label: string }> = [
  { value: '', label: '全部状态' },
  { value: 'pending', label: '等待中' },
  { value: 'running', label: '运行中' },
  { value: 'completed', label: '已完成' },
  { value: 'failed', label: '失败' },
  { value: 'cancelled', label: '已取消' },
];

export function RunsPage() {
  const [params, setParams] = useSearchParams();

  const query = useMemo<RunsListQuery>(() => {
    const page = Number(params.get('page') ?? 1);
    const limit = Number(params.get('limit') ?? 20);
    return {
      page: Number.isFinite(page) && page > 0 ? page : 1,
      limit: [20, 50, 100].includes(limit) ? limit : 20,
      version: params.get('version') || undefined,
      status: params.get('status') || undefined,
      sortBy: params.get('sortBy') ?? 'start_time',
      sortDir: (params.get('sortDir') as SortDir) ?? 'desc',
    };
  }, [params]);

  // 清理已下线的筛选参数（搜索 q / 阶段 phase），避免旧书签残留
  useEffect(() => {
    if (!params.has('q') && !params.has('phase')) return;
    const np = new URLSearchParams(params);
    np.delete('q');
    np.delete('phase');
    setParams(np, { replace: true });
  }, [params, setParams]);

  const { items, meta, loading, error, refresh } = useRuns(query);
  useAsyncErrorToast(error, '加载运行列表失败');
  const toast = useToast();

  // 版本下拉
  const versionsAsync = useAsync((signal) => runsApi.versions(signal), []);
  useAsyncErrorToast(versionsAsync.error, '加载版本列表失败');

  // 删除 / 清空的进行中状态
  const [deletingId, setDeletingId] = useState<string | null>(null);
  const [clearing, setClearing] = useState(false);

  const gotoPage = (p: number) => {
    const np = new URLSearchParams(params);
    np.set('page', String(p));
    setParams(np, { replace: true });
  };

  const handleDelete = async (run: RunSummary) => {
    const ok = window.confirm(
      `确定删除运行记录「${run.name}」吗？\n` +
      `该运行下的 ${run.total_cases ?? 0} 条用例结果将一并删除，此操作不可恢复。`,
    );
    if (!ok) return;
    setDeletingId(String(run.id));
    try {
      const res = await runsApi.delete(run.id);
      clearApiCache();
      // 最后一页仅剩一条时，删除后回退一页，避免停留在空页
      if (items.length === 1 && meta.page > 1) {
        gotoPage(meta.page - 1);
      } else {
        await refresh();
      }
      toast.push({
        kind: 'success',
        title: '运行记录已删除',
        desc: `已删除 ${res.deleted_results} 条用例结果`,
      });
    } catch (err) {
      toast.error(err, '删除运行记录失败');
    } finally {
      setDeletingId(null);
    }
  };

  const handleClearAll = async () => {
    if (meta.total === 0) return;
    const ok = window.confirm(
      `确定清空全部 ${meta.total} 条运行记录吗？\n` +
      `所有运行记录及对应用例结果都将被删除，此操作不可恢复。`,
    );
    if (!ok) return;
    setClearing(true);
    try {
      const res = await runsApi.clearAll();
      clearApiCache();
      gotoPage(1);
      await refresh();
      toast.push({
        kind: 'success',
        title: '运行记录已清空',
        desc: `共删除 ${res.deleted_runs} 条运行、${res.deleted_results} 条用例结果`,
      });
    } catch (err) {
      toast.error(err, '清空运行记录失败');
    } finally {
      setClearing(false);
    }
  };

  // 下拉筛选即时生效（无需"应用"按钮）
  const onFilter = (key: 'version' | 'status', value: string) => {
    const np = new URLSearchParams(params);
    if (value) np.set(key, value);
    else np.delete(key);
    np.set('page', '1');
    setParams(np, { replace: true });
  };

  const onSort = (key: string, dir: SortDir) => {
    const np = new URLSearchParams(params);
    np.set('sortBy', key);
    np.set('sortDir', dir);
    setParams(np, { replace: true });
  };

  const onPage = (p: number) => {
    const np = new URLSearchParams(params);
    np.set('page', String(p));
    setParams(np, { replace: true });
  };

  const onLimit = (limit: number) => {
    const np = new URLSearchParams(params);
    np.set('limit', String(limit));
    np.set('page', '1');
    setParams(np, { replace: true });
  };

  return (
    <div className="page">
      <PageHeader
        eyebrow="Runs"
        title="运行记录"
        desc="所有历史运行的完整列表；支持按版本 / 状态筛选，点击任一运行查看详情。"
      />

      <section className="card">
        <div className="filter-bar">
          <select
            className="select"
            value={query.version ?? ''}
            disabled={versionsAsync.loading}
            onChange={(e) => onFilter('version', e.target.value)}
          >
            <option value="">全部版本</option>
            {versionsAsync.data?.map((v) => (
              <option key={v} value={v}>{v}</option>
            ))}
          </select>
          <select
            className="select"
            value={query.status ?? ''}
            onChange={(e) => onFilter('status', e.target.value)}
          >
            {STATUS_OPTIONS.map((o) => (
              <option key={o.value || 'all-st'} value={o.value}>{o.label}</option>
            ))}
          </select>
        </div>

        <div className="toolbar">
          <div className="muted">
            共 <strong>{meta.total}</strong> 条
          </div>
          <div className="flex items-center gap-8">
            <label className="muted">
              每页：
              <select
                className="select select--sm"
                value={String(meta.limit)}
                onChange={(e) => onLimit(Number(e.target.value))}
              >
                {[20, 50, 100].map((n) => (
                  <option key={n} value={String(n)}>{n}</option>
                ))}
              </select>
            </label>
            <Button
              variant="danger"
              size="sm"
              loading={clearing}
              disabled={meta.total === 0}
              onClick={handleClearAll}
            >
              清空全部
            </Button>
          </div>
        </div>

        {loading ? (
          <TableSkeleton cols={8} rows={Math.min(12, meta.limit)} />
        ) : (
          <RunsTable
            items={items}
            sortKey={query.sortBy}
            sortDir={query.sortDir}
            onSort={onSort}
            onDelete={handleDelete}
            deletingId={deletingId}
          />
        )}

        <Pager
          page={meta.page}
          pages={meta.pages}
          total={meta.total}
          limit={meta.limit}
          onChange={onPage}
        />
      </section>

      {!loading && versionsAsync.loading ? <Skeleton /> : null}
    </div>
  );
}
