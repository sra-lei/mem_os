/** 轻量 Modal：ESC/背景点击关闭 + 滚动锁定（复用全局 .modal CSS）。 */
import { useEffect } from 'react';
import type { ReactNode } from 'react';
import { Button } from './Button';

interface ModalProps {
  open: boolean;
  onClose: () => void;
  title: ReactNode;
  subtitle?: ReactNode;
  children: ReactNode;
  footer?: ReactNode;
  /** 面板宽度（默认 720px，wide=940px） */
  size?: 'md' | 'lg';
  /** 底部「关闭」按钮文案；传 null 则不渲染默认关闭钮 */
  closeText?: ReactNode;
}

export function Modal({
  open,
  onClose,
  title,
  subtitle,
  children,
  footer,
  size = 'md',
  closeText = '关闭',
}: ModalProps) {
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

  if (!open) return null;

  return (
    <div className="modal" role="dialog" aria-modal="true" aria-label={typeof title === 'string' ? title : undefined}>
      <div className="modal__backdrop" onClick={onClose} />
      <div className="modal__panel" role="document" style={size === 'lg' ? { width: 'min(940px, 94vw)' } : undefined}>
        <header className="modal__head">
          <div className="modal__titles">
            <h3 className="modal__title">{title}</h3>
            {subtitle ? <div className="modal__sub">{subtitle}</div> : null}
          </div>
          <Button variant="ghost" size="sm" onClick={onClose} aria-label="关闭">
            ×
          </Button>
        </header>
        <div className="modal__body">{children}</div>
        {(footer || closeText !== null) && (
          <footer className="modal__foot">
            <div className="modal__foot-hint muted">{footer ?? ''}</div>
            {closeText !== null && (
              <Button variant="secondary" onClick={onClose}>
                {closeText}
              </Button>
            )}
          </footer>
        )}
      </div>
    </div>
  );
}

/** 危险操作二次确认对话框（内容区 + 确认/取消按钮）。 */
export function ConfirmModal({
  open,
  onClose,
  onConfirm,
  title,
  children,
  confirmText = '确认删除',
  danger = true,
  busy = false,
}: {
  open: boolean;
  onClose: () => void;
  onConfirm: () => void;
  title: ReactNode;
  children: ReactNode;
  confirmText?: string;
  danger?: boolean;
  busy?: boolean;
}) {
  return (
    <Modal
      open={open}
      onClose={onClose}
      title={title}
      closeText={null}
      footer={
        <div className="flex gap-6 justify-end">
          <Button variant="secondary" onClick={onClose} disabled={busy}>
            取消
          </Button>
          <Button
            variant={danger ? 'danger' : 'primary'}
            loading={busy}
            onClick={() => {
              void onConfirm();
            }}
          >
            {confirmText}
          </Button>
        </div>
      }
    >
      {children}
    </Modal>
  );
}
