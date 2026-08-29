import type { RunSummary } from '@/types';
import { StatusBadge, PhaseBadge } from '../UI/Badge';
import { RateBar } from '../UI/RateBar';
import { Link } from 'react-router-dom';
import {
  fmtDate,
  fmtRate,
  fmtDurationBetween,
  fmtNum,
  fmtLatency,
} from '@/utils/format';

export function RunSummary({ run }: { run: RunSummary }) {
  const tone = run.pass_rate >= 0.9 ? 'success' : run.pass_rate >= 0.7 ? 'warning' : 'danger';
  const total = run.total_cases;
  const p = run.passed;
  const f = run.failed;
  const skipped = Math.max(0, total - p - f);
  const duration = fmtDurationBetween(run.start_time, run.end_time);

  // avg latency placeholder：后端 summary 尚未暴露，先展示 "—"
  const avgLatency: number | null = null;

  return (
    <section className="card run-summary">
      <header className="run-summary__head">
        <div className="run-summary__title-block">
          <Link to="/runs" className="link-muted">← 返回运行列表</Link>
          <h2 className="run-summary__name">
            {run.name}
            <code className="chip chip--lg">#{run.id} · {run.version}</code>
          </h2>
          <div className="run-summary__meta">
            <PhaseBadge phase={run.phase} />
            <StatusBadge status={run.status ?? 'completed'} />
            <span className="muted mono">开始 {fmtDate(run.start_time)}</span>
            <span className="muted mono">耗时 {duration}</span>
          </div>
          {run.error_message ? (
            <div className="callout callout--danger">
              <strong>失败原因：</strong>
              <span>{run.error_message}</span>
            </div>
          ) : null}
        </div>
        <div className="run-summary__rate">
          <div className={`run-summary__rate-num num num--${tone}`}>{fmtRate(run.pass_rate)}</div>
          <RateBar value={run.pass_rate} tone={tone as 'success' | 'warning' | 'danger'} />
          <div className="run-summary__rate-legend">
            <span className="dot dot--success" /> 通过 {fmtNum(p)}
            <span className="dot dot--danger" /> 失败 {fmtNum(f)}
            {skipped > 0 ? <><span className="dot dot--muted" /> 跳过 {fmtNum(skipped)}</> : null}
          </div>
        </div>
      </header>
      <ul className="run-summary__metrics">
        <li><span>用例总数</span><strong>{fmtNum(run.total_cases)}</strong></li>
        <li><span>通过</span><strong className="num--success">{fmtNum(p)}</strong></li>
        <li><span>失败</span><strong className="num--danger">{fmtNum(f)}</strong></li>
        <li><span>平均耗时</span><strong>{avgLatency == null ? '—' : fmtLatency(avgLatency)}</strong></li>
        <li><span>进度</span><strong>{run.progress ? `${Math.round(run.progress * 100)}%` : '—'}</strong></li>
      </ul>
    </section>
  );
}
