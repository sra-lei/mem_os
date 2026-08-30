import { Link } from "react-router-dom";
import { SortHeader, nextDir, type SortableColumn } from "./tableUtils";
import { fmtDate } from "@/utils/format";
import { sortBy } from "@/utils/sort";
import type { CaseDefinition } from "@/types";
import type { SortDir } from "@/utils/sort";
import { Button } from "@/components/UI";

export interface CasesTableProps {
  items: CaseDefinition[];
  sortKey?: string;
  sortDir?: SortDir;
  onSort?: (key: string, dir: SortDir) => void;
}

const COLS: Array<SortableColumn & { key: ValidSortKey }> = [
  { key: "name", label: "用例名称 / 用户输入", sortable: true },
  {
    key: "updated_at",
    label: "更新时间",
    width: "200px",
    align: "center",
    sortable: true,
  },
  { key: "__actions", label: "操作", width: "140px", align: "center" },
];

type ValidSortKey =
  | "name"
  | "updated_at"
  | "__actions";

interface RowShape {
  c: CaseDefinition;
  /** accessor dispatch values (string for sortBy — always string/number) */
  sortVals: Record<ValidSortKey, string | number | null | undefined>;
}

function buildRows(items: CaseDefinition[]): RowShape[] {
  return items.map((c) => ({
    c,
    sortVals: {
      name: c.case_name,
      updated_at: c.updated_at ?? "",
      __actions: "",
    },
  }));
}

export function CasesTable({
  items,
  sortKey,
  sortDir,
  onSort,
}: CasesTableProps) {
  const key = (sortKey ?? "updated_at") as ValidSortKey;
  const rows = sortBy(
    buildRows(items),
    (r) => r.sortVals[key] ?? "",
    sortDir ?? "desc",
  );

  const toggle = (k: string) => {
    if (!onSort) return;
    const vk = k as ValidSortKey;
    if (sortKey !== vk) {
      onSort(vk, "asc");
      return;
    }
    onSort(vk, nextDir(sortDir));
  };

  return (
    <div className="table-wrap">
      <table className="table table--cases">
        <colgroup>
          {COLS.map((c) => (
            <col key={c.key} style={{ width: c.width }} />
          ))}
        </colgroup>
        <thead>
          <tr>
            {COLS.map((c) => (
              <th key={c.key} style={{ textAlign: c.align ?? "center" }}>
                <SortHeader
                  column={c}
                  currentKey={sortKey}
                  currentDir={sortDir}
                  onToggle={toggle}
                />
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.length === 0 ? (
            <tr>
              <td colSpan={COLS.length} className="table__empty">
                暂无匹配的用例
              </td>
            </tr>
          ) : (
            rows.map(({ c }) => (
              <tr key={c.case_id} className="table__row">
                <td>
                  <div className="table__primary">
                    <Link
                      className="table__primary-name"
                      to={`/cases/${c.case_id}`}
                    >
                      <strong>{c.case_name}</strong>
                    </Link>
                    <span className="table__hint">
                      输入：{(c.user_input ?? "—").slice(0, 80)}
                    </span>
                  </div>
                </td>
                <td>
                  <div className="mono" style={{ textAlign: "center", whiteSpace: "nowrap" }}>
                    {c.updated_at ? fmtDate(c.updated_at) : "—"}
                  </div>
                </td>
                <td>
                  <div className="flex gap-6">
                    <Link to={`/cases/${c.case_id}`}>
                      <Button variant="ghost" size="sm">
                        详情
                      </Button>
                    </Link>
                    <Link to={`/cases/${c.case_id}/history`}>
                      <Button variant="secondary" size="sm">
                        历史
                      </Button>
                    </Link>
                  </div>
                </td>
              </tr>
            ))
          )}
        </tbody>
      </table>
    </div>
  );
}
