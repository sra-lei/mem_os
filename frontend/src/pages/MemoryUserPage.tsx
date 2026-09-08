/**
 * 记忆管理 · 单用户事实管理页（/memories/:userId）
 *
 * 浏览/检索该用户全部结构化记忆，支持：新增(upsert) / 编辑 fact/value/confidence /
 * 删除单条 / 查看来源会话原文 / 清空（可选重置 ingest 门禁）/ 重建投影。
 *
 * 一致性语义：写操作 SQLite（权威）先提交 → 投影尽力同步；若响应 projection=failed，
 * 提示「已保存本地库，投影待修复」，可点「重建投影」兜底。
 */
import { useMemo, useState } from 'react';
import { Link, useParams, useSearchParams } from 'react-router-dom';
import { memoriesApi } from '@/api/memories';
import type {
  FactCreatePayload,
  MemoryFact,
  MemWriteResponse,
  MemoryMessage,
} from '@/api/memories';
import { useAsync } from '@/hooks/useAsync';
import { useToast, useAsyncErrorToast, TableSkeleton, Button } from '@/components/UI';
import { Modal, ConfirmModal } from '@/components/UI';
import { PageHeader, CardHeader, Pager } from './DashboardPage';
import { fmtDate } from '@/utils/format';

const LIMIT = 50;

interface FormState {
  category: string;
  key: string;
  fact: string;
  value: string;
  confidence: string; // string,便于输入
}

const EMPTY_FORM: FormState = { category: 'finance', key: '', fact: '', value: '', confidence: '0.9' };

type ConfirmTarget =
  | { kind: 'delete'; fact: MemoryFact }
  | { kind: 'clear-facts' }
  | { kind: 'clear-reextract' }
  | { kind: 'rebuild' }
  | null;

export function MemoryUserPage() {
  const { userId = '' } = useParams();
  const toast = useToast();
  const [params, setParams] = useSearchParams();

  const category = params.get('category') ?? '';
  const q = params.get('q') ?? '';
  const page = Math.max(1, Number(params.get('page') ?? 1) || 1);

  const factsAsync = useAsync(
    (signal) =>
      memoriesApi.listFacts(
        userId,
        { category: category || undefined, search: q || undefined, offset: (page - 1) * LIMIT, limit: LIMIT },
        signal,
      ),
    [userId, category, q, page],
  );
  useAsyncErrorToast(factsAsync.error, '加载事实失败');

  const summaryAsync = useAsync(
    async (_signal) => {
      const list = await memoriesApi.listUsers();
      return list.find((u) => u.user_id === userId) ?? null;
    },
    [userId],
  );

  const facts = factsAsync.data?.items ?? [];
  const total = factsAsync.data?.total ?? 0;
  const pages = Math.max(1, Math.ceil(total / LIMIT));

  // ---- 表单弹窗状态 ----
  const [editing, setEditing] = useState<{ mode: 'create' } | { mode: 'edit'; fact: MemoryFact } | null>(null);
  const [confirmTarget, setConfirmTarget] = useState<ConfirmTarget>(null);
  const [msgView, setMsgView] = useState<{ conversationId: string; msgs: MemoryMessage[]; loading: boolean } | null>(null);
  const [busy, setBusy] = useState(false);

  // 已加载事实里提炼「category → 既有 key」提示（防止同义新键）
  const keyHints = useMemo(() => {
    const map = new Map<string, string[]>();
    for (const f of facts) {
      const arr = map.get(f.category) ?? [];
      if (!arr.includes(f.key)) arr.push(f.key);
      map.set(f.category, arr);
    }
    return map;
  }, [facts]);

  const setParam = (key: string, value: string) => {
    const np = new URLSearchParams(params);
    if (value) np.set(key, value);
    else np.delete(key);
    if (key !== 'page') np.set('page', '1');
    setParams(np, { replace: true });
  };

  const doRefresh = () => {
    void factsAsync.refresh();
    void summaryAsync.refresh();
  };

  // ---- 写操作统一反馈 ----
  const handleWrite = async (p: Promise<MemWriteResponse>, okMsg: string) => {
    setBusy(true);
    try {
      const res = await p;
      if (res.warning) {
        toast.push({
          kind: 'warning',
          title: res.projection === 'failed' ? `${okMsg}（仅本地库）` : okMsg,
          desc: res.warning,
          autoCloseMs: 9000,
        });
      } else {
        toast.push({ kind: 'success', title: okMsg });
      }
      setConfirmTarget(null);
      setEditing(null);
      doRefresh();
    } catch (err) {
      toast.error(err, '操作失败');
    } finally {
      setBusy(false);
    }
  };

  const openMessages = async (conversationId: string) => {
    setMsgView({ conversationId, msgs: [], loading: true });
    try {
      const msgs = await memoriesApi.messages(userId, conversationId);
      setMsgView({ conversationId, msgs, loading: false });
    } catch (err) {
      setMsgView(null);
      toast.error(err, '加载原文失败');
    }
  };

  const summary = summaryAsync.data;

  return (
    <div className="page">
      <PageHeader
        eyebrow="Memory Admin"
        title={<span className="mono">{userId}</span>}
        desc={
          <>
            该用户的记忆事实（SQLite 权威 + 向量投影尽力同步）。修改后重跑评测即可验证检索注入效果。
            <br />
            <Link className="link-muted" to="/memories">← 返回用户列表</Link>
          </>
        }
        right={
          <Button variant="primary" onClick={() => setEditing({ mode: 'create' })}>
            ＋ 新增记忆
          </Button>
        }
      />

      {/* 概览条 */}
      <section className="card">
        <div className="mem-overview-strip">
          <span>
            事实 <strong className="num num--success">{summary?.fact_count ?? '…'}</strong>
          </span>
          <span>
            原文消息 <strong>{summary?.message_count ?? '…'}</strong>
          </span>
          <span>
            会话 <strong>{summary?.session_count ?? '…'}</strong>
          </span>
          <span>
            最近活动{' '}
            <span className="mono">{summary?.latest_activity ? fmtDate(summary.latest_activity) : '—'}</span>
          </span>
          {Object.entries(summary?.conv_status ?? {}).map(([st, n]) => (
            <span key={st} className="badge badge--muted">
              {st}×{n}
            </span>
          ))}
        </div>
      </section>

      {/* 事实表 */}
      <section className="card">
        <div className="filter-bar filter-bar--wrap">
          <select
            className="select"
            value={category}
            onChange={(e) => setParam('category', e.target.value)}
            title="按类别过滤"
          >
            <option value="">全部类别</option>
            {Object.keys(summary?.categories ?? {})
              .sort()
              .map((c) => (
                <option key={c} value={c}>
                  {c}（{summary?.categories[c]}）
                </option>
              ))}
          </select>
          <input
            type="search"
            className="input input--search input--grow"
            placeholder="搜索 fact / key / value 内容…（回车生效）"
            defaultValue={q}
            onKeyDown={(e) => {
              if (e.key === 'Enter') setParam('q', (e.target as HTMLInputElement).value.trim());
            }}
            onBlur={(e) => {
              if ((e.target as HTMLInputElement).value.trim() !== q) {
                setParam('q', (e.target as HTMLInputElement).value.trim());
              }
            }}
          />
          <div className="muted filter-summary">
            共 <strong>{total}</strong> 条
            {category ? ` · 类别：${category}` : ''}
            {q ? ` · 搜索：${q}` : ''}
          </div>
        </div>

        {factsAsync.loading ? (
          <TableSkeleton cols={6} rows={12} />
        ) : (
          <div className="table-wrap">
            <table className="table">
              <colgroup>
                <col style={{ width: '34%' }} />
                <col style={{ width: '120px' }} />
                <col style={{ width: '18%' }} />
                <col style={{ width: '90px' }} />
                <col style={{ width: '150px' }} />
                <col style={{ width: '150px' }} />
              </colgroup>
              <thead>
                <tr>
                  <th style={{ textAlign: 'left' }}>记忆事实（fact）</th>
                  <th>category / key</th>
                  <th style={{ textAlign: 'left' }}>value</th>
                  <th>置信</th>
                  <th>更新时间</th>
                  <th>操作</th>
                </tr>
              </thead>
              <tbody>
                {facts.length === 0 ? (
                  <tr>
                    <td colSpan={6} className="table__empty">
                      {total === 0 ? '该用户暂无记忆事实。' : '没有匹配当前筛选的事实。'}
                    </td>
                  </tr>
                ) : (
                  facts.map((f) => (
                    <tr key={f.id} className="table__row">
                      <td>
                        <div className="table__primary">
                          <span className="mem-fact__text">{f.fact}</span>
                          {f.previous_fact ? (
                            <span className="mem-fact__prev">
                              上次：{f.previous_fact}
                            </span>
                          ) : null}
                          {f.source_conversation_id ? (
                            <span className="mem-fact__src mono">
                              src: {f.source_conversation_id}
                              {f.source_chunk_id ? ` / ${f.source_chunk_id}` : ''}
                            </span>
                          ) : null}
                        </div>
                      </td>
                      <td>
                        <span className="tag tag--muted">{f.category}</span>{' '}
                        <span className="mono muted">{f.key}</span>
                      </td>
                      <td>
                        <span className="mono" title={f.value}>
                          {(f.value ?? '').slice(0, 60) || '—'}
                        </span>
                      </td>
                      <td style={{ textAlign: 'center' }}>
                        <span className="mono">{Math.round(f.confidence * 100)}%</span>
                      </td>
                      <td className="mono" style={{ textAlign: 'center', whiteSpace: 'nowrap' }}>
                        {fmtDate(f.updated_at)}
                      </td>
                      <td style={{ textAlign: 'center', whiteSpace: 'nowrap' }}>
                        {f.source_conversation_id ? (
                          <Button variant="ghost" size="sm" onClick={() => openMessages(f.source_conversation_id)}>
                            原文
                          </Button>
                        ) : null}
                        <Button variant="ghost" size="sm" onClick={() => setEditing({ mode: 'edit', fact: f })}>
                          编辑
                        </Button>
                        <Button variant="ghost" size="sm" onClick={() => setConfirmTarget({ kind: 'delete', fact: f })}>
                          删除
                        </Button>
                      </td>
                    </tr>
                  ))
                )}
              </tbody>
            </table>
          </div>
        )}

        <Pager page={Math.min(page, pages)} pages={pages} total={total} limit={LIMIT} onChange={(p) => setParam('page', String(p))} />
      </section>

      {/* 危险操作区 */}
      <section className="card">
        <CardHeader
          title="整用户操作"
          subtitle="写操作先落 SQLite（权威），再尽力同步向量投影；投影失败时可点「重建投影」修复。"
        />
        <div style={{ padding: '0 16px 16px' }}>
          <div className="mem-danger-row">
            <div style={{ flex: 1 }}>
              <strong>重建投影</strong>
              <p className="muted mem-card__desc">
                把该用户 SQLite 事实全量重新嵌入向量库（Milvus）。用于修复投影同步失败/漂移；改完记忆后重跑评测前建议执行。
              </p>
            </div>
            <Button variant="secondary" onClick={() => setConfirmTarget({ kind: 'rebuild' })}>
              重建投影
            </Button>
          </div>
          <div className="mem-danger-row">
            <div style={{ flex: 1 }}>
              <strong>清空记忆（仅事实）</strong>
              <p className="muted mem-card__desc">
                删除该用户全部 struct_memories，保留 conv_messages 原文与 conv_meta 处理状态（重跑评测不会重新提取）。
              </p>
            </div>
            <Button variant="danger" onClick={() => setConfirmTarget({ kind: 'clear-facts' })}>
              清空
            </Button>
          </div>
          <div className="mem-danger-row">
            <div style={{ flex: 1 }}>
              <strong>清空并允许重新提取</strong>
              <p className="muted mem-card__desc">
                在上一项基础上把 conv_meta 重置为 PENDING（打开 ingest 门禁）。下次 ingest/评测会从原文重新抽取
                —— 会调用提取 LLM（有费用），verbatim 兜底句按内容指纹幂等、不重复堆积。
              </p>
            </div>
            <Button variant="danger" onClick={() => setConfirmTarget({ kind: 'clear-reextract' })}>
              清空 + 重置门禁
            </Button>
          </div>
        </div>
      </section>

      {/* 编辑/新增弹窗 */}
      {editing ? (
        <FactFormModal
          userId={userId}
          mode={editing.mode === 'create' ? 'create' : 'edit'}
          fact={editing.mode === 'edit' ? editing.fact : null}
          keyHints={keyHints}
          busy={busy}
          onClose={() => setEditing(null)}
          onSubmit={(payload, factId) =>
            handleWrite(
              editing.mode === 'create'
                ? memoriesApi.createFact(userId, payload)
                : memoriesApi.updateFact(userId, factId ?? '', {
                    fact: payload.fact,
                    value: payload.value,
                    confidence: payload.confidence,
                  }),
              editing.mode === 'create' ? '记忆已新增/覆盖' : '记忆已更新',
            )
          }
        />
      ) : null}

      {/* 原文查看 */}
      {msgView ? (
        <Modal
          open
          size="lg"
          title={`会话原文 · ${msgView.conversationId}`}
          subtitle={<span className="mono muted">{userId}（只读）</span>}
          onClose={() => setMsgView(null)}
        >
          {msgView.loading ? (
            <TableSkeleton cols={1} rows={10} />
          ) : (
            <div className="pre pre--neutral mem-msg-list">
              {msgView.msgs.length === 0 ? (
                <div className="muted">该会话没有原文消息</div>
              ) : (
                msgView.msgs.map((m) => (
                  <div key={m.seq} className="mem-msg-line">
                    <span className="mem-msg-seq mono">{m.seq}</span>
                    <span className="mem-msg-body">{m.content}</span>
                  </div>
                ))
              )}
            </div>
          )}
        </Modal>
      ) : null}

      {/* 二次确认 */}
      <ConfirmModal
        open={confirmTarget !== null}
        onClose={() => setConfirmTarget(null)}
        onConfirm={() => {
          if (!confirmTarget) return;
          if (confirmTarget.kind === 'delete') {
            void handleWrite(memoriesApi.deleteFact(userId, confirmTarget.fact.id), '记忆已删除');
          } else if (confirmTarget.kind === 'clear-facts') {
            void handleWrite(memoriesApi.clearUser(userId, false), '该用户记忆已清空');
          } else if (confirmTarget.kind === 'clear-reextract') {
            void handleWrite(memoriesApi.clearUser(userId, true), '已清空并重置提取门禁');
          } else if (confirmTarget.kind === 'rebuild') {
            void handleWrite(memoriesApi.rebuildProjection(userId), '投影已重建');
          }
        }}
        busy={busy}
        title={
          confirmTarget?.kind === 'delete'
            ? '删除这条记忆？'
            : confirmTarget?.kind === 'rebuild'
              ? '重建投影？'
              : '危险操作确认'
        }
        confirmText={
          confirmTarget?.kind === 'delete'
            ? '删除'
            : confirmTarget?.kind === 'rebuild'
              ? '重建'
              : '确认执行'
        }
      >
        {confirmTarget?.kind === 'delete' ? (
          <p>
            将删除事实 <strong>{confirmTarget.fact.key}</strong>（{confirmTarget.fact.category}），并同步删除向量投影。SQLite 删除不可撤销。
          </p>
        ) : confirmTarget?.kind === 'clear-facts' ? (
          <p>
            将删除该用户全部 <strong>{total}</strong> 条事实并同步清空投影；conv_messages 原文与会话处理状态保留，重跑评测不会重新提取。
          </p>
        ) : confirmTarget?.kind === 'clear-reextract' ? (
          <p className="callout callout--danger">
            将删除全部事实 + 重置 conv_meta 为 PENDING。下次 ingest/评测会从原文重新调用提取 LLM（有费用）。此操作不可撤销。
          </p>
        ) : confirmTarget?.kind === 'rebuild' ? (
          <p>
            按当前 SQLite 全量事实重建该用户的向量投影（先整用户删除，再批量嵌入重插）。仅影响检索，不改动 SQLite。
          </p>
        ) : null}
      </ConfirmModal>
    </div>
  );
}

// ===========================================================================
// 新增/编辑表单弹窗
// ===========================================================================

function FactFormModal({
  userId,
  mode,
  fact,
  keyHints,
  busy,
  onClose,
  onSubmit,
}: {
  userId: string;
  mode: 'create' | 'edit';
  fact: MemoryFact | null;
  keyHints: Map<string, string[]>;
  busy: boolean;
  onClose: () => void;
  onSubmit: (payload: FactCreatePayload, factId?: string) => void;
}) {
  const [form, setForm] = useState<FormState>(
    fact
      ? {
          category: fact.category,
          key: fact.key,
          fact: fact.fact,
          value: fact.value,
          confidence: String(fact.confidence),
        }
      : { ...EMPTY_FORM },
  );
  const toast = useToast();

  const set = <K extends keyof FormState>(k: K, v: string) => setForm((prev) => ({ ...prev, [k]: v }));

  const submit = () => {
    if (!form.category.trim() || !form.key.trim() || !form.fact.trim() || !form.value.trim()) {
      toast.push({ kind: 'error', title: '表单不完整', desc: 'category / key / fact / value 均不能为空' });
      return;
    }
    const conf = Number(form.confidence);
    if (Number.isNaN(conf) || conf < 0 || conf > 1) {
      toast.push({ kind: 'error', title: '置信度不合法', desc: 'confidence 需在 0~1 之间' });
      return;
    }
    onSubmit(
      {
        category: form.category.trim(),
        key: form.key.trim(),
        fact: form.fact.trim(),
        value: form.value.trim(),
        confidence: conf,
        ...(fact?.source_conversation_id ? { source_conversation_id: fact.source_conversation_id } : {}),
      },
      fact?.id,
    );
  };

  const hints = keyHints.get(form.category) ?? [];

  return (
    <Modal
      open
      title={mode === 'create' ? '新增记忆事实' : `编辑记忆 · ${fact?.key ?? ''}`}
      subtitle={
        mode === 'create' ? (
          <span className="mono muted">
            {userId} —— 同 (category, key) 已存在时按「覆盖 + 旧值归档」处理
          </span>
        ) : (
          <span className="mono muted">
            {userId} · {fact?.category} / {fact?.key}（身份字段不可改；改键 = 删除后新增）
          </span>
        )
      }
      onClose={onClose}
      closeText="取消"
      footer={
        <div className="flex gap-6 justify-end">
          <Button variant="primary" loading={busy} onClick={submit}>
            {mode === 'create' ? '新增' : '保存修改'}
          </Button>
        </div>
      }
    >
      <div className="kv kv--grid kv--modal">
        <label className="span-2">
          <span className="muted">fact（自然语言记忆句，主字段）</span>
          <textarea
            className="input mem-form__textarea"
            rows={3}
            value={form.fact}
            onChange={(e) => set('fact', e.target.value)}
            placeholder="如：用户的支票账户号码是 4429853327"
          />
        </label>
        <label>
          <span className="muted">category</span>
          <input
            className="input"
            list="mem-cat-suggest"
            value={form.category}
            disabled={mode === 'edit'}
            onChange={(e) => set('category', e.target.value)}
          />
          <datalist id="mem-cat-suggest">
            {['finance', 'personal', 'contact', 'preference', 'health', 'family', 'work', 'travel', 'education', 'other'].map((c) => (
              <option key={c} value={c} />
            ))}
          </datalist>
        </label>
        <label>
          <span className="muted">key（同义概念请复用既有 key）</span>
          <input
            className="input"
            value={form.key}
            disabled={mode === 'edit'}
            onChange={(e) => set('key', e.target.value)}
            placeholder="如 checking_account_number"
          />
        </label>
        {hints.length > 0 ? (
          <div className="span-2">
            <span className="muted">该类别既有 key：</span>
            <span className="tags tags--sm">
              {hints.map((k) => (
                <span
                  key={k}
                  className="tag tag--muted"
                  style={{ cursor: 'pointer' }}
                  onClick={() => mode === 'create' && set('key', k)}
                >
                  {k}
                </span>
              ))}
            </span>
          </div>
        ) : null}
        <label>
          <span className="muted">value（精确值）</span>
          <input
            className="input"
            value={form.value}
            onChange={(e) => set('value', e.target.value)}
            placeholder="如 4429853327"
          />
        </label>
        <label>
          <span className="muted">confidence（0~1）</span>
          <input
            className="input"
            type="number"
            min={0}
            max={1}
            step={0.05}
            value={form.confidence}
            onChange={(e) => set('confidence', e.target.value)}
          />
        </label>
      </div>
    </Modal>
  );
}
