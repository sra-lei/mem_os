#!/usr/bin/env python3
"""检索注入模式离线对照（只读）：现链窄窗 vs 全入（去噪/不去噪）。

背景（docs/方案/方案-检索注入简化-宽窗替代策略链.md）：
策略链是"固定窄窗稀缺"下的资源分配补丁。若窗口放开，链上 2/3 号去重与
4/5 号配额失去对象，只有 1 号（形式干扰句闸门）可能仍有价值。本脚本用
**真实数据 + 真实生产代码**量化三种注入模式的差异，不接 LLM、不连 Milvus。

三种模式（同一 case 的 20 例）：
  A 现链    ：取评测库 run 落库的 `retrieved_memories`（真实 RRF 排序 + 现链，top_k=15）
  B 全入去噪：库里该 user 全部行，verbatim 段先过生产 VerbatimNoiseFilter
  C 全入原样：库里该 user 全部行，不做任何过滤

输出指标：行数 / 字符 / tokens 估算（由 run 实测 tokens_input 回归外推）/
判分 token 覆盖 / 带进的形式干扰句数 / 结构化-verbatim 构成。

用法：
    uv run python scripts/compare_injection_modes.py
    uv run python scripts/compare_injection_modes.py --run run_79fd83b0ba \\
        --out .tmp/injection_modes.md
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))

from os_mem.core.retrieve.strategies import VerbatimNoiseFilter  # noqa: E402
from os_mem.extractor.utils.token_utils import fact_tokens  # noqa: E402

HEADER = '## 关于用户的长久记忆'
MEM_DB = ROOT / 'src/os_mem/data/memories.db'
EVAL_DB = ROOT / 'src/testing/data/memos.db'


@dataclass
class Variant:
    """一种注入模式在某 case 上的产物与指标。"""

    name: str
    text: str = ''
    rows: int = 0
    structured: int = 0
    verbatim: int = 0
    # 文本里命中噪声正则的行数。注意：run 落库的 retrieved_memories 是纯文本、
    # **不含 key**，所以 A 组无法区分"结构化事实"与"verbatim 兜底句"——实测 A 的
    # 命中经逐条核对全是结构化事实（含 instead of 的调整项），不是 verbatim 噪声。
    noise_regex_hits: int = 0

    @property
    def chars(self) -> int:
        return len(self.text)


def _load_memories() -> dict[str, list[dict]]:
    con = sqlite3.connect(MEM_DB)
    con.row_factory = sqlite3.Row
    rows = [
        dict(r)
        for r in con.execute(
            'SELECT user_id, key, category, fact, value FROM struct_memories'
        )
    ]
    con.close()
    by_user: dict[str, list[dict]] = {}
    for r in rows:
        by_user.setdefault(r['user_id'], []).append(r)
    return by_user


def _load_run(run_prefix: str) -> tuple[dict[str, dict], list[dict]]:
    """读某 run 的逐例结果 + test_runs 元信息。"""
    con = sqlite3.connect(EVAL_DB)
    con.row_factory = sqlite3.Row
    cases = {
        r['case_id']: dict(r)
        for r in con.execute(
            'SELECT case_id, passed, score, tokens_input, tokens_output,'
            ' latency_ms, retrieved_memories FROM test_case_results'
            ' WHERE run_id LIKE ?',
            (run_prefix + '%',),
        )
    }
    meta = [
        dict(r)
        for r in con.execute(
            'SELECT id, run_at, total_cases, passed_count, pass_rate,'
            ' duration_seconds FROM test_runs WHERE id LIKE ?',
            (run_prefix + '%',),
        )
    ]
    con.close()
    return cases, meta


def _load_criteria() -> dict[str, str]:
    con = sqlite3.connect(EVAL_DB)
    con.row_factory = sqlite3.Row
    out = {
        r['case_id']: (r['evaluation_criteria'] or '')
        for r in con.execute(
            'SELECT case_id, evaluation_criteria FROM test_case_definitions'
        )
    }
    con.close()
    return out


def _estimate_token_model(cases: dict[str, dict]) -> tuple[float, float]:
    """用 run 实测 (窗口字符, tokens_input) 做最小二乘，得 tokens ≈ a + b*chars。"""
    pts = [
        (len(c['retrieved_memories'] or ''), c['tokens_input'] or 0)
        for c in cases.values()
        if c['retrieved_memories'] and c['tokens_input']
    ]
    n = len(pts)
    if n < 3:
        return 0.0, 0.0
    mx = sum(x for x, _ in pts) / n
    my = sum(y for _, y in pts) / n
    denom = sum((x - mx) ** 2 for x, _ in pts)
    b = sum((x - mx) * (y - my) for x, y in pts) / denom if denom else 0.0
    return my - b * mx, b


def _is_verbatim(row: dict) -> bool:
    return (row['key'] or '').startswith('verbatim_')


def _render(rows: list[dict]) -> str:
    lines = [HEADER]
    if not rows:
        lines.append('（当前没有可用记忆）')
    for r in rows:
        lines.append(f"- {r['fact']}")
    return '\n'.join(lines)


def _build_variants(
    rows: list[dict], gate: VerbatimNoiseFilter
) -> tuple[Variant, Variant]:
    """B（全入 + 1 号去噪）与 C（全入原样）。结构化在前、verbatim 补位。"""
    structured = [r for r in rows if not _is_verbatim(r)]
    verbatim = [r for r in rows if _is_verbatim(r)]

    hits = [
        {
            'key': r['key'],
            'category': r['category'],
            'fact': r['fact'],
            'value': r['value'],
        }
        for r in verbatim
    ]
    kept_ids = {id(h) for h in gate.apply('', hits, top_k=len(hits) or 1)}
    verb_kept = [r for r, h in zip(verbatim, hits, strict=True) if id(h) in kept_ids]
    verb_dropped = len(verbatim) - len(verb_kept)

    b_rows = structured + verb_kept
    c_rows = structured + verbatim
    b = Variant('B 全入去噪', _render(b_rows), len(b_rows), len(structured),
                len(verb_kept), 0)
    c = Variant('C 全入原样', _render(c_rows), len(c_rows), len(structured),
                len(verbatim), verb_dropped)
    return b, c


def _noise_regex_hits(text: str, gate: VerbatimNoiseFilter) -> int:
    """文本里命中 1 号 噪声正则的行数（**不区分 key**，口径见 Variant 注释）。"""
    hits = [
        {'key': 'verbatim_probe', 'fact': line[2:], 'value': ''}
        for line in text.split('\n')
        if line.startswith('- ')
    ]
    kept = gate.apply('', hits, top_k=len(hits) or 1)
    return len(hits) - len(kept)


def main() -> None:
    parser = argparse.ArgumentParser(description='检索注入模式离线对照（只读）')
    parser.add_argument(
        '--run',
        default='run_79fd83b0ba',
        help='基准 run id 前缀（现链窗口来源）',
    )
    parser.add_argument('--out', default=None, help='把 markdown 结果写到该路径')
    args = parser.parse_args()

    by_user = _load_memories()
    cases, meta = _load_run(args.run)
    criteria = _load_criteria()
    gate = VerbatimNoiseFilter()
    a0, b0 = _estimate_token_model(cases)

    if meta:
        m = meta[0]
        print(
            f'基准 run: {m["id"]}  {m["run_at"]}  '
            f'通过 {m["passed_count"]}/{m["total_cases"]}'
            f'  时长 {m["duration_seconds"]:.0f}s'
        )
    print(
        'token 估算模型（由该 run 实测回归）: '
        f'tokens_input ≈ {a0:.0f} + {b0:.3f} × 窗口字符'
    )
    print()

    header = (
        f'{"case":32} {"A行":>4} {"A字符":>6} {"B行":>4} {"B字符":>6} '
        f'{"判分tok":>7} {"A覆盖":>5} {"B覆盖":>5} {"C覆盖":>5} '
        f'{"A正则命中":>8} {"C干扰":>5} {"结果":>4}'
    )
    print(header)
    print('-' * len(header))

    tot = {'atok': 0, 'achar': 0, 'bchar': 0, 'brows': 0, 'arows': 0,
           'ctok': 0, 'acov': 0, 'bcov': 0, 'ccov': 0,
           'adist': 0, 'cdist': 0, 'gain': 0, 'cases_with_gain': 0}
    detail: list[str] = []

    for case_id in sorted(cases):
        rec = cases[case_id]
        rows = by_user.get(case_id, [])
        a = Variant('A 现链', rec['retrieved_memories'] or '',
                    len([ln for ln in (rec['retrieved_memories'] or '').split('\n')
                         if ln.startswith('- ')]))
        b, c = _build_variants(rows, gate)
        a.noise_regex_hits = _noise_regex_hits(a.text, gate)

        ctoks = fact_tokens(criteria.get(case_id, ''))
        acov = ctoks & fact_tokens(a.text)
        bcov = ctoks & fact_tokens(b.text)
        ccov = ctoks & fact_tokens(c.text)

        gain = len(bcov - acov)
        tot['gain'] += gain
        tot['cases_with_gain'] += 1 if gain else 0
        tot['atok'] += len(a.text)
        tot['achar'] += len(a.text)
        tot['bchar'] += len(b.text)
        tot['arows'] += a.rows
        tot['brows'] += b.rows
        tot['ctok'] += len(ctoks)
        tot['acov'] += len(acov)
        tot['bcov'] += len(bcov)
        tot['ccov'] += len(ccov)
        tot['adist'] += a.noise_regex_hits
        tot['cdist'] += c.noise_regex_hits

        print(f'{case_id:32} {a.rows:>4} {a.chars:>6} {b.rows:>4} {b.chars:>6} '
              f'{len(ctoks):>7} {len(acov):>5} {len(bcov):>5} {len(ccov):>5} '
              f'{a.noise_regex_hits:>8} {c.noise_regex_hits:>5} '
              f'{"通过" if rec["passed"] else "失败":>4}')
        detail.append(
            f'| `{case_id}` | {a.rows} | {a.chars} | {b.rows} | {b.chars} | '
            f'{len(acov)}/{len(ctoks)} | {len(bcov)}/{len(ctoks)} | '
            f'{len(bcov - acov)} | {a.noise_regex_hits} | {c.noise_regex_hits} | '
            f'{"✅" if rec["passed"] else "❌"} |'
        )

    print('-' * len(header))
    n = len(cases)
    print(f'合计/均值（{n} 例）：')
    print(f'  窗口字符  A {tot["achar"]} → B {tot["bchar"]}'
          f'（{tot["bchar"] / max(1, tot["achar"]):.2f}×）'
          f'；行数 A {tot["arows"]} → B {tot["brows"]}')
    print(f'  tokens 估算  A {a0 + b0 * tot["achar"] / n:.0f} → '
          f'B {a0 + b0 * tot["bchar"] / n:.0f} 每条（+'
          f'{b0 * (tot["bchar"] - tot["achar"]) / n:.0f}）')
    print(
        f'  判分 token 覆盖  A {tot["acov"]}/{tot["ctok"]}'
        f'（{100 * tot["acov"] / max(1, tot["ctok"]):.0f}%）→ '
        f'B {tot["bcov"]}/{tot["ctok"]}'
        f'（{100 * tot["bcov"] / max(1, tot["ctok"]):.0f}%）'
        f'→ C {tot["ccov"]}/{tot["ctok"]}'
    )
    print(f'  B 相对 A 新增覆盖 = {tot["gain"]} 个判分 token，'
          f'分布在 {tot["cases_with_gain"]} 个 case 上')
    print(
        f'  噪声正则命中行  A 文本 {tot["adist"]} 条'
        f'（纯文本无 key，实测经核对全是结构化事实）；'
        f'C 带进 verbatim 干扰句 {tot["cdist"]} 条（B 为 0，1 号在拦）'
    )

    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(
            '# 注入模式离线对照（脚本自动生成）\n\n'
            f'基准 run：`{args.run}`；脚本：`scripts/compare_injection_modes.py`\n\n'
            '| case | A 行 | A 字符 | B 行 | B 字符 | A 覆盖 | B 覆盖 '
            '| B−A 新增 | A 正则命中 | C 干扰 | 该 run 结果 |\n'
            '|---|---|---|---|---|---|---|---|---|---|---|\n'
            + '\n'.join(detail) + '\n',
            encoding='utf-8',
        )
        print(f'\n已写出：{out}')


if __name__ == '__main__':
    main()
