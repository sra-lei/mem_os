import { useMemo } from 'react';
import { Link } from 'react-router-dom';
import type { RunSummary } from '@/types';
import { StatusBadge, PhaseBadge } from '../UI/Badge';
import { Button } from '../UI/Button';
import { RateBar } from '../UI/RateBar';
import { SortHeader, nextDir, type SortableColumn } from './tableUtils';
import type { SortDir } from '@/utils/sort';
import { fmtDate, fmtRate, fmtDurationBetween, fmtNum } from '@/utils/format';

const COLS: SortableColumn[] = [
  { key: 'id', label: 'ID', sortable: true, width: '72px', align: 'right' },
  { key: 'name', label: '运行名称', sortable: true },
  { key: 'version', label: '版本', sortable: true, width: '110px' },
  { key: 'phase', label: '阶段', sortable: true, width: '110px' },
  { key: 'start_time', label: '开始时间', sortable: true, width: '160px' },
  { key: 'duration', label: '耗时', sortable: false, width: '90px' },
  { key: 'total_cases', label: '用例数', sortable: true, width: '80px', align: 'right' },
  { key: 'pass_rate', label: '通过率', sortable: true, width: '180px' },
  { key: 'status', label: '状态', sortable: true, width: '100px' },
  { key: 'actions', label: '操作', sortable: false, width: '90px', align: 'right' },
];

export interface RunsTableProps {
  items: RunSummary[];
  sortKey?: string;
  sortDir?: SortDir;
  onSort: (key: string, dir: SortDir) => void;
}

export function RunsTable({ items, sortKey, sortDir, onSort }: RunsTableProps) {
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
                <td align="right">#{r.id}</td>
                <td>
                  <div className="table__primary">
                    <Link to={`/runs/${r.id}`} className="link-strong">
                      {r.name}
                    </Link>
                    {r.error_message ? (
                      <span className="table__hint" title={r.error_message}>
                        错误：{r.error_message.slice(0, 80)}
                      </span>
                    ) : null}
                  </div>
                </td>
                <td>
                  <code className="chip">{r.version}</code>
                </td>
                <td><PhaseBadge phase={r.phase} /></td>
                <td className="mono">{fmtDate(r.start_time)}</td>
                <td className="mono">{fmtDurationBetween(r.start_time, r.end_time)}</td>
                <td align="right">{fmtNum(r.total_cases)}</td>
                <td>
                  <div className="flex items-center gap-8">
                    <RateBar value={r.pass_rate} tone={tone as 'success' | 'warning' | 'danger'} />
                    <span className={`num num--${tone}`}>{fmtRate(r.pass_rate)}</span>
                  </div>
                </td>
                <td><StatusBadge status={r.status} /></td>
                <td align="right">
                  <Link to={`/runs/${r.id}`}>
                    <Button variant="ghost" size="sm">详情</Button>
                  </Link>
                </td>
              </tr>
            ))
          )}
        </tbody>
      </table>
    </div>
  );
}
