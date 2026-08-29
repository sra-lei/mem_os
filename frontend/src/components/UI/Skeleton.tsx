export function Skeleton({
  width,
  height,
  lines = 1,
  className,
}: {
  width?: string | number;
  height?: string | number;
  lines?: number;
  className?: string;
}) {
  if (lines > 1) {
    return (
      <div className={`skeleton-lines ${className ?? ''}`}>
        {Array.from({ length: lines }, (_, i) => (
          <div
            key={i}
            className="skeleton skeleton--line"
            style={{
              width: width ?? (i === lines - 1 ? '60%' : '100%'),
              height: height ?? '14px',
            }}
          />
        ))}
      </div>
    );
  }
  return (
    <div
      className={`skeleton ${className ?? ''}`}
      style={{ width, height }}
    />
  );
}

export function TableSkeleton({ cols = 5, rows = 6 }: { cols?: number; rows?: number }) {
  return (
    <div className="table-skeleton">
      {Array.from({ length: rows }, (_, r) => (
        <div key={r} className="table-skeleton__row">
          {Array.from({ length: cols }, (_, c) => (
            <div
              key={c}
              className="skeleton skeleton--line"
              style={{
                height: '14px',
                width: c === 0 ? '28%' : c === cols - 1 ? '12%' : `${Math.round(60 / cols)}%`,
              }}
            />
          ))}
        </div>
      ))}
    </div>
  );
}
