import type { TestCaseResult } from '@/types';
import { PhaseBadge, PassBadge } from '../UI/Badge';
import { Button } from '../UI/Button';
import { fmtLatency, fmtNum, truncate } from '@/utils/format';

export interface FailedCaseCardProps {
  result: TestCaseResult;
  onOpenCompare: (id: number) => void;
}

export function FailedCaseCard({ result, onOpenCompare }: FailedCaseCardProps) {
  return (
    <article className="failed-card">
      <header className="failed-card__head">
        <div className="failed-card__titles">
          <strong className="failed-card__name">#{result.case_id} {result.case_name}</strong>
          <div className="failed-card__tags">
            <PhaseBadge phase={result.phase} />
            <PassBadge passed={false} />
            <span className="muted mono">{fmtLatency(result.latency_ms)} · tokens {fmtNum(result.tokens_input)}/{fmtNum(result.tokens_output)}</span>
            {result.tags.length > 0 ? (
              <div className="tags tags--sm">
                {result.tags.slice(0, 3).map((t) => (
                  <span key={t} className="tag">{t}</span>
                ))}
              </div>
            ) : null}
          </div>
        </div>
        <Button variant="danger" size="sm" onClick={() => onOpenCompare(result.id)}>
          查看原因 →
        </Button>
      </header>
      <dl className="kv kv--compact failed-card__kv">
        <div>
          <dt>期望输出</dt>
          <dd><pre className="pre pre--success">{truncate(result.expected_output, 180)}</pre></dd>
        </div>
        <div>
          <dt>实际输出</dt>
          <dd><pre className="pre pre--danger">{truncate(result.actual_output, 180)}</pre></dd>
        </div>
        <div>
          <dt>判分理由</dt>
          <dd><pre className="pre pre--warning">{truncate(result.judge_reason, 240)}</pre></dd>
        </div>
      </dl>
    </article>
  );
}

export function FailedCasesGrid({
  results,
  onOpenCompare,
}: {
  results: TestCaseResult[];
  onOpenCompare: (id: number) => void;
}) {
  const failed = results.filter((r) => !r.passed);
  if (failed.length === 0) {
    return (
      <div className="empty-state empty-state--success">
        <svg width="44" height="44" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M20 6 9 17l-5-5"/></svg>
        <strong>太棒了，没有失败用例</strong>
        <span className="muted">本次运行中全部通过。</span>
      </div>
    );
  }
  return (
    <div className="grid-failed">
      {failed.map((r) => (
        <FailedCaseCard key={r.id} result={r} onOpenCompare={onOpenCompare} />
      ))}
    </div>
  );
}
