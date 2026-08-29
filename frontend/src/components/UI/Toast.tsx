/**
 * 轻量 Toast：通过 render 层的事件总线触发。
 * 为避免引入外部状态库，只用一个单例订阅 + 本地组件实现。
 */
import { createContext, useCallback, useContext, useEffect, useMemo, useState } from 'react';
import type { ReactNode } from 'react';
import type { ApiError } from '@/types/api';

type ToastKind = 'success' | 'error' | 'info' | 'warning';

interface ToastItem {
  id: number;
  kind: ToastKind;
  title: string;
  desc?: string;
  ts: number;
}

interface ToastApi {
  push: (t: Omit<ToastItem, 'id' | 'ts'> & { autoCloseMs?: number }) => number;
  error: (err: ApiError | Error | unknown, title?: string) => number;
  close: (id: number) => void;
}

const ToastContext = createContext<ToastApi | null>(null);

let idSeq = 1;

export function ToastProvider({ children }: { children: ReactNode }) {
  const [items, setItems] = useState<ToastItem[]>([]);

  const close = useCallback((id: number) => {
    setItems((prev) => prev.filter((x) => x.id !== id));
  }, []);

  const api: ToastApi = useMemo<ToastApi>(() => {
    return {
      push: ({ autoCloseMs = 3500, ...rest }) => {
        const id = idSeq++;
        const item: ToastItem = { id, ts: Date.now(), ...rest };
        setItems((prev) => [...prev, item]);
        if (autoCloseMs > 0) setTimeout(() => close(id), autoCloseMs);
        return id;
      },
      error: (err, title = '操作失败') => {
        const desc =
          err && typeof err === 'object' && 'toUserMessage' in err
            ? (err as ApiError).toUserMessage()
            : (err as Error)?.message || '未知错误';
        return api.push({ kind: 'error', title, desc });
      },
      close,
    };
  }, [close]);

  return (
    <ToastContext.Provider value={api}>
      {children}
      <div className="toast-viewport" role="status" aria-live="polite">
        {items.map((t) => (
          <div key={t.id} className={`toast toast--${t.kind}`}>
            <div className="toast__head">
              <span className={`toast__icon toast__icon--${t.kind}`} aria-hidden />
              <strong className="toast__title">{t.title}</strong>
              <button className="toast__close" onClick={() => close(t.id)} aria-label="关闭">
                ×
              </button>
            </div>
            {t.desc ? <div className="toast__desc">{t.desc}</div> : null}
          </div>
        ))}
      </div>
    </ToastContext.Provider>
  );
}

export function useToast(): ToastApi {
  const ctx = useContext(ToastContext);
  if (!ctx) throw new Error('useToast must be used within ToastProvider');
  return ctx;
}

/** 便捷 hook：首次 mount 时自动消费 async 错误到 Toast */
export function useAsyncErrorToast(error: Error | null, title = '加载失败') {
  const toast = useToast();
  useEffect(() => {
    if (error) toast.error(error, title);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [error?.message]);
}
