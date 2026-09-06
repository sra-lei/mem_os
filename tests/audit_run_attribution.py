"""评测 run 三层归因审计（离线只读：仅查 memos.db / memories.db / YAML）。

背景：struct 评测（layer1 数字密集长对话）失败需要区分发生在哪一层——09-05
调试记录的三层归因法，本工具把它固化，并复用 ``test_case_results.retrieved_memories``
（每次 run 都落库了注入原文），因此**无需重跑即可审计任意历史 run**：
本工具只读不写，不调 LLM / Milvus。

  1. 提取漏    ：期望信息点在记忆库（memories.db struct_memories）中不存在；
  2. 检索覆盖  ：库里有，但没进本次注入窗口（top_k 截断 / 排序被挤占）；
  3. 回答/污染 ：进窗了但答案没给出（错版本 / 中间值 / 旧值误导，或纯回答层问题）。

判分口径对齐：期望"信息点"抽取规则与 assert_judger 保持一致（见下方同步注释），
本工具只回答"值在不在各层"，不替代 moonshot/assert 判分——因此 moonshot 判失败
但 token 全命中时归为"回答层/判分口径"，属正常边界。

实现说明：刻意**不 import os_mem / eval** —— 两者会拖入 loguru（Windows 命名
管道）与 Milvus/LLM 等重型导入，本工具只需 stdlib + PyYAML，任何环境可跑。

用法（无需在线服务；DB 均以只读打开）：
    uv run python tests/audit_run_attribution.py --run run_7979
    uv run python tests/audit_run_attribution.py --run run_7979 \\
        --case layer1_11_mortgage_application
    uv run python tests/audit_run_attribution.py --run run_7979 --all  # 含通过 case
    uv run python tests/audit_run_attribution.py --run run_7979 --window  # 窗口全文
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
_MEMOS_DB = ROOT / 'src' / 'testing' / 'data' / 'memos.db'
_MEMORIES_DB = ROOT / 'src' / 'os_mem' / 'data' / 'memories.db'
_TEST_CASES_DIR = ROOT / 'tests' / 'test_cases'

# ---------------------------------------------------------------------- #
#  期望信息点抽取（与 tests/eval/judge/impl/assert_judger.py 保持同步——
#  判分 token 规则变更时，此处必须一并修改，否则审计口径与判分脱节）
# ---------------------------------------------------------------------- #
_MONTH_ALIASES = {
    'january': 1, 'jan': 1,
    'february': 2, 'feb': 2,
    'march': 3, 'mar': 3,
    'april': 4, 'apr': 4,
    'may': 5,
    'june': 6, 'jun': 6,
    'july': 7, 'jul': 7,
    'august': 8, 'aug': 8,
    'september': 9, 'sept': 9, 'sep': 9,
    'october': 10, 'oct': 10,
    'november': 11, 'nov': 11,
    'december': 12, 'dec': 12,
}

# 归一化：去掉金额符号/千分位/连字符/空白等纯格式噪音，保留数字/字母/小数点与中文
_NORM_RE = re.compile(r"[\s,$%_'\"-]+")

# 整块英文日期（December 23, 2024 / Nov 21st 2025）——整体剔除，不把年份当普通数字
_DATE_BLOCK_RE = re.compile(
    r'\b(?:'
    + '|'.join(sorted((re.escape(m) for m in _MONTH_ALIASES), key=len, reverse=True))
    + r')\.?\s+\d{1,2}(?:st|nd|rd|th)?(?:,?\s*\d{4})?\b',
    re.IGNORECASE,
)

# 金额：$2,400 / $1,017.50 / $150
_AMOUNT_RE = re.compile(r'\$\s?\d{1,3}(?:,\d{3})+(?:\.\d+)?|\$\s?\d+(?:\.\d+)?')

# 字母开头编号/代码：PAC-778K4M / MAT-151 / CLM-2024-894327 / E7739482M
_CODE_RE = re.compile(r'\b[A-Z]{1,8}-?\d{2,}[A-Za-z0-9-]*\b', re.IGNORECASE)
# 长数字序列（信用卡等多组连写）：5524-8897-2234-4001
_CARD_RE = re.compile(r'\b\d{4,}(?:[-\s]\d{4,}){1,}\b')
# 座位/短编号：12C、14C
_SEAT_RE = re.compile(r'\b\d{1,3}[A-Z]\b', re.IGNORECASE)
# 剩余 ≥4 位纯数字：账号/路由/确认号
_NUM4_RE = re.compile(r'(?<!\d)\d{4,}(?!\d)')


def _norm(text: str) -> str:
    """等价归一：去格式噪音 + 忽略大小写（保留数字/字母/小数点/中文）。"""
    return _NORM_RE.sub('', text).lower()


def expected_facts(expected: str) -> list[str]:
    """从期望判分文本抽取需核验的信息点（去重保序，归一化形式）。

    与 assert_judger.AssertJudger._expected_facts 同语义（日期整块先行剔除，
    金额/代码/卡片/座位/长数字依次抽取并移除，避免嵌套重复计数）。
    """
    text = _DATE_BLOCK_RE.sub(' ', expected)
    facts: list[str] = []
    for m in _AMOUNT_RE.finditer(text):
        facts.append(_norm(m.group(0)))
    text = _AMOUNT_RE.sub(' ', text)
    for m in _CODE_RE.finditer(text):
        facts.append(_norm(m.group(0)))
    text = _CODE_RE.sub(' ', text)
    for m in _CARD_RE.finditer(text):
        facts.append(_norm(m.group(0)))
    text = _CARD_RE.sub(' ', text)
    for m in _SEAT_RE.finditer(text):
        facts.append(_norm(m.group(0)))
    text = _SEAT_RE.sub(' ', text)
    for n in _NUM4_RE.findall(text):
        facts.append(_norm(n))
    return list(dict.fromkeys(facts))


# ---------------------------------------------------------------------- #
#  数据读取（只读）
# ---------------------------------------------------------------------- #
def _ro_connect(db_path: Path) -> sqlite3.Connection:
    return sqlite3.connect(f'file:{db_path}?mode=ro', uri=True)


def load_run_meta(run_id: str) -> dict[str, str]:
    """test_runs 的 run 元信息（时间 / phase / config_snapshot）。"""
    with _ro_connect(_MEMOS_DB) as con:
        row = con.execute(
            'SELECT run_at, phase, config_snapshot FROM test_runs WHERE id LIKE ?',
            (run_id + '%',),
        ).fetchone()
    if not row:
        return {}
    cfg = json.loads(row[2] or '{}')
    return {
        'run_at': str(row[0]),
        'phase': str(row[1] or ''),
        'config': ', '.join(f'{k}={v}' for k, v in cfg.items()),
    }


def load_case_results(run_id: str) -> list[dict]:
    """run 内全部 case 结果（含注入原文与答案）。"""
    with _ro_connect(_MEMOS_DB) as con:
        rows = con.execute(
            'SELECT case_id, passed, score, retrieved_memories, actual_answer, '
            'error_message FROM test_case_results WHERE run_id LIKE ?',
            (run_id + '%',),
        ).fetchall()
    return [
        {
            'case_id': r[0],
            'passed': bool(r[1]),
            'score': r[2],
            'window_text': r[3],
            'answer': r[4],
            'error': r[5],
        }
        for r in rows
    ]


def load_yaml_expected() -> dict[str, str]:
    """test_id → 期望判分文本（evaluation_criteria 优先，behavior 兜底）。"""
    out: dict[str, str] = {}
    for p in sorted(_TEST_CASES_DIR.rglob('*.yaml')):
        data = yaml.safe_load(p.read_text(encoding='utf-8'))
        tid = data.get('test_id')
        if not tid:
            continue
        out[tid] = (
            data.get('evaluation_criteria') or data.get('expected_behavior') or ''
        )
    return out


def load_store_facts(user_id: str) -> list[dict]:
    """user 在记忆库中的全部事实（authoritative SQLite）。"""
    with _ro_connect(_MEMORIES_DB) as con:
        rows = con.execute(
            'SELECT fact, category, key, value FROM struct_memories WHERE user_id = ?',
            (user_id,),
        ).fetchall()
    return [
        {'fact': r[0], 'category': r[1], 'key': r[2], 'value': r[3]} for r in rows
    ]


# ---------------------------------------------------------------------- #
#  解析与判定
# ---------------------------------------------------------------------- #
def parse_window(text: str | None) -> list[str]:
    """从 retrieved_memories 原文解析注入的事实列表（"- xxx" 行）。"""
    if not text:
        return []
    raw = text.strip()
    if not raw:
        return []
    if raw[0] in '[{':
        try:
            obj = json.loads(raw)
            if isinstance(obj, dict):
                obj = obj.get('memories') or obj.get('text') or raw
            if isinstance(obj, list):
                raw = '\n'.join(str(x) for x in obj)
        except Exception:
            pass
    bullets: list[str] = []
    for line in raw.splitlines():
        s = line.strip()
        if s.startswith('- '):
            bullets.append(s[2:].strip())
    return bullets


def _norm_corpus(texts: list[str]) -> str:
    """逐条归一后以 '|' 连接，避免跨条拼接伪造 token（如 500|000 → 500000）。"""
    return '|'.join(_norm(t) for t in texts if t)


def audit_case(
    case: dict,
    store_facts: list[dict],
    expected: str,
) -> dict:
    """对单个 case 做三层归因，返回逐 token 判定与窗口构成。"""
    tokens = expected_facts(expected)
    bullets = parse_window(case['window_text'])

    store_norm = _norm_corpus([f'{r["fact"]} {r["value"]}' for r in store_facts])
    win_norm = _norm_corpus(bullets)
    ans_norm = _norm(case['answer'] or '')

    extract_missing: list[str] = []
    retrieval_missing: list[str] = []
    answer_missing: list[str] = []
    for tok in tokens:
        in_store = tok in store_norm
        in_window = tok in win_norm
        if not in_store:
            extract_missing.append(tok)
        elif not in_window:
            retrieval_missing.append(tok)
        elif tok not in ans_norm:
            answer_missing.append(tok)

    # 窗口行打标：verbatim / structured / 未匹配（与库内 fact 原文比对）
    key_by_norm: dict[str, str] = {}
    for r in store_facts:
        key_by_norm[_norm(r['fact'])] = r['key']
    n_verbatim = n_structured = n_unmatched = 0
    tagged: list[tuple[str, str]] = []
    for b in bullets:
        key = key_by_norm.get(_norm(b))
        if key is None:
            tag = 'unmatched'
            n_unmatched += 1
        elif key.startswith('verbatim_'):
            tag = 'verbatim'
            n_verbatim += 1
        else:
            tag = 'structured'
            n_structured += 1
        tagged.append((b, tag))

    n_verbatim_store = sum(1 for r in store_facts if r['key'].startswith('verbatim_'))
    counts = {
        'extract': len(extract_missing),
        'retrieval': len(retrieval_missing),
        'answer': len(answer_missing),
    }
    layer = max(counts, key=counts.get) if any(counts.values()) else 'no-missing'
    return {
        'case_id': case['case_id'],
        'score': case['score'],
        'passed': case['passed'],
        'error': case.get('error'),
        'tokens': tokens,
        'extract_missing': extract_missing,
        'retrieval_missing': retrieval_missing,
        'answer_missing': answer_missing,
        'layer': layer,
        'n_store': len(store_facts),
        'n_verbatim_store': n_verbatim_store,
        'window': tagged,
        'window_counts': {
            'verbatim': n_verbatim,
            'structured': n_structured,
            'unmatched': n_unmatched,
        },
        'answer': case['answer'],
    }


# ---------------------------------------------------------------------- #
#  输出
# ---------------------------------------------------------------------- #
def _line_list(items: list[str]) -> str:
    return '\n'.join(f'    {t}' for t in items)


def print_case(report: dict, show_window: bool = False) -> None:
    """单个 case 的人类可读账本。"""
    cid = report['case_id']
    print(f'== {cid} | score={report["score"]} | layer={report["layer"]}')
    print(
        f'   期望信息点 {len(report["tokens"])} 个'
        f' | 提取缺 {len(report["extract_missing"])}'
        f' | 覆盖缺 {len(report["retrieval_missing"])}'
        f' | 回答缺 {len(report["answer_missing"])}'
    )
    print(
        f'   库内 {report["n_store"]} 条'
        f'（verbatim {report["n_verbatim_store"]}）'
        f' | 窗口 {len(report["window"])} 条'
        f'（verbatim {report["window_counts"]["verbatim"]}'
        f' / structured {report["window_counts"]["structured"]}'
        f' / unmatched {report["window_counts"]["unmatched"]}）'
    )
    if report['extract_missing']:
        print('   [提取漏] 库内没有:')
        print(_line_list(report['extract_missing']))
    if report['retrieval_missing']:
        print('   [覆盖漏] 库里有、窗口没有:')
        print(_line_list(report['retrieval_missing']))
    if report['answer_missing']:
        print('   [回答漏] 窗口里有、答案没给出:')
        print(_line_list(report['answer_missing']))
        for tok in report['answer_missing']:
            ctx = [b for b, _ in report['window'] if tok in _norm(b)]
            for line in ctx[:3]:
                print(f'       └ 窗内含该 token: {line[:110]}')
    if show_window:
        print('   [注入窗口]')
        for b, tag in report['window']:
            print(f'       ({tag}) {b[:110]}')
    print()


def main() -> None:
    parser = argparse.ArgumentParser(description='评测 run 三层归因审计（离线只读）')
    parser.add_argument('--run', required=True, help='run id 前缀，如 run_7979')
    parser.add_argument('--case', default=None, help='只审计包含该串的 case')
    parser.add_argument(
        '--all', action='store_true', help='含通过 case（默认只看失败）'
    )
    parser.add_argument('--window', action='store_true', help='打印注入窗口全文')
    args = parser.parse_args()

    meta = load_run_meta(args.run)
    print(f'run: {args.run} | {meta.get("run_at", "")} | {meta.get("config", "")}\n')

    expected_map = load_yaml_expected()
    results = load_case_results(args.run)
    if not results:
        print(f'未在 memos.db 找到 {args.run} 的 case 结果（需 --record-db 落库）')
        return
    if args.case:
        results = [c for c in results if args.case in c['case_id']]
    elif not args.all:
        results = [c for c in results if not c['passed']]

    reports: list[dict] = []
    no_expected: list[str] = []
    for case in sorted(results, key=lambda c: c['case_id']):
        expected = expected_map.get(case['case_id'], '')
        if not expected:
            no_expected.append(case['case_id'])
            continue
        store_facts = load_store_facts(case['case_id'])
        reports.append(audit_case(case, store_facts, expected))

    for r in reports:
        print_case(r, show_window=args.window)
    if no_expected:
        print(f'跳过（无期望判分文本）: {", ".join(no_expected)}\n')

    if not reports:
        print('无可审计 case')
        return
    failed = [r for r in reports if not r['passed']]
    if failed:
        missing_of = {
            'extract': lambda r: r['extract_missing'],
            'retrieval': lambda r: r['retrieval_missing'],
            'answer': lambda r: r['answer_missing'],
        }
        agg = {k: sum(len(fn(r)) for r in failed) for k, fn in missing_of.items()}
        by_layer: dict[str, list[str]] = {}
        for r in failed:
            by_layer.setdefault(r['layer'], []).append(r['case_id'])
        print('--- run 汇总（仅失败 case）---')
        print(f'失败 {len(failed)} 个 | 期望点逐层缺失合计:')
        print(
            f'   [提取漏] {agg["extract"]}'
            f' | [覆盖漏] {agg["retrieval"]}'
            f' | [回答漏] {agg["answer"]}'
        )
        for layer, cases in by_layer.items():
            print(f'   主导层 {layer}: {", ".join(cases)}')


if __name__ == '__main__':
    main()
