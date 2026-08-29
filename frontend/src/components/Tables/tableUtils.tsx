/**
 * 排序辅助：统一的表头排序按钮 + 双向切换。
 */
import type { SortDir } from '@/utils/sort';

export interface SortableColumn<K extends string = string> {
  key: K;
  label: React.ReactNode;
  width?: string;
  sortable?: boolean;
  align?: 'left' | 'right' | 'center';
}

export function SortHeader({
  column,
  currentKey,
  currentDir,
  onToggle,
}: {
  column: SortableColumn;
  currentKey?: string;
  currentDir?: SortDir;
  onToggle: (key: string) => void;
}) {
  const justify =
    column.align === 'right' ? 'flex-end' : column.align === 'center' ? 'center' : 'flex-start';
  if (!column.sortable) {
    return (
      <span
        className="table__th-content"
        style={{ textAlign: column.align ?? 'left', justifyContent: justify }}
      >
        {column.label}
      </span>
    );
  }
  const active = currentKey === column.key;
  const arrowChar = active ? (currentDir === 'asc' ? '↑' : '↓') : '↕';
  // For right-aligned columns the arrow sits on the LEFT of the label so the
  // label's right edge lines up with the cell content (numbers) below.
  const arrow = <span className="table__sort-arrow">{arrowChar}</span>;
  const label = <span>{column.label}</span>;
  return (
    <button
      type="button"
      className={`table__sort-btn ${active ? 'is-active' : ''}`}
      style={{ width: '100%', justifyContent: justify, textAlign: column.align ?? 'left' }}
      onClick={() => onToggle(column.key)}
    >
      {column.align === 'right' ? (<>{arrow}{label}</>) : (<>{label}{arrow}</>)}
    </button>
  );
}

export function nextDir(currentDir?: SortDir): SortDir {
  if (currentDir === 'desc') return 'asc';
  return 'desc';
}
