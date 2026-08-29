import { useMemo } from 'react';
import { Link } from 'react-router-dom';
import type { CaseDefinition } from '@/types';
import { PhaseBadge, PassBadge } from '../UI/Badge';
import { Button } from '../UI/Button';
import { SortHeader, nextDir, type SortableColumn } from './tableUtils';
import type { SortDir } from '@/utils/sort';
import { fmtDateShort, fmtRate, fmtNum } from '@/utils/format';

const COLS: SortableColumn[] = [
  { key: 'id', label: 'ID', sortable: true, width: '60px', align: 'right' },
  { key: 'case_id', label: '用例编号', sortable: true, width: '160px' },
  { key: 'case_name', label: '用例名称', sortable: true },
  { key: 'phase', label: '阶段', sortable: true, width: '100px' },
  { key: 'tags', label: '标签', sortable: false, width: '180px' },
  { key: 'total_runs', label: '运行次数', sortable: true, width: '90px', align: 'right' },
  { key: 'pass_rate', label: '历史通过率', sortable: true, width: '140px' },
  { key: 'updated_at', label: '最近更新', sortable: true, width: '120px' },
  { key: 'actions', label: '操作', sortable: false, width: '120px', align: 'right' },
];

export interface CasesTableProps {
  items: CaseDefinition[];
  sortKey?: string;
  sortDir?: SortDir;
  onSort: (key: string, dir: SortDir) => void;
}

export function CasesTable({ items, sortKey, sortDir, onSort }: CasesTableProps) {
  const toggleSort = (key: string) => {
    onSort(key, sortKey === key ? (sortDir === 'desc' ? 'asc' : 'desc') : nextDir());
  };

  const rows = useMemo(
    () =>
      items.map((c) => {
        const total = c.total_runs || 0;
        const rate = total ? c.pass_count / total : 0;
        const tone = rate >= 0.9 ? 'success' : rate >= 0.7 ? 'warning' : 'danger';
        return { c, rate, tone, total };
      }),
    [items],
  );

  return (
    <div className="table-wrap">
      <table className="table table--cases">
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
                暂无匹配的用例
              </td>
            </tr>
          ) : (
            rows.map(({ c, rate, tone, total }) => (
              <tr key={c.id} className="table__row">
                <td align="right">{fmtNum(c.id)}</td>
                <td><code className="chip chip--outline">{c.case_id}</code></td>
                <td>
                  <div className="table__primary">
                    <strong>{c.case_name}</strong>
                    <span className="table__hint">输入：{c.user_input.slice(0, 80)}</span>
                  </div>
                </td>
                <td><PhaseBadge phase={c.phase} /></td>
                <td>
                  <div className="tags">
                    {c.tags.slice(0, 3).map((t) => (
                      <span key={t} className="tag">{t}</span>
                    ))}
                    {c.tags.length > 3 ? <span className="tag tag--muted">+{c.tags.length - 3}</span> : null}
                  </div>
                </td>
                <td align="right">{fmtNum(total)}</td>
                <td>
                  <div className="flex items-center gap-8">
                    <PassBadge passed={rate >= 0.7 && total > 0} />
                    <span className={`num num--${tone}`}>{fmtRate(rate)}</span>
                    <span className="muted">({fmtNum(c.pass_count)}/{fmtNum(total)})</span>
                  </div>
                </td>
                <td className="mono">{fmtDateShort(c.updated_at)}</td>
                <td align="right">
                  <Link to={`/cases/${c.id}`}>
                    <Button variant="ghost" size="sm">历史</Button>
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
