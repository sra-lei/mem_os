import { useEffect, useMemo } from 'react';
import type { ReactNode } from 'react';
import type { TestCaseResult } from '@/types';
import { PhaseBadge, PassBadge } from '../UI/Badge';
import { Button } from '../UI/Button';
import { fmtLatency, fmtNum } from '@/utils/format';

interface Props {
  open: boolean;
  onClose: () => void;
  result: TestCaseResult | null;
  title?: ReactNode;
}

/**
 * Case Compare Modal：期望 vs 实际 对比视图 + 折叠检索记忆。
 * 单结果对比实现；包含 ESC 关闭 / 背景点击关闭 / 滚动锁定。
 */
export function CaseCompareModal({ open, onClose, result, title }: Props) {
  // ESC 关闭
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    window.addEventListener('keydown', onKey);
    document.body.style.overflow = 'hidden';
    return () => {
      window.removeEventListener('keydown', onKey);
      document.body.style.overflow = '';
    };
  }, [open, onClose]);

  const sections = useMemo(() => {
    if (!result) return null;
    return [
      { label: 'User Input（用户输入）', value: result.user_input || '—', tone: 'neutral' as const },
      {
        label: 'Expected Output（期望）',
        value: result.expected_output || '—',
        tone: 'success' as const,
      },
      {
        label: 'Actual Output（实际）',
        value: result.actual_output || '—',
        tone: result.passed ? ('success' as const) : ('danger' as const),
      },
      {
        label: 'Judge Reason（判分理由）',
        value: result.judge_reason || '—',
        tone: 'warning' as const,
      },
    ];
  }, [result]);

  if (!open || !result) return null;

  return (
    <div className="modal" role="dialog" aria-modal="true" aria-labelledby="case-compare-title">
      <div className="modal__backdrop" onClick={onClose} />
      <div className="modal__panel" role="document">
        <header className="modal__head">
          <div className="modal__titles">
            <h3 id="case-compare-title" className="modal__title">
              {title ?? (
                <>
                  #{result.case_id} · {result.case_name}
                </>
              )}
            </h3>
            <div className="modal__sub">
              <PhaseBadge phase={result.phase} />
              <PassBadge passed={!!result.passed} />
              <span className="muted mono">
                耗时 {fmtLatency(result.latency_ms)} · tokens in {fmtNum(result.tokens_input)} / out{' '}
                {fmtNum(result.tokens_output)}
              </span>
              {result.tags.length > 0 ? (
                <div className="tags tags--sm">
                  {result.tags.map((t) => (
                    <span key={t} className="tag">
                      {t}
                    </span>
                  ))}
                </div>
              ) : null}
            </div>
          </div>
          <Button variant="ghost" size="sm" onClick={onClose} aria-label="关闭">
            ×
          </Button>
        </header>

        <div className="modal__body">
          <RetrievedMemoriesBlock memories={result.retrieval_memory ?? ''} />
          <div className="kv kv--grid kv--modal">
            {sections?.map((s) => (
              <div key={s.label} className={s.tone === 'danger' || s.tone === 'success' ? 'span-2' : ''}>
                <dt>{s.label}</dt>
                <dd>
                  <pre className={`pre pre--${s.tone}`}>{s.value}</pre>
                </dd>
              </div>
            ))}
          </div>
        </div>

        <footer className="modal__foot">
          <div className="modal__foot-hint muted">提示：按 ESC 可快速关闭</div>
          <Button variant="secondary" onClick={onClose}>
            关闭
          </Button>
        </footer>
      </div>
    </div>
  );
}

function RetrievedMemoriesBlock({ memories }: { memories: string }) {
  if (!memories) return null;
  return (
    <details className="collapse-block" open>
      <summary className="collapse-block__summary">
        <span className="collapse-block__marker" />
        <strong>🔍 检索上下文（Retrieved Memories）</strong>
        <span className="muted">将作为 RAG 输入的一部分注入</span>
      </summary>
      <pre className="pre pre--memory">{memories}</pre>
    </details>
  );
}
