import { useMemo, useState } from 'react';
import type { TestCaseResult } from '@/types';
import { PhaseBadge, PassBadge } from '../UI/Badge';
import { fmtLatency, fmtNum } from '@/utils/format';
import { Button } from '../UI/Button';

type TabKey = 'passed' | 'skipped';

export function PassedAccordion({
  results,
  onOpenCompare,
}: {
  results: TestCaseResult[];
  onOpenCompare: (id: string | number) => void;
}) {
  const { passed, skipped } = useMemo(() => {
    const total = results.length;
    const passedCount = results.filter((r) => r.passed).length;
    const failedCount = results.filter((r) => !r.passed).length;
    const skippedLocal = total - passedCount - failedCount;
    return {
      passed: results.filter((r) => r.passed),
      skipped: skippedLocal > 0 ? results.slice(passedCount, passedCount + skippedLocal) : [],
    };
  }, [results]);

  const [tab, setTab] = useState<TabKey>('passed');
  const [openId, setOpenId] = useState<string | number | null>(null);
  const list = tab === 'passed' ? passed : skipped;

  return (
    <section className="card accordion-wrap">
      <div className="accordion-wrap__head">
        <div className="tabs" role="tablist">
          <button
            className={`tab ${tab === 'passed' ? 'is-active' : ''}`}
            onClick={() => setTab('passed')}
          >
            通过 <span className="chip">{fmtNum(passed.length)}</span>
          </button>
          <button
            className={`tab ${tab === 'skipped' ? 'is-active' : ''}`}
            onClick={() => setTab('skipped')}
          >
            跳过 / 其他 <span className="chip">{fmtNum(skipped.length)}</span>
          </button>
        </div>
        <div className="accordion-wrap__hint muted">
          共 {fmtNum(list.length)} 条，展开查看期望 / 实际 / 理由
        </div>
      </div>
      {list.length === 0 ? (
        <div className="empty-state empty-state--neutral">
          <strong>无记录</strong>
          <span className="muted">该分组下暂无内容。</span>
        </div>
      ) : (
        <ul className="accordion-list">
          {list.map((r) => {
            const opened = openId === r.id;
            return (
              <li key={r.id} className={`accordion-item ${opened ? 'is-open' : ''}`}>
                <button
                  type="button"
                  className="accordion-item__head"
                  onClick={() => setOpenId(opened ? null : r.id)}
                  aria-expanded={opened}
                >
                  <span className="accordion-item__col">
                    <strong>#{r.case_id}</strong> <span className="muted">{r.case_name}</span>
                  </span>
                  <span className="accordion-item__col">
                    <PhaseBadge phase={r.phase} />
                    <PassBadge passed={!!r.passed} />
                    <span className="muted mono">{fmtLatency(r.latency_ms)} · tokens {fmtNum(r.tokens_input ?? 0)}/{fmtNum(r.tokens_output ?? 0)}</span>
                  </span>
                  <span className="accordion-item__arrow" aria-hidden>{opened ? '−' : '+'}</span>
                </button>
                {opened ? (
                  <div className="accordion-item__body">
                    <dl className="kv kv--grid">
                      <div>
                        <dt>User Input</dt>
                        <dd><pre className="pre">{r.user_input}</pre></dd>
                      </div>
                      <div>
                        <dt>Retrieval Memory</dt>
                        <dd><pre className="pre pre--memory">{r.retrieval_memory || '—'}</pre></dd>
                      </div>
                      <div className="span-2">
                        <dt>Expected Output（期望）</dt>
                        <dd><pre className="pre pre--success">{r.expected_output}</pre></dd>
                      </div>
                      <div className="span-2">
                        <dt>Actual Output（实际）</dt>
                        <dd><pre className="pre pre--answer">{r.actual_output}</pre></dd>
                      </div>
                      <div className="span-2">
                        <dt>Judge Reason（判分理由）</dt>
                        <dd>
                          <pre className="pre pre--warning">{r.judge_reason}</pre>
                          <div className="accordion-item__actions">
                            <Button variant="secondary" size="sm" onClick={() => onOpenCompare(r.id)}>
                              对比视图 →
                            </Button>
                          </div>
                        </dd>
                      </div>
                    </dl>
                  </div>
                ) : null}
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
}
