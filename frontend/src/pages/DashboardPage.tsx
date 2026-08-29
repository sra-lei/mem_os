import { Link } from 'react-router-dom';
import type { ParamKeyValuePair } from 'react-router-dom';
import { useDashboard } from '@/hooks/useDashboard';
import { StatsGrid } from '@/components/Stats';
import { TrendLineChart, CategoryChart } from '@/components/Charts';
import { FailingCasesList, RecentRunsList } from '@/components/List';
import { TableSkeleton, Skeleton, useAsyncErrorToast } from '@/components/UI';

export function DashboardPage() {
  const { stats, loading, error } = useDashboard();
  useAsyncErrorToast(error, '仪表盘加载失败');

  return (
    <div className="page">
      <PageHeader
        eyebrow="Dashboard"
        title="总览仪表盘"
        desc="最近运行的整体质量、阶段表现与失败用例一览，快速定位需要重点关注的版本或阶段。"
      />

      {loading && !stats ? (
        <>
          <div className="grid-4">
            {Array.from({ length: 6 }, (_, i) => (
              <div key={i} className="card card--skeleton">
                <Skeleton width="120px" height="14px" />
                <div style={{ height: 16 }} />
                <Skeleton width="55%" height="32px" />
                <div style={{ height: 12 }} />
                <Skeleton lines={2} />
              </div>
            ))}
          </div>
          <div className="grid-2">
            <div className="card"><div style={{ height: 320 }}><Skeleton width="100%" height="100%" /></div></div>
            <div className="card"><div style={{ height: 320 }}><Skeleton width="100%" height="100%" /></div></div>
            <div className="card"><div style={{ height: 380 }}><TableSkeleton cols={3} rows={8} /></div></div>
            <div className="card"><div style={{ height: 380 }}><TableSkeleton cols={3} rows={8} /></div></div>
          </div>
        </>
      ) : stats ? (
        <>
          <StatsGrid stats={stats} />
          <div className="grid-2">
            <section className="card">
              <CardHeader
                title="版本通过率趋势"
                subtitle="近 10 次运行的通过率曲线 + 用例数柱，颜色按版本散列"
                action={<Link to="/runs" className="link-muted">全部运行 →</Link>}
              />
              <TrendLineChart data={stats.trend} />
            </section>
            <section className="card">
              <CardHeader
                title="阶段通过率 / 耗时"
                subtitle="按记忆检索、Prompt 生成、回答生成三维度对比；柱堆叠=通过/失败，线=平均耗时"
                action={<Link to="/cases" className="link-muted">用例定义 →</Link>}
              />
              <CategoryChart data={stats.by_category} />
            </section>
            <section className="card">
              <CardHeader
                title="持续失败用例 TOP 10"
                subtitle="跨运行累计失败次数最多的用例，优先修这些"
                badge={<BadgeText tone="danger">{stats.failing_cases.length}</BadgeText>}
              />
              <FailingCasesList items={stats.failing_cases} />
            </section>
            <section className="card">
              <CardHeader
                title="最近运行"
                subtitle="最近 10 次运行的基本情况，点卡片即可进入详情"
                badge={<BadgeText tone="primary">{stats.recent_runs.length}</BadgeText>}
              />
              <RecentRunsList items={stats.recent_runs} />
            </section>
          </div>
        </>
      ) : null}
    </div>
  );
}

// ---- 页面公用小组件（仅本文件使用，未来抽离到 UI 时再移） ----
import type { ReactNode } from 'react';

export function PageHeader({
  eyebrow,
  title,
  desc,
  right,
}: {
  eyebrow?: string;
  title: ReactNode;
  desc?: ReactNode;
  right?: ReactNode;
}) {
  return (
    <header className="page__head">
      <div className="page__titles">
        {eyebrow ? <span className="eyebrow">{eyebrow}</span> : null}
        <h1 className="page__title">{title}</h1>
        {desc ? <p className="page__desc">{desc}</p> : null}
      </div>
      {right ? <div className="page__actions">{right}</div> : null}
    </header>
  );
}

export function CardHeader({
  title,
  subtitle,
  action,
  badge,
}: {
  title: ReactNode;
  subtitle?: ReactNode;
  action?: ReactNode;
  badge?: ReactNode;
}) {
  return (
    <header className="card__head">
      <div className="card__titles">
        <h3 className="card__title">
          {title} {badge}
        </h3>
        {subtitle ? <p className="card__subtitle">{subtitle}</p> : null}
      </div>
      {action ? <div className="card__action">{action}</div> : null}
    </header>
  );
}

export function BadgeText({ tone = 'neutral', children }: { tone?: 'neutral' | 'primary' | 'success' | 'warning' | 'danger'; children: ReactNode }) {
  return <span className={`badge-text badge-text--${tone}`}>{children}</span>;
}

/** 分页器：轻量实现，避免引入 antd。 */
export function Pager({
  page,
  pages,
  onChange,
  total,
  limit,
}: {
  page: number;
  pages: number;
  onChange: (next: number) => void;
  total: number;
  limit: number;
}) {
  const from = total === 0 ? 0 : (page - 1) * limit + 1;
  const to = Math.min(total, page * limit);
  const windowed = buildPagerWindow(page, pages);
  return (
    <div className="pager">
      <div className="pager__info muted">
        共 <strong>{total}</strong> 条 · {from}-{to}
      </div>
      <div className="pager__btns">
        <button className="btn btn--ghost btn--sm" disabled={page <= 1} onClick={() => onChange(1)}>
          «
        </button>
        <button className="btn btn--ghost btn--sm" disabled={page <= 1} onClick={() => onChange(page - 1)}>
          ‹
        </button>
        {windowed.map((p, idx) =>
          p === '...' ? (
            <span key={`dot-${idx}`} className="pager__dots">…</span>
          ) : (
            <button
              key={p}
              className={`btn btn--sm ${p === page ? 'btn--primary' : 'btn--ghost'}`}
              onClick={() => onChange(p as number)}
            >
              {p}
            </button>
          ),
        )}
        <button className="btn btn--ghost btn--sm" disabled={page >= pages} onClick={() => onChange(page + 1)}>
          ›
        </button>
        <button className="btn btn--ghost btn--sm" disabled={page >= pages} onClick={() => onChange(pages)}>
          »
        </button>
      </div>
    </div>
  );
}

function buildPagerWindow(current: number, total: number): (number | '...')[] {
  const size = 7;
  if (total <= size) return Array.from({ length: total }, (_, i) => i + 1);
  const arr: (number | '...')[] = [1];
  const left = Math.max(2, current - 1);
  const right = Math.min(total - 1, current + 1);
  if (left > 2) arr.push('...');
  for (let i = left; i <= right; i++) arr.push(i);
  if (right < total - 1) arr.push('...');
  arr.push(total);
  return arr;
}

// Helper to construct query params helpers (used by filter bars)
export function toQuery(pairs: ParamKeyValuePair[]): string {
  const usp = new URLSearchParams();
  for (const [k, v] of pairs) usp.append(k, v);
  const s = usp.toString();
  return s ? `?${s}` : '';
}
