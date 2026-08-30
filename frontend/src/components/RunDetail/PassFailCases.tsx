import { useMemo, useState } from 'react';
import type { TestCaseResult } from '@/types';
import { PhaseBadge, PassBadge } from '../UI/Badge';
import { fmtLatency, fmtNum } from '@/utils/format';
import { Button } from '../UI/Button';
import { FailedCaseCard } from './FailedCasesGrid';

type TabKey = 'passed' | 'failed';

/** 运行详情：通过/失败合并展示。失败用 failed-card（期望/实际/理由对比），
 *  通过用可展开 accordion。 */
export function PassFailCases({
  results,
  onOpenCompare,
}: {
  results: TestCaseResult[];
  onOpenCompare: (id: string | number) => void;
}) {
  const { passed, failed } = useMemo(
    () => ({
      passed: results.filter((r) => r.passed),
      failed: results.filter((r) => !r.passed),
    }),
    [results],
  );

  const [tab, setTab] = useState<TabKey>('passed');
  const [openId, setOpenId] = useState<string | number | null>(null);
  const list = tab === 'passed' ? passed : failed;

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
            className={`tab ${tab === 'failed' ? 'is-active' : ''}`}
            onClick={() => setTab('failed')}
          >
            失败 <span className="chip">{fmtNum(failed.length)}</span>
          </button>
        </div>
        <div className="accordion-wrap__hint muted">
          {tab === 'failed'
            ? `共 ${fmtNum(failed.length)} 条失败，点击「查看原因」打开对比详情`
            : `共 ${fmtNum(passed.length)} 条通过，展开查看期望 / 实际 / 理由`}
        </div>
      </div>

      {list.length === 0 ? (
        <div className="empty-state empty-state--neutral">
          <strong>无记录</strong>
          <span className="muted">该分组下暂无内容。</span>
        </div>
      ) : tab === 'failed' ? (
        <div className="grid-failed">
          {failed.map((r) => (
            <FailedCaseCard key={r.id} result={r} onOpenCompare={onOpenCompare} />
          ))}
        </div>
      ) : (
        <ul className="accordion-list">
          {passed.map((r) => {
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
