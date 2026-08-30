import { useMemo, useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import { useRunDetail } from '@/hooks/useRunDetail';
import { RunSummary, PassFailCases } from '@/components/RunDetail';
import { RunCategoryChart, PassFailPieChart } from '@/components/Charts';
import { CaseCompareModal } from '@/components/CaseCompareModal';
import { CardHeader, PageHeader } from './DashboardPage';
import { Skeleton, TableSkeleton, Button, useAsyncErrorToast } from '@/components/UI';
import { fmtNum } from '@/utils/format';
import type { TestCaseResult } from '@/types';

export function RunDetailPage() {
  const { runId } = useParams<{ runId: string }>();
  const { run, results, byCategory, loading, error } = useRunDetail(runId);
  useAsyncErrorToast(error, '加载运行详情失败');

  const [compareId, setCompareId] = useState<string | number | null>(null);
  const compare: TestCaseResult | null = useMemo(
    () => (compareId != null ? results.find((r) => r.id === compareId) ?? null : null),
    [compareId, results],
  );

  // Token 消耗（回答 LLM，不含 judge）：对每条用例的 tokens 求和
  const tokens = useMemo(
    () => ({
      input: results.reduce((a, r) => a + (r.tokens_input ?? 0), 0),
      output: results.reduce((a, r) => a + (r.tokens_output ?? 0), 0),
    }),
    [results],
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
          <RunSummary run={run} />

          <section className="card">
            <CardHeader title="Token 消耗" subtitle="回答 LLM（DeepSeek）的 token 用量，不含评测判分" />
            <ul className="run-summary__metrics">
              <li><span>Token 输入</span><strong>{fmtNum(tokens.input)}</strong></li>
              <li><span>Token 输出</span><strong>{fmtNum(tokens.output)}</strong></li>
              <li><span>Token 合计</span><strong>{fmtNum(tokens.input + tokens.output)}</strong></li>
            </ul>
          </section>

          <div className="grid-2">
            <section className="card">
              <CardHeader title="分类通过率 & 平均耗时" subtitle="按阶段看表现，快速识别表现最弱的阶段" />
              <RunCategoryChart data={byCategory} />
            </section>
            <section className="card">
              <CardHeader
                title="结果分布"
                subtitle={`通过 ${run.passed} / 失败 ${run.failed} / 总 ${run.total_cases}`}
              />
              <PassFailPieChart run={run} />
            </section>
          </div>

          <PassFailCases results={results} onOpenCompare={(id) => setCompareId(id)} />
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
