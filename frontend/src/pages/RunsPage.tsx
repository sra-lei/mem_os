import { useEffect, useMemo, useState } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import { useAsync } from '@/hooks/useAsync';
import { useRuns } from '@/hooks/useRuns';
import { RunsTable } from '@/components/Tables';
import { runsApi } from '@/api/runs';
import type { SortDir } from '@/utils/sort';
import { Button, TableSkeleton, Skeleton, useAsyncErrorToast } from '@/components/UI';
import { PageHeader, Pager } from './DashboardPage';
import type { RunsListQuery } from '@/api/runs';

const PHASE_OPTIONS: Array<{ value: string; label: string }> = [
  { value: '', label: '全部阶段' },
  { value: 'memory_retrieval', label: '记忆检索' },
  { value: 'prompt_generation', label: 'Prompt 生成' },
  { value: 'answer_generation', label: '回答生成' },
  { value: 'all', label: '全链路' },
];

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
  const navigate = useNavigate();

  const query = useMemo<RunsListQuery>(() => {
    const page = Number(params.get('page') ?? 1);
    const limit = Number(params.get('limit') ?? 20);
    return {
      page: Number.isFinite(page) && page > 0 ? page : 1,
      limit: [20, 50, 100].includes(limit) ? limit : 20,
      search: params.get('q') ?? '',
      phase: params.get('phase') || undefined,
      version: params.get('version') || undefined,
      status: params.get('status') || undefined,
      sortBy: params.get('sortBy') ?? 'start_time',
      sortDir: (params.get('sortDir') as SortDir) ?? 'desc',
    };
  }, [params]);

  const { items, meta, loading, error } = useRuns(query);
  useAsyncErrorToast(error, '加载运行列表失败');

  // 版本下拉
  const versionsAsync = useAsync((signal) => runsApi.versions(signal), []);
  useAsyncErrorToast(versionsAsync.error, '加载版本列表失败');

  // 本地表单状态（提交时才同步 URL）
  const [draft, setDraft] = useState({
    q: query.search,
    phase: query.phase ?? '',
    version: query.version ?? '',
    status: query.status ?? '',
  });
  useEffect(() => {
    setDraft({
      q: query.search,
      phase: query.phase ?? '',
      version: query.version ?? '',
      status: query.status ?? '',
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [query.search, query.phase, query.version, query.status]);

  const applyFilter = (patch?: Partial<typeof draft>) => {
    const next = { ...draft, ...patch };
    setDraft(next);
    const np = new URLSearchParams();
    np.set('page', '1');
    np.set('limit', String(meta.limit));
    if (next.q) np.set('q', next.q);
    if (next.phase) np.set('phase', next.phase);
    if (next.version) np.set('version', next.version);
    if (next.status) np.set('status', next.status);
    np.set('sortBy', query.sortBy ?? 'start_time');
    np.set('sortDir', query.sortDir ?? 'desc');
    navigate({ search: np.toString() }, { replace: false });
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
        desc="所有历史运行的完整列表；支持按版本 / 阶段 / 状态筛选，点击任一运行查看详情。"
      />

      <section className="card">
        <div className="filter-bar">
          <input
            className="input"
            placeholder="搜索运行名 / 版本 / 备注…"
            value={draft.q}
            onChange={(e) => setDraft((d) => ({ ...d, q: e.target.value }))}
            onKeyDown={(e) => {
              if (e.key === 'Enter') applyFilter();
            }}
          />
          <select className="select" value={draft.phase} onChange={(e) => applyFilter({ phase: e.target.value })}>
            {PHASE_OPTIONS.map((o) => (
              <option key={o.value || 'all-ph'} value={o.value}>{o.label}</option>
            ))}
          </select>
          <select
            className="select"
            value={draft.version}
            disabled={versionsAsync.loading}
            onChange={(e) => applyFilter({ version: e.target.value })}
          >
            <option value="">全部版本</option>
            {versionsAsync.data?.map((v) => (
              <option key={v} value={v}>{v}</option>
            ))}
          </select>
          <select className="select" value={draft.status} onChange={(e) => applyFilter({ status: e.target.value })}>
            {STATUS_OPTIONS.map((o) => (
              <option key={o.value || 'all-st'} value={o.value}>{o.label}</option>
            ))}
          </select>
          <Button variant="primary" onClick={() => applyFilter()}>应用筛选</Button>
          <Button
            variant="ghost"
            onClick={() => {
              setDraft({ q: '', phase: '', version: '', status: '' });
              navigate({ search: '' });
            }}
          >
            重置
          </Button>
        </div>

        <div className="toolbar">
          <div className="muted">
            共 <strong>{meta.total}</strong> 条
          </div>
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
        </div>

        {loading ? (
          <TableSkeleton cols={8} rows={Math.min(12, meta.limit)} />
        ) : (
          <RunsTable items={items} sortKey={query.sortBy} sortDir={query.sortDir} onSort={onSort} />
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
