import { Link, useParams } from 'react-router-dom';
import { useMemo, useState } from 'react';
import { useAsync } from '@/hooks/useAsync';
import { useCaseHistory } from '@/hooks/useCaseHistory';
import { casesApi } from '@/api/cases';
import { CaseHistoryTable } from '@/components/Tables';
import { CaseHistoryChart } from '@/components/Charts';
import { CaseCompareModal } from '@/components/CaseCompareModal';
import { Skeleton, TableSkeleton, Button, PhaseBadge, PassBadge, useAsyncErrorToast } from '@/components/UI';
import { CardHeader, PageHeader } from './DashboardPage';
import { fmtDate, fmtRate, fmtNum, fmtLatency } from '@/utils/format';
import type { TestCaseResult } from '@/types';

export function CaseHistoryPage() {
  const { caseId } = useParams<{ caseId: string }>();
  const defAsync = useAsync(
    (_signal) => (caseId != null && caseId !== '' ? casesApi.detail(caseId, _signal) : Promise.resolve(null)),
    [caseId],
  );
  const history = useCaseHistory(caseId);
  useAsyncErrorToast(defAsync.error, '加载用例定义失败');
  useAsyncErrorToast(history.error, '加载历史运行失败');

  const [compareId, setCompareId] = useState<string | number | null>(null);

  // 通过 result id 再拉取一次 detail，填入模态框
  const compareResultAsync = useAsync(async (_signal) => {
    if (compareId == null) return null;
    // 先从 history 里找（它缺少 setup 等字段），找不到就用占位
    const match = history.data?.find((h) => h.id === compareId);
    if (!match) return null;
    return {
      id: match.id,
      run_id: match.run_id,
      case_id: 0,
      case_name: defAsync.data?.case_name ?? '',
      phase: defAsync.data?.phase ?? 'memory_retrieval',
      tags: defAsync.data?.tags ?? [],
      retrieval_memory: null,
      user_input: defAsync.data?.query ?? '',
      expected_output: defAsync.data?.expected_answer ?? '',
      actual_output: match.actual_output,
      passed: match.passed,
      judge_reason: match.judge_reason,
      latency_ms: match.latency_ms,
      tokens_input: match.tokens_input,
      tokens_output: match.tokens_output,
      created_at: match.start_time,
    } as unknown as TestCaseResult;
  }, [compareId, history.data, defAsync.data]);

  const stats = useMemo(() => {
    const items = history.data ?? [];
    const total = items.length;
    const pass = items.filter((x) => x.passed).length;
    const rate = total ? pass / total : 0;
    const avgLatency =
      total && items.every((x) => Number.isFinite(x.latency_ms))
        ? items.reduce((sum, x) => sum + (x.latency_ms || 0), 0) / total
        : null;
    return { total, pass, fail: total - pass, rate, avgLatency };
  }, [history.data]);

  return (
    <div className="page">
      <PageHeader
        eyebrow="Case History"
        title={
          <>
            用例历史
            {defAsync.data ? (
              <Link to="/cases" className="link-muted">
                {' '}
                ← 返回用例库
              </Link>
            ) : null}
          </>
        }
        desc="同一用例在不同版本 / 不同运行下的表现对比，观察回归或改进趋势。"
        right={
          defAsync.data ? (
            <Link to={`/cases/${defAsync.data.case_id}`}>
              <Button variant="secondary">刷新</Button>
            </Link>
          ) : null
        }
      />

      {defAsync.loading && !defAsync.data ? (
        <div className="card">
          <Skeleton width="40%" height="26px" />
          <div style={{ height: 12 }} />
          <Skeleton lines={3} />
        </div>
      ) : defAsync.data ? (
        <section className="card case-def">
          <div className="case-def__row">
            <div>
              <div className="muted">Case ID</div>
              <code className="chip chip--lg">{defAsync.data.case_id}</code>
            </div>
            <div>
              <div className="muted">阶段</div>
              <PhaseBadge phase={defAsync.data.phase} />
            </div>
            <div>
              <div className="muted">标签</div>
              <div className="tags">
                {defAsync.data.tags.map((t) => (
                  <span key={t} className="tag">{t}</span>
                ))}
                {defAsync.data.tags.length === 0 ? <span className="muted">无</span> : null}
              </div>
            </div>
            <div>
              <div className="muted">最近更新</div>
              <div className="mono">{fmtDate(defAsync.data.updated_at)}</div>
            </div>
          </div>
          <h2 className="case-def__name">{defAsync.data.case_name}</h2>
          <dl className="kv kv--grid">
            <div><dt>User Input</dt><dd><pre className="pre">{defAsync.data.user_input ?? '—'}</pre></dd></div>
            <div><dt>Retrieval Memory</dt><dd><pre className="pre pre--memory">{defAsync.data.retrieval_memory || '—'}</pre></dd></div>
            <div className="span-2">
              <dt>Expected Output</dt>
              <dd><pre className="pre pre--success">{defAsync.data.expected_output}</pre></dd>
            </div>
          </dl>
          <div className="case-def__stats">
            <Metric label="历史运行" value={fmtNum(defAsync.data.total_runs)} />
            <Metric label="通过" value={<span className="num--success">{fmtNum(defAsync.data.pass_count)}</span>} />
            <Metric label="失败" value={<span className="num--danger">{fmtNum(defAsync.data.fail_count)}</span>} />
            <Metric
              label="历史通过率"
              value={
                <span
                  className={`num--${
                    defAsync.data.total_runs
                      ? defAsync.data.pass_count / defAsync.data.total_runs >= 0.7
                        ? 'success'
                        : 'danger'
                      : 'neutral'
                  }`}
                >
                  {fmtRate(
                    defAsync.data.total_runs ? defAsync.data.pass_count / defAsync.data.total_runs : 0,
                  )}
                </span>
              }
            />
          </div>
        </section>
      ) : null}

      <div className="grid-2">
        <section className="card">
          <CardHeader
            title="历史聚合统计"
            subtitle={`共 ${stats.total} 次运行 · 通过率 ${fmtRate(stats.rate)}${
              stats.avgLatency ? ` · 平均耗时 ${fmtLatency(stats.avgLatency)}` : ''
            }`}
          />
          <ul className="metrics-vertical">
            <li><PassBadge passed={stats.rate >= 0.7 && stats.total > 0} /><span className="muted">通过率</span><strong>{fmtRate(stats.rate)}</strong></li>
            <li><span>通过 / 失败</span><strong>{fmtNum(stats.pass)} / {fmtNum(stats.fail)}</strong></li>
            <li><span>平均耗时</span><strong>{stats.avgLatency == null ? '—' : fmtLatency(stats.avgLatency)}</strong></li>
          </ul>
        </section>
        <section className="card">
          <CardHeader title="历史耗时散点" subtitle="按时间顺序横轴：通过=绿圆，失败=红三角；纵轴=耗时" />
          {history.loading ? <Skeleton width="100%" height={280} /> : <CaseHistoryChart items={history.data ?? []} />}
        </section>
      </div>

      <section className="card">
        <CardHeader
          title="历史运行记录列表"
          subtitle="点击「查看详细」可以打开期望 vs 实际 + Judge Reason 对比视图"
        />
        {history.loading && !history.data ? (
          <TableSkeleton cols={6} rows={Math.min(10, stats.total || 6)} />
        ) : (
          <CaseHistoryTable
            items={history.data ?? []}
            onOpenCompare={(id) => setCompareId(id)}
          />
        )}
      </section>

      <CaseCompareModal
        open={!!compareResultAsync.data}
        result={compareResultAsync.data}
        onClose={() => setCompareId(null)}
      />
    </div>
  );
}

function Metric({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div className="metric-pill">
      <span className="muted">{label}</span>
      <strong>{value}</strong>
    </div>
  );
}
