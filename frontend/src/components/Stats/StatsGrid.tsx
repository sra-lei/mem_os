import { fmtDuration, fmtNum, fmtRate, fmtLatency } from '@/utils/format';
import type { DashboardStats } from '@/types';
import { PHASE_LABEL } from '@/utils/color';
import { RateBar } from '../UI/RateBar';

interface StatCardProps {
  label: string;
  value: React.ReactNode;
  sub?: React.ReactNode;
  tone?: 'primary' | 'success' | 'warning' | 'danger' | 'neutral';
  icon?: React.ReactNode;
}

export function StatCard({ label, value, sub, tone = 'neutral', icon }: StatCardProps) {
  return (
    <article className={`stat-card stat-card--${tone}`}>
      <div className="stat-card__head">
        <div className="stat-card__label">{label}</div>
        {icon ? <div className={`stat-card__icon stat-card__icon--${tone}`}>{icon}</div> : null}
      </div>
      <div className="stat-card__value">{value}</div>
      {sub ? <div className="stat-card__sub">{sub}</div> : null}
    </article>
  );
}

export function StatsGrid({ stats }: { stats: DashboardStats }) {
  const rateTone =
    stats.total_pass_rate >= 0.9
      ? 'success'
      : stats.total_pass_rate >= 0.7
      ? 'warning'
      : 'danger';

  const totalTokensEstimate = 0; // 原后端暂时未暴露总 tokens，占位以保持结构

  return (
    <section className="grid-4">
      <StatCard
        tone="primary"
        label="运行总数"
        value={fmtNum(stats.total_runs)}
        sub={`近 7 天运行 ${fmtNum(stats.recent_7_days)} 次`}
        icon={
          <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M4 5h16v4H4zM4 12h10v7H4zM18 12h2v7h-2z"/></svg>
        }
      />
      <StatCard
        tone="neutral"
        label="用例定义"
        value={fmtNum(stats.total_cases)}
        sub="覆盖记忆检索 / Prompt 生成 / 回答生成"
        icon={
          <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M6 3h9l5 5v13a1 1 0 0 1-1 1H6a1 1 0 0 1-1-1V4a1 1 0 0 1 1-1z"/></svg>
        }
      />
      <StatCard
        tone={rateTone as 'success' | 'warning' | 'danger'}
        label="历史总通过率"
        value={fmtRate(stats.total_pass_rate)}
        sub={<RateBar value={stats.total_pass_rate} tone={stats.total_pass_rate >= 0.9 ? 'success' : stats.total_pass_rate >= 0.7 ? 'warning' : 'danger'} showLabel={false} />}
        icon={
          <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M20 6 9 17l-5-5"/></svg>
        }
      />
      <StatCard
        tone={stats.longest_phase ? 'warning' : 'neutral'}
        label="最慢阶段"
        value={stats.longest_phase ? PHASE_LABEL[stats.longest_phase.phase] : '暂无数据'}
        sub={stats.longest_phase ? `平均耗时 ${fmtLatency(stats.longest_phase.avg_latency_ms)}` : '等待首次运行'}
        icon={
          <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/></svg>
        }
      />
      {/* 辅助卡片：总 tokens（占位）+ 平均耗时 */}
      <StatCard
        tone="neutral"
        label="累计 Token 消耗"
        value={fmtNum(totalTokensEstimate) as unknown as React.ReactNode}
        sub="按结果 token 数聚合（Phase 4 接真实统计）"
        icon={
          <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M4 7h16M4 12h16M4 17h10"/></svg>
        }
      />
      <StatCard
        tone="neutral"
        label="最近一次运行"
        value={stats.recent_runs[0]?.name ?? '—'}
        sub={stats.recent_runs[0] ? fmtDurationBetweenSub(stats.recent_runs[0].start_time, stats.recent_runs[0].end_time) : '暂无'}
        icon={
          <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M12 2v4M12 18v4M2 12h4M18 12h4"/></svg>
        }
      />
    </section>
  );
}

function fmtDurationBetweenSub(start: string, end: string | null): string {
  if (!start) return '—';
  const s = new Date(start).getTime();
  const e = end ? new Date(end).getTime() : Date.now();
  if (Number.isNaN(s) || Number.isNaN(e)) return '—';
  return `耗时 ${fmtDuration(e - s)}`;
}
