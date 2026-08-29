/**
 * 通用比较器：不引入 lodash，避免打包膨胀。
 */

export type SortDir = 'asc' | 'desc';

export interface Sorter<K> {
  key: K;
  dir: SortDir;
}

/** 字符串比较（空值置底） */
export function cmpStr(a: string | null | undefined, b: string | null | undefined): number {
  if (a == null && b == null) return 0;
  if (a == null) return 1;
  if (b == null) return -1;
  return a.localeCompare(b, 'zh-CN');
}

/** 数字比较（空值置底） */
export function cmpNum(a: number | null | undefined, b: number | null | undefined): number {
  if (a == null && b == null) return 0;
  if (a == null) return 1;
  if (b == null) return -1;
  return a - b;
}

/** 日期字符串比较（ISO 即可按字符串比较） */
export function cmpDate(a: string | null | undefined, b: string | null | undefined): number {
  return -cmpStr(a, b); // 默认新的在前
}

/** 按对象 key + dir 排序；accessor 返回可比较的基本类型 */
export function sortBy<T, V extends string | number | null | undefined>(
  items: T[],
  accessor: (x: T) => V,
  dir: SortDir,
): T[] {
  const arr = [...items];
  arr.sort((x, y) => {
    const ax = accessor(x);
    const by = accessor(y);
    const base =
      typeof ax === 'number' && typeof by === 'number'
        ? cmpNum(ax, by)
        : cmpStr(
              typeof ax === 'number' ? String(ax) : ax,
              typeof by === 'number' ? String(by) : by,
            );
    return dir === 'asc' ? base : -base;
  });
  return arr;
}

/** 切分页（客户端分页） */
export function paginateClient<T>(items: T[], page: number, limit: number): T[] {
  const start = Math.max(0, (page - 1) * limit);
  return items.slice(start, start + limit);
}
