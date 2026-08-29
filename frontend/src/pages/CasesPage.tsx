import { useEffect, useMemo, useState } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import { useCases } from '@/hooks/useCases';
import { CasesTable } from '@/components/Tables';
import { Button, Skeleton, TableSkeleton, useAsyncErrorToast } from '@/components/UI';
import { PageHeader, Pager } from './DashboardPage';
import type { SortDir } from '@/utils/sort';
import type { CasesListQuery } from '@/api/cases';
import type { PhaseKey } from '@/types';

const PHASE_OPTIONS: Array<{ value: string; label: string }> = [
  { value: '', label: '全部阶段' },
  { value: 'memory_retrieval', label: '记忆检索' },
  { value: 'prompt_generation', label: 'Prompt 生成' },
  { value: 'answer_generation', label: '回答生成' },
];

export function CasesPage() {
  const [params, setParams] = useSearchParams();
  const navigate = useNavigate();

  const query = useMemo<CasesListQuery>(() => {
    const page = Number(params.get('page') ?? 1);
    const limit = Number(params.get('limit') ?? 20);
    return {
      page: Number.isFinite(page) && page > 0 ? page : 1,
      limit: [20, 50, 100].includes(limit) ? limit : 20,
      search: params.get('q') ?? '',
      phase: (params.get('phase') as PhaseKey | null) || null,
      tag: params.get('tag') || null,
      sortBy: params.get('sortBy') ?? 'total_runs',
      sortDir: (params.get('sortDir') as SortDir) ?? 'desc',
    };
  }, [params]);

  const { items, meta, tags, loading, error, tagsLoading } = useCases(query);
  useAsyncErrorToast(error, '加载用例列表失败');

  const [draft, setDraft] = useState({
    q: query.search,
    phase: query.phase ?? '',
    tag: query.tag ?? '',
  });

  useEffect(() => {
    setDraft({
      q: query.search,
      phase: query.phase ?? '',
      tag: query.tag ?? '',
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [query.search, query.phase, query.tag]);

  const applyFilter = (patch?: Partial<typeof draft>) => {
    const next = { ...draft, ...patch };
    setDraft(next);
    const np = new URLSearchParams();
    np.set('page', '1');
    np.set('limit', String(meta.limit));
    if (next.q) np.set('q', next.q);
    if (next.phase) np.set('phase', next.phase);
    if (next.tag) np.set('tag', next.tag);
    np.set('sortBy', query.sortBy ?? 'total_runs');
    np.set('sortDir', query.sortDir ?? 'desc');
    navigate({ search: np.toString() });
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
        eyebrow="Case Library"
        title="用例定义"
        desc="覆盖各阶段的测试用例库，查看历史通过率与最近更新，点击「历史」可查看同一用例在不同版本中的表现。"
        right={<Button variant="secondary">🧭 批量导入（Phase 4）</Button>}
      />

      <section className="card">
        <div className="filter-bar">
          <input
            className="input input--search"
            placeholder="搜索用例名 / Case ID / 输入内容…"
            value={draft.q}
            onChange={(e) => setDraft((d) => ({ ...d, q: e.target.value }))}
            onKeyDown={(e) => e.key === 'Enter' && applyFilter()}
          />
          <select
            className="select"
            value={draft.phase}
            onChange={(e) => applyFilter({ phase: e.target.value })}
          >
            {PHASE_OPTIONS.map((o) => (
              <option key={o.value || 'all'} value={o.value}>{o.label}</option>
            ))}
          </select>
          <select
            className="select"
            value={draft.tag}
            disabled={tagsLoading}
            onChange={(e) => applyFilter({ tag: e.target.value })}
          >
            <option value="">全部标签</option>
            {tags.map((t) => (
              <option key={t} value={t}>{t}</option>
            ))}
          </select>
          <Button variant="primary" onClick={() => applyFilter()}>应用</Button>
          <Button
            variant="ghost"
            onClick={() => {
              setDraft({ q: '', phase: '', tag: '' });
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

        {loading ? <TableSkeleton cols={8} rows={Math.min(12, meta.limit)} /> : <CasesTable items={items} sortKey={query.sortBy} sortDir={query.sortDir} onSort={onSort} />}

        <Pager page={meta.page} pages={meta.pages} total={meta.total} limit={meta.limit} onChange={onPage} />
      </section>

      {!loading && tagsLoading ? <Skeleton /> : null}
    </div>
  );
}
