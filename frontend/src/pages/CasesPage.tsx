import { useEffect, useMemo } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import { useCases } from '@/hooks/useCases';
import { CasesTable } from '@/components/Tables';
import { Button, Skeleton, TableSkeleton, useAsyncErrorToast } from '@/components/UI';
import { PageHeader, Pager } from './DashboardPage';
import type { SortDir } from '@/utils/sort';
import type { CasesListQuery } from '@/api/cases';

const LAYER_LABEL: Record<string, string> = {
  layer1: 'L1 单场景',
  layer2: 'L2 多场景组合',
  layer3: 'L3 多领域复杂协同',
};

const VALID_SORT_KEYS = ['name', 'updated_at'];

export function CasesPage() {
  const [params, setParams] = useSearchParams();
  const navigate = useNavigate();

  const query = useMemo<CasesListQuery>(() => {
    const page = Number(params.get('page') ?? 1);
    const limit = Number(params.get('limit') ?? 20);
    const layerRaw = params.get('layer');
    const sortByRaw = params.get('sortBy');
    return {
      page: Number.isFinite(page) && page > 0 ? page : 1,
      limit: [20, 50, 100].includes(limit) ? limit : 20,
      layer: (layerRaw === 'layer1' || layerRaw === 'layer2' || layerRaw === 'layer3') ? layerRaw : null,
      sortBy: sortByRaw && VALID_SORT_KEYS.includes(sortByRaw) ? sortByRaw : 'updated_at',
      sortDir: (params.get('sortDir') as SortDir) ?? 'desc',
    };
  }, [params]);

  const {
    items, meta,
    layers, layersLoading,
    loading, error,
  } = useCases(query);
  useAsyncErrorToast(error, '加载用例列表失败');

  const currentLayer = query.layer ?? '';

  // 当 layer 通过下拉改变时，立即写入 URL（回到第 1 页）
  const onLayerChange = (layer: string) => {
    const np = new URLSearchParams(params);
    np.set('page', '1');
    if (layer) np.set('layer', layer); else np.delete('layer');
    np.set('limit', String(meta.limit));
    np.set('sortBy', query.sortBy ?? 'updated_at');
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

  // 若 URL 中携带了已废弃参数（q/phase/version/tag 或已删除列的 sortBy），清理掉以保持地址栏整洁
  useEffect(() => {
    const deprecatedKeys = ['q', 'phase', 'version', 'tag'];
    const legacySort = params.get('sortBy');
    const hasLegacy =
      deprecatedKeys.some((k) => params.has(k)) ||
      (!!legacySort && !VALID_SORT_KEYS.includes(legacySort));
    if (!hasLegacy) return;
    const np = new URLSearchParams(params.toString());
    deprecatedKeys.forEach((k) => np.delete(k));
    if (legacySort && !VALID_SORT_KEYS.includes(legacySort)) {
      np.delete('sortBy');
      np.delete('sortDir');
    }
    setParams(np, { replace: true });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return (
    <div className="page">
      <PageHeader
        eyebrow="Case Library"
        title="用例定义"
        desc="按层级浏览全部测试用例。点击用例名称查看详情，点击「历史」查看同一用例在不同版本中的表现。"
        right={<Button variant="secondary">🧭 批量导入（Phase 4）</Button>}
      />

      <section className="card">
        <div className="filter-bar filter-bar--cases">
          <select
            className="select"
            value={currentLayer}
            disabled={layersLoading}
            onChange={(e) => onLayerChange(e.target.value)}
          >
            <option value="">全部层级</option>
            {layers.map((l) => (
              <option key={l} value={l}>{LAYER_LABEL[l] ?? l}</option>
            ))}
          </select>
          <div className="filter-bar__right">
            <div className="muted filter-summary">
              <span>共 <strong>{meta.total}</strong> 条</span>
              {query.layer ? <span> · 层级：{LAYER_LABEL[query.layer] ?? query.layer}</span> : null}
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
        </div>

        {loading ? <TableSkeleton cols={3} rows={Math.min(12, meta.limit)} /> : (
          <CasesTable items={items} sortKey={query.sortBy} sortDir={query.sortDir} onSort={onSort} />
        )}

        <Pager page={meta.page} pages={meta.pages} total={meta.total} limit={meta.limit} onChange={onPage} />

        {layersLoading ? <Skeleton /> : null}
      </section>
    </div>
  );
}
