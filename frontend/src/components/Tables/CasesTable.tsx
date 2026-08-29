import { Link } from "react-router-dom";
import { PassBadge } from "../UI/Badge";
import { SortHeader, nextDir, type SortableColumn } from "./tableUtils";
import { fmtDateShort, fmtNum, fmtRate } from "@/utils/format";
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
    key: "total_runs",
    label: "运行次数",
    width: "100px",
    align: "center",
    sortable: true,
  },
  {
    key: "run_status",
    label: "状态",
    width: "100px",
    align: "center",
    sortable: true,
  },
  {
    key: "pass_rate",
    label: "通过率",
    width: "110px",
    align: "center",
    sortable: true,
  },
  {
    key: "updated_at",
    label: "更新时间",
    width: "120px",
    align: "center",
    sortable: true,
  },
  { key: "__actions", label: "操作", width: "140px", align: "center" },
];

type ValidSortKey =
  | "name"
  | "total_runs"
  | "run_status"
  | "pass_rate"
  | "updated_at"
  | "__actions";

interface RowShape {
  c: CaseDefinition;
  rate: number;
  tone: "muted" | "ok" | "warn" | "bad";
  total: number;
  /** accessor dispatch values (string for sortBy — always string/number) */
  sortVals: Record<ValidSortKey, string | number | null | undefined>;
}

function buildRows(items: CaseDefinition[]): RowShape[] {
  return items.map((c) => {
    const total = Number(c.total_runs ?? 0);
    const pass = Number(c.pass_count ?? 0);
    const rate = total > 0 ? pass / total : 0;
    const tone: RowShape["tone"] =
      total === 0 ? "muted" : rate >= 0.7 ? "ok" : rate >= 0.4 ? "warn" : "bad";
    return {
      c,
      rate,
      tone,
      total,
      sortVals: {
        name: c.case_name,
        total_runs: total,
        run_status: rate,
        pass_rate: rate,
        updated_at: c.updated_at ?? "",
        __actions: "",
      },
    };
  });
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
            rows.map(({ c, rate, tone, total }) => (
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
                  <div className="num" style={{ textAlign: "center" }}>
                    {fmtNum(total)}
                  </div>
                </td>
                <td>
                  <div className="flex items-center gap-2">
                    <PassBadge passed={!!(rate >= 0.7 && total > 0)} />
                  </div>
                </td>
                <td>
                  <div className="flex items-center gap-6">
                    <span className={`num num--${tone}`}>{fmtRate(rate)}</span>
                    <span className="muted">
                      ({fmtNum(c.pass_count)}/{fmtNum(total)})
                    </span>
                  </div>
                </td>
                <td>
                  <div className="mono" style={{ textAlign: "center" }}>
                    {c.updated_at ? fmtDateShort(c.updated_at) : "—"}
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
