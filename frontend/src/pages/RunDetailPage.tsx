import { useMemo, useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import { useRunDetail } from '@/hooks/useRunDetail';
import { RunSummary, FailedCasesGrid, PassedAccordion } from '@/components/RunDetail';
import { RunCategoryChart, PassFailPieChart } from '@/components/Charts';
import { CaseCompareModal } from '@/components/CaseCompareModal';
import { CardHeader, PageHeader } from './DashboardPage';
import { Skeleton, TableSkeleton, Button, useAsyncErrorToast } from '@/components/UI';
import type { TestCaseResult } from '@/types';

export function RunDetailPage() {
  const { runId } = useParams<{ runId: string }>();
  const { run, results, byCategory, loading, error, refresh } = useRunDetail(runId);
  useAsyncErrorToast(error, '加载运行详情失败');

  const [compareId, setCompareId] = useState<number | null>(null);
  const compare: TestCaseResult | null = useMemo(
    () => (compareId ? results.find((r) => r.id === compareId) ?? null : null),
    [compareId, results],
  );

  return (
    <div className="page">
      <PageHeader
        eyebrow="Run Detail"
        title={
          <>
            运行详情
            {run ? <Link to="/runs" className="link-muted"> ← 返回列表</Link> : null}
          </>
        }
        desc="该次运行的整体表现、阶段分布、失败原因逐条对比，支持快速定位异常用例。"
      />

      {loading && !run ? (
        <>
          <div className="card">
            <div style={{ display: 'grid', gap: 12 }}>
              <Skeleton width="40%" height="28px" />
              <Skeleton lines={3} />
              <Skeleton width="60%" height="22px" />
            </div>
          </div>
          <div className="grid-2">
            <div className="card"><div style={{ height: 320 }}><Skeleton width="100%" height="100%" /></div></div>
            <div className="card"><div style={{ height: 320 }}><Skeleton width="100%" height="100%" /></div></div>
          </div>
          <div className="card"><TableSkeleton cols={4} rows={8} /></div>
        </>
      ) : run ? (
        <>
          <RunSummary run={run} onRefresh={refresh} />

          <div className="grid-2">
            <section className="card">
              <CardHeader title="分类通过率 & 平均耗时" subtitle="按阶段看表现，快速识别表现最弱的阶段" />
              <RunCategoryChart data={byCategory} />
            </section>
            <section className="card">
              <CardHeader
                title="结果分布"
                subtitle={`通过 ${run.passed} / 失败 ${run.failed} / 总 ${run.total_cases}`}
                action={
                  <Button variant="ghost" size="sm" onClick={() => refresh()}>
                    刷新
                  </Button>
                }
              />
              <PassFailPieChart run={run} />
            </section>
          </div>

          <section className="card">
            <CardHeader
              title={
                <>
                  ❌ 失败用例
                  <span className="badge-text badge-text--danger">
                    {results.filter((r) => !r.passed).length}
                  </span>
                </>
              }
              subtitle="点击「查看原因」打开对比详情：期望 vs 实际 / Judge Reason / 检索记忆"
            />
            <FailedCasesGrid results={results} onOpenCompare={(id) => setCompareId(id)} />
          </section>

          <PassedAccordion results={results} onOpenCompare={(id) => setCompareId(id)} />
        </>
      ) : (
        <div className="card">
          <div className="empty-state">
            <h2>未找到该运行</h2>
            <p className="muted">
              运行 ID <code>{runId}</code> 可能不存在或已被清理。
            </p>
            <Link to="/runs">
              <Button variant="primary">返回运行列表</Button>
            </Link>
          </div>
        </div>
      )}

      <CaseCompareModal open={!!compare} result={compare} onClose={() => setCompareId(null)} />
    </div>
  );
}
