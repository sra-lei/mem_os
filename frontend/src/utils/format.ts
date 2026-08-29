/**
 * 纯函数工具：格式化。不包含任何 React 依赖，便于后续单元测试。
 */

/** ISO 字符串（UTC）→ 本地 YYYY-MM-DD HH:mm:ss */
export function fmtDate(iso: string | null | undefined): string {
  if (!iso) return '—';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  const pad = (n: number) => n.toString().padStart(2, '0');
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(
    d.getHours(),
  )}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
}

/** ISO 字符串（UTC）→ 本地 YYYY-MM-DD */
export function fmtDateShort(iso: string | null | undefined): string {
  if (!iso) return '—';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  const pad = (n: number) => n.toString().padStart(2, '0');
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
}

/** 持续时间（毫秒）→ "4m 05s" / "1.2s" / "820ms" */
export function fmtDuration(ms: number | null | undefined): string {
  if (ms == null || Number.isNaN(ms)) return '—';
  if (ms < 1000) return `${Math.round(ms)}ms`;
  if (ms < 60_000) return `${(ms / 1000).toFixed(1)}s`;
  const m = Math.floor(ms / 60_000);
  const s = Math.round((ms % 60_000) / 1000);
  return `${m}m ${s.toString().padStart(2, '0')}s`;
}

/**
 * start_time / end_time ISO 时间差 → 持续时间字符串
 * 若 end 缺失，用当前时间估算 running 场景
 */
export function fmtDurationBetween(
  start: string | null | undefined,
  end: string | null | undefined,
): string {
  if (!start) return '—';
  const s = new Date(start).getTime();
  const e = end ? new Date(end).getTime() : Date.now();
  if (Number.isNaN(s) || Number.isNaN(e) || e < s) return '—';
  return fmtDuration(e - s);
}

/** 通过率（0-1 浮点数）→ 百分比（一位小数） */
export function fmtRate(rate: number | null | undefined): string {
  if (rate == null || Number.isNaN(rate)) return '—';
  return `${(rate * 100).toFixed(1)}%`;
}

/** 单个数值（毫秒）→ "420ms" / "1.2s" */
export function fmtLatency(ms: number | null | undefined): string {
  return fmtDuration(ms);
}

/** 大数字 → 千分位 */
export function fmtNum(n: number | null | undefined): string {
  if (n == null || Number.isNaN(n)) return '—';
  return n.toLocaleString('zh-CN');
}

/** 文本超长裁剪（加省略号） */
export function truncate(s: string | null | undefined, max = 120): string {
  if (!s) return '—';
  return s.length > max ? `${s.slice(0, max)}…` : s;
}

/** 为 HTML 属性做严格转义（旧 app.js 中 escapeHtml 的 TS 版） */
export function escapeHtml(s: string): string {
  return s
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}
