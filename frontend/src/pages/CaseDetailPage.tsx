import { Link, useParams } from 'react-router-dom';
import { useAsync } from '@/hooks/useAsync';
import { casesApi } from '@/api/cases';
import { Button, Skeleton, useAsyncErrorToast } from '@/components/UI';
import { PhaseBadge, PassBadge } from '@/components/UI/Badge';
import { fmtDate as fmtDateFull, fmtRate } from '@/utils/format';
import type { CaseDefinition } from '@/types';

/**
 * CaseDefinition Detail page.
 * Route: /cases/:caseId
 *
 * Shows all 15 real DB columns of test_case_definitions organized into logical
 * sections, plus 3 computed aggregate counters.
 */

const LAYER_LABEL: Record<string, string> = {
  layer1: 'L1 单场景',
  layer2: 'L2 多场景组合',
  layer3: 'L3 多领域复杂协同',
};

/** Pretty-print JSON string or fall back to raw text. */
function PrettyText({ value, fallback = '—' }: { value: string | null | undefined; fallback?: string }) {
  if (!value) return <span className="muted">{fallback}</span>;
  try {
    const parsed = JSON.parse(value);
    return <pre className="pre pre--json">{JSON.stringify(parsed, null, 2)}</pre>;
  } catch {
    return <pre className="pre">{value}</pre>;
  }
}

function MetaItem({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="meta-item">
      <div className="meta-item__label">{label}</div>
      <div className="meta-item__value">{children}</div>
    </div>
  );
}

function StatKPI({
  label, value, tone = 'muted', hint,
}: {
  label: string;
  value: React.ReactNode;
  tone?: 'muted' | 'ok' | 'warn' | 'bad';
  hint?: React.ReactNode;
}) {
  return (
    <div className={`kpi kpi--${tone}`}>
      <div className="kpi__value">{value}</div>
      <div className="kpi__label">{label}</div>
      {hint ? <div className="kpi__hint">{hint}</div> : null}
    </div>
  );
}

function OverviewKPIs({ c }: { c: CaseDefinition }) {
  const total = Number(c.total_runs ?? 0);
  const pass = Number(c.pass_count ?? 0);
  const fail = Number(c.fail_count ?? 0);
  const rate = total > 0 ? pass / total : 0;
  const tone = total === 0 ? 'muted' : rate >= 0.7 ? 'ok' : rate >= 0.4 ? 'warn' : 'bad';
  return (
    <div className="kpi-grid">
      <StatKPI label="历史运行次数" value={total} tone="muted" hint="(所有版本累计)" />
      <StatKPI label="通过" value={pass} tone="ok" />
      <StatKPI label="失败" value={fail} tone={fail > 0 ? 'bad' : 'muted'} />
      <StatKPI
        label="通过率"
        value={fmtRate(rate)}
        tone={tone}
        hint={<PassBadge passed={rate >= 0.7 && total > 0} />}
      />
    </div>
  );
}

export function CaseDetailPage() {
  const { caseId = '' } = useParams<{ caseId: string }>();

  const defAsync = useAsync(
    (signal) => casesApi.detail(caseId, signal),
    [caseId],
  );
  useAsyncErrorToast(defAsync.error, '加载用例详情失败');

  const c = defAsync.data;
  const layer = c?.layer ?? null;
  const loading = defAsync.loading;

  return (
    <div className="page">
      {/* ===== Header ===== */}
      <header className="page-header">
        <div className="page-header__crumb">
          <Link to="/cases" className="muted link-muted">← 用例库</Link>
        </div>
        <div className="page-header__body">
          <div>
            <div className="page-header__eyebrow eyebrow">
              <code className="chip chip--outline">{caseId || '—'}</code>
              {c ? <PhaseBadge phase={c.phase} /> : null}
              {c?.version ? <code className="chip chip--sm">目标 {c.version}</code> : null}
              {layer ? (
                <span className={`layer-chip layer-chip--${layer}`}>
                  {LAYER_LABEL[layer] ?? layer}
                </span>
              ) : null}
            </div>
            <h1 className="page-header__title">
              {loading ? <Skeleton width="60%" height="28px" /> : (c?.case_name ?? '未知用例')}
            </h1>
            <p className="page-header__desc muted">
              {loading ? <Skeleton width="90%" lines={2} /> : (c?.description ?? '无描述')}
            </p>
          </div>
          <div className="page-header__actions">
            <Link to={`/cases/${caseId}/history`}>
              <Button variant="primary">📜 查看运行历史</Button>
            </Link>
          </div>
        </div>
      </header>

      {/* ===== KPIs ===== */}
      <section className="card">
        <h2 className="card__title">历史统计概览</h2>
        {loading ? <Skeleton lines={3} /> : c ? <OverviewKPIs c={c} /> : <span className="muted">暂无数据</span>}
      </section>

      {/* ===== Metadata (attrs 14 cols except the big text ones) ===== */}
      <section className="card">
        <h2 className="card__title">基本属性</h2>
        {loading ? <Skeleton lines={6} /> : c ? (
          <div className="meta-grid">
            <MetaItem label="Case ID"><code>{c.case_id}</code></MetaItem>
            <MetaItem label="用例名称">{c.case_name}</MetaItem>
            <MetaItem label="阶段 (category)"><PhaseBadge phase={c.phase} /></MetaItem>
            <MetaItem label="目标版本">
              {c.version ? <code className="chip chip--sm">{c.version}</code> : <span className="muted">—</span>}
            </MetaItem>
            <MetaItem label="测试层级">
              {layer
                ? <span className={`layer-chip layer-chip--${layer}`}>{LAYER_LABEL[layer] ?? layer}</span>
                : <span className="muted">—</span>}
            </MetaItem>
            <MetaItem label="源码路径">
              {c.source_path ? <code className="code-inline mono">{c.source_path}</code> : <span className="muted">—</span>}
            </MetaItem>
            <MetaItem label="标签">
              {(c.tags?.length ?? 0) > 0
                ? <div className="tags">{(c.tags ?? []).map((t) => <span key={t} className="tag">{t}</span>)}</div>
                : <span className="muted">—</span>}
            </MetaItem>
            <MetaItem label="创建时间">{c.created_at ? fmtDateFull(c.created_at) : <span className="muted">—</span>}</MetaItem>
            <MetaItem label="最近更新">{c.updated_at ? fmtDateFull(c.updated_at) : <span className="muted">—</span>}</MetaItem>
          </div>
        ) : <span className="muted">暂无数据</span>}
      </section>

      {/* ===== Prompt / Eval config ===== */}
      <section className="card">
        <h2 className="card__title">用例定义详情</h2>
        {loading ? <Skeleton lines={8} /> : c ? (
          <div className="detail-grid">
            <div className="detail-section">
              <div className="detail-section__title">📝 场景描述 (description)</div>
              <PrettyText value={c.description} />
            </div>
            <div className="detail-section">
              <div className="detail-section__title">💬 会话历史素材 (conversation_histories)</div>
              <div className="muted detail-section__hint">多段会话（含时间戳与元数据），评测时逐段送入记忆系统生成记忆。</div>
              <PrettyText value={c.conversation_histories_raw} />
            </div>
            <div className="detail-section">
              <div className="detail-section__title">👤 用户问题 (query)</div>
              <PrettyText value={c.query} />
            </div>
            <div className="detail-section">
              <div className="detail-section__title">🎯 期望行为 (expected_behavior)</div>
              <PrettyText value={c.expected_behavior} />
            </div>
            <div className="detail-section detail-section--full">
              <div className="detail-section__title">⚖️ 评分标准 (evaluation_criteria)</div>
              <div className="muted detail-section__hint">LLM-as-Judge 的判分依据。</div>
              <PrettyText value={c.evaluation_criteria} />
            </div>
          </div>
        ) : <span className="muted">暂无数据</span>}
      </section>
    </div>
  );
}
