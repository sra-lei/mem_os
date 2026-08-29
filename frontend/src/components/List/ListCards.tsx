import { Link } from 'react-router-dom';
import type { FailingCase, RunSummary } from '@/types';
import { PhaseBadge, PassBadge } from '../UI/Badge';
import { fmtDateShort, fmtNum } from '@/utils/format';

export function FailingCasesList({ items }: { items: FailingCase[] }) {
  if (items.length === 0) {
    return (
      <div className="empty-state empty-state--success">
        <strong>🎉 没有持续失败的用例</strong>
        <span className="muted">最近运行中全部通过。</span>
      </div>
    );
  }
  return (
    <ul className="list-cards">
      {items.slice(0, 10).map((c) => {
        const total = (c.pass_count ?? 0) + (c.fail_count ?? 0);
        return (
          <li key={c.case_id} className="list-card list-card--danger">
            <div className="list-card__row">
              <strong className="list-card__title">
                #{c.case_id} {c.case_name}
              </strong>
              <PhaseBadge phase={c.phase} />
            </div>
            <div className="list-card__row muted">
              <span>
                失败 <strong className="num--danger">{fmtNum(c.fail_count)}</strong> / 共{' '}
                {fmtNum(total)}
              </span>
              {c.last_run_name ? (
                <span>
                  最近：<Link to={`/runs/${c.last_run_id}`}>{c.last_run_name}</Link>
                </span>
              ) : null}
              <span className="mono">{fmtDateShort(c.last_run_time)}</span>
              <PassBadge passed={!!c.last_passed} />
            </div>
          </li>
        );
      })}
    </ul>
  );
}

export function RecentRunsList({ items }: { items: RunSummary[] }) {
  if (items.length === 0) {
    return (
      <div className="empty-state empty-state--neutral">
        <strong>暂无运行</strong>
        <span className="muted">去运行一次测试吧。</span>
      </div>
    );
  }
  return (
    <ul className="list-cards">
      {items.slice(0, 10).map((r) => {
        const tone = r.pass_rate >= 0.9 ? 'success' : r.pass_rate >= 0.7 ? 'warning' : 'danger';
        return (
          <li key={r.id} className="list-card">
            <div className="list-card__row">
              <Link to={`/runs/${r.id}`} className="link-strong">
                #{r.id} {r.name}
              </Link>
              <code className="chip chip--sm">{r.version}</code>
              <PhaseBadge phase={r.phase} />
            </div>
            <div className="list-card__row muted">
              <span className="mono">{fmtDateShort(r.start_time)}</span>
              <span>
                用例 {fmtNum(r.total_cases)} · 通过率{' '}
                <strong className={`num--${tone}`}>{(r.pass_rate * 100).toFixed(1)}%</strong>
              </span>
            </div>
          </li>
        );
      })}
    </ul>
  );
}
