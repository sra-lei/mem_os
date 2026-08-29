import { Link } from 'react-router-dom';
import type { CaseHistoryItem } from '@/types';
import { StatusBadge, PassBadge } from '../UI/Badge';
import { fmtDate, fmtLatency, fmtNum } from '@/utils/format';

export function CaseHistoryTable({
  items,
  onOpenCompare,
}: {
  items: CaseHistoryItem[];
  onOpenCompare: (id: number) => void;
}) {
  if (items.length === 0) {
    return (
      <div className="table__empty">
        该用例暂无历史运行记录
      </div>
    );
  }
  return (
    <div className="table-wrap">
      <table className="table table--history">
        <thead>
          <tr>
            <th style={{ width: '80px' }}>结果ID</th>
            <th style={{ width: '120px' }}>运行</th>
            <th>版本</th>
            <th style={{ width: '160px' }}>运行时间</th>
            <th style={{ width: '90px' }}>结果</th>
            <th style={{ width: '120px' }}>耗时</th>
            <th style={{ width: '140px' }}>Tokens</th>
            <th style={{ width: '120px', textAlign: 'right' }}>操作</th>
          </tr>
        </thead>
        <tbody>
          {items.map((h) => (
            <tr key={h.id}>
              <td className="mono">·{fmtNum(h.id)}</td>
              <td>
                <Link to={`/runs/${h.run_id}`} className="link-strong">{h.run_name}</Link>
              </td>
              <td><code className="chip">{h.run_version}</code></td>
              <td className="mono">{fmtDate(h.start_time)}</td>
              <td><PassBadge passed={h.passed} /></td>
              <td className="mono">{fmtLatency(h.latency_ms)}</td>
              <td className="mono">
                in {fmtNum(h.tokens_input)} / out {fmtNum(h.tokens_output)}
              </td>
              <td style={{ textAlign: 'right' }}>
                <button
                  type="button"
                  className="btn btn--link btn--sm"
                  onClick={() => onOpenCompare(h.id)}
                >
                  查看详细
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
// 未使用的 StatusBadge 引入是为了保持和 RunsTable 一致性导出；避免 lint 报错
export type _StatusBadgeCompat = typeof StatusBadge;
