/**
 * 记忆管理 · 用户列表页（/memories）
 *
 * 数据源：memories.db 的 struct_memories / conv_messages / conv_meta 三表聚合
 * （后端 aggregate_users）。每个评测 case（user_id = test_id）即一个「用户」。
 */
import { useMemo, useState } from 'react';
import { Link } from 'react-router-dom';
import { memoriesApi } from '@/api/memories';
import { useAsync } from '@/hooks/useAsync';
import { PageHeader } from './DashboardPage';
import { TableSkeleton, useAsyncErrorToast } from '@/components/UI';
import { fmtDate } from '@/utils/format';

const STATUS_TONE: Record<string, 'warning' | 'success' | 'danger' | 'info' | 'muted'> = {
  PENDING: 'warning',
  EXTRACTING: 'info',
  SAVING_SQLITE: 'info',
  SAVING_VECTOR: 'info',
  COMPLETED: 'success',
  FAILED: 'danger',
};

export function MemoryUsersPage() {
  const { data, loading, error } = useAsync((_signal) => memoriesApi.listUsers(), []);
  useAsyncErrorToast(error, '加载用户记忆列表失败');

  const [search, setSearch] = useState('');

  const users = useMemo(() => {
    const list = data ?? [];
    const kw = search.trim().toLowerCase();
    if (!kw) return list;
    return list.filter(
      (u) => u.user_id.toLowerCase().includes(kw) || String(u.fact_count).includes(kw),
    );
  }, [data, search]);

  const totalFacts = useMemo(() => (data ?? []).reduce((s, u) => s + u.fact_count, 0), [data]);

  return (
    <div className="page">
      <PageHeader
        eyebrow="Memory Admin"
        title="记忆管理"
        desc={`按用户查看与维护已沉淀的记忆（数据源 memories.db，SQLite 权威源）。当前 ${data?.length ?? 0} 个用户 · 共 ${totalFacts} 条事实。评测 case 的记忆在提取后自动落入对应「用户」。`}
      />

      <section className="card">
        <div className="filter-bar">
          <input
            type="search"
            className="input input--search input--grow"
            placeholder="搜索用户 ID…"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
          />
          <div className="muted filter-summary">
            {search ? (
              <span>
                匹配 <strong>{users.length}</strong> 个用户
              </span>
            ) : (
              <span>
                共 <strong>{data?.length ?? 0}</strong> 个用户
              </span>
            )}
          </div>
        </div>

        {loading ? (
          <TableSkeleton cols={5} rows={10} />
        ) : (
          <div className="table-wrap">
            <table className="table">
              <colgroup>
                <col style={{ width: '30%' }} />
                <col style={{ width: '90px' }} />
                <col style={{ width: '26%' }} />
                <col style={{ width: '90px' }} />
                <col style={{ width: '150px' }} />
                <col style={{ width: '170px' }} />
                <col style={{ width: '110px' }} />
              </colgroup>
              <thead>
                <tr>
                  <th style={{ textAlign: 'left' }}>用户 / 记忆主体</th>
                  <th>事实数</th>
                  <th style={{ textAlign: 'left' }}>类别分布（事实）</th>
                  <th>原文条数</th>
                  <th>会话状态</th>
                  <th>最近活动</th>
                  <th>操作</th>
                </tr>
              </thead>
              <tbody>
                {users.length === 0 ? (
                  <tr>
                    <td colSpan={7} className="table__empty">
                      暂无记忆数据 —— 先运行评测（struct provider 提取入库）后这里会出现每个 case 用户的记忆。
                    </td>
                  </tr>
                ) : (
                  users.map((u) => (
                    <tr key={u.user_id} className="table__row">
                      <td>
                        <div className="table__primary">
                          <span className="table__primary-name mono">{u.user_id}</span>
                          <span className="table__hint">
                            {u.session_count} 个会话 · {u.message_count} 条原文消息
                          </span>
                        </div>
                      </td>
                      <td style={{ textAlign: 'center' }}>
                        <strong className="num num--success">{u.fact_count}</strong>
                      </td>
                      <td>
                        <CategoryChips categories={u.categories} />
                      </td>
                      <td style={{ textAlign: 'center' }} className="mono">
                        {u.message_count}
                      </td>
                      <td>
                        <StatusChips statuses={u.conv_status} />
                      </td>
                      <td className="mono" style={{ textAlign: 'center', whiteSpace: 'nowrap' }}>
                        {u.latest_activity ? fmtDate(u.latest_activity) : '—'}
                      </td>
                      <td>
                        <Link className="btn btn--ghost btn--sm" to={`/memories/${encodeURIComponent(u.user_id)}`}>
                          管理记忆 →
                        </Link>
                      </td>
                    </tr>
                  ))
                )}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </div>
  );
}

function CategoryChips({ categories }: { categories: Record<string, number> }) {
  const entries = Object.entries(categories).sort((a, b) => b[1] - a[1]);
  if (entries.length === 0) return <span className="muted">—</span>;
  const show = entries.slice(0, 5);
  const rest = entries.length - show.length;
  return (
    <span className="tags tags--sm">
      {show.map(([cat, n]) => (
        <span key={cat} className="tag tag--muted">
          {cat} {n}
        </span>
      ))}
      {rest > 0 ? <span className="tag">+{rest}</span> : null}
    </span>
  );
}

function StatusChips({ statuses }: { statuses: Record<string, number> }) {
  const entries = Object.entries(statuses);
  if (entries.length === 0) return <span className="muted">—</span>;
  return (
    <span className="tags tags--sm">
      {entries.map(([st, n]) => (
        <span
          key={st}
          className={`badge badge--${STATUS_TONE[st] ?? 'muted'}`}
          title={`${n} 个会话处于 ${st}`}
        >
          {st}×{n}
        </span>
      ))}
    </span>
  );
}
