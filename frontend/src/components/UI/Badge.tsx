import type { CSSProperties, ReactNode } from 'react';
import { STATUS_TONE, STATUS_LABEL, PHASE_LABEL, PHASE_COLOR_VAR } from '@/utils/color';
import type { PhaseKey, RunStatus } from '@/types';

export interface BadgeProps {
  tone?: 'success' | 'danger' | 'warning' | 'info' | 'neutral' | 'muted' | 'progress';
  children: ReactNode;
  className?: string;
  style?: CSSProperties;
}

export function Badge({ tone = 'neutral', children, className, style }: BadgeProps) {
  return (
    <span className={`badge badge--${tone} ${className ?? ''}`} style={style}>
      {children}
    </span>
  );
}

export function StatusBadge({ status }: { status: RunStatus }) {
  return <Badge tone={STATUS_TONE[status]}>{STATUS_LABEL[status]}</Badge>;
}

export function PhaseBadge({ phase }: { phase: PhaseKey | null | undefined }) {
  if (!phase) return <Badge tone="muted">未指定</Badge>;
  return (
    <span className="phase-badge" style={{ background: PHASE_COLOR_VAR[phase] + '22', color: PHASE_COLOR_VAR[phase] }}>
      {PHASE_LABEL[phase]}
    </span>
  );
}

export function PassBadge({ passed }: { passed: boolean }) {
  return passed ? (
    <Badge tone="success">通过</Badge>
  ) : (
    <Badge tone="danger">失败</Badge>
  );
}
