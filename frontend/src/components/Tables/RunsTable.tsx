import { useMemo } from 'react';
import { Link } from 'react-router-dom';
import type { RunSummary } from '@/types';
import { StatusBadge, PhaseBadge } from '../UI/Badge';
import { Button } from '../UI/Button';
import { RateBar } from '../UI/RateBar';
import { SortHeader, nextDir, type SortableColumn } from './tableUtils';
import type { SortDir } from '@/utils/sort';
import { fmtDate, fmtDurationBetween, fmtNum } from '@/utils/format';

const COLS: SortableColumn[] = [
  { key: 'id', label: 'ID', sortable: true, width: '72px', align: 'right' },
  { key: 'version', label: '版本', sortable: true, width: '110px' },
  { key: 'phase', label: '阶段', sortable: true, width: '110px' },
  { key: 'start_time', label: '开始时间', sortable: true, width: '160px' },
  { key: 'duration', label: '耗时', sortable: false, width: '90px' },
  { key: 'tokens', label: 'Token 输入/输出', sortable: false, width: '140px', align: 'right' },
  { key: 'total_cases', label: '用例数', sortable: true, width: '80px', align: 'right' },
  { key: 'pass_rate', label: '通过率', sortable: true, width: '160px' },
  { key: 'status', label: '状态', sortable: true, width: '120px' },
  { key: 'actions', label: '操作', sortable: false, width: '150px', align: 'right' },
];

export interface RunsTableProps {
  items: RunSummary[];
  sortKey?: string;
  sortDir?: SortDir;
  onSort: (key: string, dir: SortDir) => void;
  onDelete?: (run: RunSummary) => void;
  deletingId?: string | null;
}

export function RunsTable({ items, sortKey, sortDir, onSort, onDelete, deletingId }: RunsTableProps) {
  const toggleSort = (key: string) => {
    onSort(key, sortKey === key ? (sortDir === 'desc' ? 'asc' : 'desc') : nextDir());
  };

  const rows = useMemo(
    () =>
      items.map((r) => {
        const tone = r.pass_rate >= 0.9 ? 'success' : r.pass_rate >= 0.7 ? 'warning' : 'danger';
        return { r, tone };
      }),
    [items],
  );

  return (
    <div className="table-wrap">
      <table className="table table--runs">
        <thead>
          <tr>
            {COLS.map((c) => (
              <th key={c.key} style={{ width: c.width, textAlign: c.align ?? 'left' }}>
                <SortHeader column={c} currentKey={sortKey} currentDir={sortDir} onToggle={toggleSort} />
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.length === 0 ? (
            <tr>
              <td colSpan={COLS.length} className="table__empty">
                暂无运行记录
              </td>
            </tr>
          ) : (
            rows.map(({ r, tone }) => (
              <tr key={r.id} className="table__row">
                <td align="right">
                  <Link to={`/runs/${r.id}`} className="link-strong">#{r.id}</Link>
                </td>
                <td>
                  <code className="chip">{r.version}</code>
                </td>
                <td><PhaseBadge phase={r.phase} /></td>
                <td className="mono">{fmtDate(r.start_time)}</td>
                <td className="mono">{fmtDurationBetween(r.start_time, r.end_time)}</td>
                <td align="right" className="mono">
                  {fmtNum(r.tokens_input ?? 0)}/{fmtNum(r.tokens_output ?? 0)}
                </td>
                <td align="right">{fmtNum(r.total_cases)}</td>
                <td>
                  <RateBar value={r.pass_rate} tone={tone as 'success' | 'warning' | 'danger'} />
                </td>
                <td>
                  <div className="flex items-center gap-6">
                    <StatusBadge status={r.status ?? 'completed'} />
                    {r.error_message ? (
                      <span className="table__hint" title={r.error_message}>
                        错误：{r.error_message.slice(0, 40)}
                      </span>
                    ) : null}
                  </div>
                </td>
                <td align="right">
                  <div className="flex items-center gap-6" style={{ justifyContent: 'flex-end' }}>
                    <Link to={`/runs/${r.id}`}>
                      <Button variant="ghost" size="sm">详情</Button>
                    </Link>
                    {onDelete && r.status !== 'running' ? (
                      <Button
                        variant="danger"
                        size="sm"
                        loading={deletingId === String(r.id)}
                        onClick={() => onDelete(r)}
                      >
                        删除
                      </Button>
                    ) : null}
                  </div>
                </td>
              </tr>
            ))
          )}
        </tbody>
      </table>
    </div>
  );
}
