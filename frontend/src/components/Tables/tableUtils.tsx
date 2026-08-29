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
  if (!column.sortable) {
    return (
      <span className="table__th-content" style={{ textAlign: column.align ?? 'left' }}>
        {column.label}
      </span>
    );
  }
  const active = currentKey === column.key;
  const arrow = active ? (currentDir === 'asc' ? '↑' : '↓') : '↕';
  return (
    <button
      type="button"
      className={`table__sort-btn ${active ? 'is-active' : ''}`}
      style={{ textAlign: column.align ?? 'left', width: '100%', justifyContent: column.align ?? 'left' }}
      onClick={() => onToggle(column.key)}
    >
      <span>{column.label}</span>
      <span className="table__sort-arrow">{arrow}</span>
    </button>
  );
}

export function nextDir(currentDir?: SortDir): SortDir {
  if (currentDir === 'desc') return 'asc';
  return 'desc';
}
