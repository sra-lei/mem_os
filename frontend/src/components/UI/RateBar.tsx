/** 进度条：展示通过率（0-1）或任务进度。 */
export function RateBar({
  value,
  max = 1,
  tone = 'success',
  showLabel = true,
  format,
}: {
  value: number | null | undefined;
  max?: number;
  tone?: 'success' | 'danger' | 'warning' | 'info';
  showLabel?: boolean;
  format?: (v: number) => string;
}) {
  const normalized =
    value == null || Number.isNaN(value) ? 0 : max ? Math.min(1, Math.max(0, value / max)) : 0;
  const pct = `${(normalized * 100).toFixed(1)}%`;
  const label = format ? format(value ?? 0) : pct;
  return (
    <div className={`rate-bar rate-bar--${tone}`}>
      <div className="rate-bar__track">
        <div className={`rate-bar__fill rate-bar__fill--${tone}`} style={{ width: pct }} />
      </div>
      {showLabel ? <span className="rate-bar__label">{label}</span> : null}
    </div>
  );
}
