/**
 * 纯函数：颜色 & 文案映射。
 * 与 styles/vars.css 中的 CSS 变量保持一致。
 */
import type { PhaseKey, RunStatus } from '@/types';

/**
 * Labels use DB's real PhaseKey values:
 *   base          → single-turn scenario tests
 *   multi_session → multi-turn / session retention tests
 *   proactive     → proactive-memory / cross-domain orchestration tests
 */
export const PHASE_LABEL: Record<PhaseKey, string> = {
  base: '基础 (Base)',
  multi_session: '多轮 (Multi-Session)',
  proactive: '主动 (Proactive)',
};

export const STATUS_LABEL: Record<RunStatus, string> = {
  pending: '等待中',
  running: '运行中',
  completed: '已完成',
  failed: '失败',
  cancelled: '已取消',
};

/** 取 phase 对应 CSS 颜色名（与 styles 同步）。
 *  真实 DB 只有 3 个 phase，我们把旧的 4 色 palette 复用前 3 个即可。
 */
export const PHASE_COLOR_VAR: Record<PhaseKey, string> = {
  base:          'var(--color-phase-memory)',   // green
  multi_session: 'var(--color-phase-prompt)',   // purple
  proactive:     'var(--color-phase-answer)',   // blue
};

/** 版本号取颜色：按哈希稳定散列到 6 个品牌色 */
const VERSION_PALETTE = [
  '#4a6cf7',
  '#8a5cf7',
  '#12b76a',
  '#f79009',
  '#d92d20',
  '#0ea5e9',
];

export function versionColor(version: string): string {
  if (!version) return VERSION_PALETTE[0];
  let h = 0;
  for (let i = 0; i < version.length; i++) {
    h = (h * 31 + version.charCodeAt(i)) >>> 0;
  }
  return VERSION_PALETTE[h % VERSION_PALETTE.length];
}

export const STATUS_TONE: Record<
  RunStatus,
  'neutral' | 'progress' | 'success' | 'danger' | 'muted'
> = {
  pending: 'neutral',
  running: 'progress',
  completed: 'success',
  failed: 'danger',
  cancelled: 'muted',
};

/** 通过率 → 颜色（用于表格/柱状图） */
export function rateColor(rate: number | null | undefined): string {
  if (rate == null || Number.isNaN(rate)) return 'var(--color-text-secondary)';
  if (rate >= 0.9) return 'var(--color-success)';
  if (rate >= 0.7) return 'var(--color-warning)';
  return 'var(--color-danger)';
}
