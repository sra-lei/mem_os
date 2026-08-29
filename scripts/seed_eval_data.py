"""Seed demo data into memos.db for the EvalView dashboard.

Usage:
    python -m scripts.seed_eval_data
"""
from __future__ import annotations

import json
import random
import uuid
from datetime import datetime, timedelta
from pathlib import Path

# Ensure project root on path
import sys
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.db import init_db, get_session  # noqa: E402
from src.db.models import (  # noqa: E402
    TestRun,
    TestCaseResult,
    TestCaseDefinition,
)


random.seed(42)

VERSIONS = ["v0.1", "v0.2", "v0.3", "v0.4"]
PHASES = ["base", "multi_session", "proactive"]
PHASE_LABEL = {
    "base": "基础回忆",
    "multi_session": "多会话检索",
    "proactive": "主动服务",
}

# ---------- Case definitions ----------
def build_case_defs():
    defs = []
    case_id_seq = {"base": 1, "multi_session": 1, "proactive": 1}
    prefix_map = {"base": "R", "multi_session": "M", "proactive": "A"}

    cases_meta = {
        "base": [
            ("银行账号信息回忆", ["银行", "账户", "账号", "个人信息"]),
            ("保险理赔细节回忆", ["保险", "理赔", "保单"]),
            ("医疗预约信息回忆", ["医疗", "预约", "医院"]),
            ("航班预订详情回忆", ["旅行", "机票", "航班"]),
            ("宽带安装信息回忆", ["宽带", "互联网", "安装"]),
            ("信用卡申请回忆", ["信用卡", "申请", "信用"]),
            ("租车合同细节回忆", ["租车", "车辆", "合同"]),
            ("酒店入住信息回忆", ["酒店", "住宿", "预订"]),
            ("家庭安防配置回忆", ["安防", "家庭", "密码"]),
            ("药店处方转移回忆", ["处方", "药品", "药店"]),
            ("房贷申请信息回忆", ["房贷", "贷款", "房产"]),
            ("健身会员信息回忆", ["健身", "会员", "运动"]),
            ("报税资料回忆", ["税务", "报税", "财务"]),
            ("手机升级方案回忆", ["手机", "通信", "套餐"]),
            ("大学入学信息回忆", ["入学", "教育", "学校"]),
            ("房屋装修方案回忆", ["装修", "房屋", "预算"]),
            ("宠物就医信息回忆", ["宠物", "兽医", "医疗"]),
            ("退休规划细节回忆", ["退休", "养老", "理财"]),
            ("婚礼场地预订回忆", ["婚礼", "场地", "预订"]),
            ("日托入学信息回忆", ["日托", "儿童", "学校"]),
        ],
        "multi_session": [
            ("多车辆保险管理", ["车辆", "保险", "多主体"]),
            ("多房产管理回忆", ["房产", "租赁", "多套"]),
            ("多信用卡账单协调", ["信用卡", "账单", "多卡"]),
            ("多订阅服务管理", ["订阅", "SaaS", "账单"]),
            ("多银行账户协调", ["银行", "账户", "转账"]),
            ("多保险保单协调", ["保险", "保单", "多份"]),
            ("多药品处方协调", ["药品", "处方", "医疗"]),
            ("多出租物业协调", ["出租", "物业", "租赁"]),
            ("多子女学校协调", ["教育", "学校", "家庭"]),
            ("旅行改签链路回忆", ["旅行", "改签", "航班"]),
            ("医疗方案演进回忆", ["医疗", "治疗", "历史"]),
            ("财务指令冲突处理", ["财务", "矛盾", "指令"]),
            ("家庭服务级联回忆", ["家庭", "服务", "安装"]),
            ("订单修改链路回忆", ["订单", "修改", "购物"]),
            ("薪资谈判链路回忆", ["薪资", "谈判", "工作"]),
            ("家庭事件冲突处理", ["家庭", "事件", "冲突"]),
            ("技术支持链路回忆", ["技术", "支持", "IT"]),
            ("教育先修链路回忆", ["教育", "先修", "课程"]),
            ("投资市场响应链路", ["投资", "市场", "股票"]),
            ("医保变更链路回忆", ["医保", "保险", "变更"]),
        ],
        "proactive": [
            ("多行程主动协调", ["旅行", "协调", "主动"]),
            ("医保联动主动提醒", ["医疗", "保险", "主动"]),
            ("购房流程主动协调", ["房产", "购房", "流程"]),
            ("保修到期主动提醒", ["保修", "期限", "提醒"]),
            ("税务资料主动整合", ["税务", "报税", "整合"]),
            ("业务扩张主动协调", ["业务", "扩张", "计划"]),
            ("养老照护主动协调", ["养老", "照护", "家庭"]),
            ("离婚财产复杂处理", ["离婚", "财产", "法律"]),
            ("车祸事件级联处理", ["车祸", "保险", "维修"]),
            ("教育融资复杂路径", ["教育", "融资", "贷款"]),
            ("移民状态主动跟踪", ["移民", "签证", "状态"]),
            ("房产投资纠葛处理", ["房产", "投资", "纠葛"]),
            ("急诊级联处理", ["急诊", "医疗", "主动"]),
            ("隐性医保网络识别", ["医保", "网络", "识别"]),
            ("身份被盗主动识别", ["安全", "身份", "盗窃"]),
            ("加密遗产难题处理", ["加密货币", "遗产", "继承"]),
            ("环境污染级联处理", ["环境", "污染", "索赔"]),
            ("基因检测启示主动处理", ["基因", "检测", "医疗"]),
            ("雇佣欺诈网络识别", ["雇佣", "欺诈", "合规"]),
            ("医疗事故模式识别", ["医疗", "事故", "识别"]),
        ],
    }

    for cat, items in cases_meta.items():
        for idx, (name, tags) in enumerate(items, start=1):
            prefix = prefix_map[cat]
            cid = f"{prefix}-{idx:03d}"
            version_idx = {"base": 0, "multi_session": 1, "proactive": 2}[cat]
            defs.append(TestCaseDefinition(
                case_id=cid,
                name=name,
                category=cat,
                version_target=VERSIONS[min(version_idx, len(VERSIONS) - 1)],
                description=f"测试{name}相关的{PHASE_LABEL[cat]}能力",
                setup_dialog=json.dumps([
                    {"role": "user", "content": f"我想处理{name}相关事务"},
                    {"role": "assistant", "content": "好的，请提供一些基本信息"},
                ], ensure_ascii=False),
                query=f"请回忆我之前关于{name}的关键信息",
                expected_answer=f"期望回答涵盖{name}的核心要点，关键信息准确无误",
                tags=json.dumps(tags, ensure_ascii=False),
                created_at=datetime(2026, 6, 1, 10, 0, 0),
                updated_at=datetime(2026, 7, 15, 14, 0, 0),
            ))
    return defs


# ---------- Runs ----------
def build_runs():
    runs = []
    # 每个版本产生若干次运行，日期从 06-20 到 08-29 分布
    base_date = datetime(2026, 6, 20, 10, 0, 0)
    run_count_per_version_phase = [3, 3, 3, 4]  # v0.1:3, v0.2:3, v0.3:3, v0.4:4
    day_offset = 0

    # 通过率基准：版本越高越高
    version_base_rate = {"v0.1": 0.72, "v0.2": 0.80, "v0.3": 0.87, "v0.4": 0.92}
    phase_factor = {"base": 1.00, "multi_session": 0.96, "proactive": 0.90}

    for v_idx, version in enumerate(VERSIONS):
        for _ in range(run_count_per_version_phase[v_idx]):
            for phase in PHASES:
                # 跳过 base 在 v0.4 的重复、保持数据量合理
                if version == "v0.1" and phase != "base":
                    continue
                if version == "v0.2" and phase == "proactive":
                    continue

                run_at = base_date + timedelta(days=day_offset, hours=random.randint(0, 10))
                day_offset += random.randint(3, 7)

                total = 20
                base_rate = version_base_rate[version] * phase_factor[phase]
                noise = random.uniform(-0.04, 0.04)
                rate = max(0.35, min(1.0, base_rate + noise))
                passed = int(total * rate)
                if random.random() < 0.5:
                    passed += random.choice([-1, 0, 1])
                passed = max(0, min(total, passed))

                run = TestRun(
                    id=f"run_{uuid.uuid4().hex[:10]}",
                    version=version,
                    phase=phase,
                    run_at=run_at,
                    total_cases=total,
                    passed_count=passed,
                    pass_rate=round(passed / total, 3),
                    duration_seconds=round(random.uniform(80, 260), 1),
                    config_snapshot=json.dumps({
                        "k": random.choice([3, 5, 7]),
                        "retrieval_mode": random.choice(["hybrid", "dense", "sparse"]),
                        "chunk_size": random.choice([256, 512, 1024]),
                    }),
                    notes=random.choice([
                        None,
                        "混合检索模式",
                        "改进了召回排序",
                        "调整了 chunk 策略",
                        "修复了多会话上下文丢失",
                    ]),
                    triggered_by=random.choice(["manual", "ci", "scheduled"]),
                    status="completed",
                    progress=1.0,
                )
                runs.append(run)
    runs.sort(key=lambda r: r.run_at)
    return runs


# ---------- Results per run ----------
def build_results(defs_by_cat, runs):
    results = []
    for run in runs:
        defs = defs_by_cat.get(run.phase, [])
        if not defs:
            defs = list(defs_by_cat.values())[0]
        # Take first total_cases defs (round-robin)
        chosen = []
        i = 0
        while len(chosen) < run.total_cases:
            chosen.append(defs[i % len(defs)])
            i += 1

        passed_needed = run.passed_count
        pass_flags = [1] * passed_needed + [0] * (run.total_cases - passed_needed)
        random.shuffle(pass_flags)

        for idx, (case_def, passed) in enumerate(zip(chosen, pass_flags)):
            if passed:
                score = round(random.uniform(0.80, 1.00), 2)
            else:
                score = round(random.uniform(0.10, 0.69), 2)
            actual_variant = "correct" if passed else f"wrong_{idx % 5}"
            results.append(TestCaseResult(
                id=f"res_{uuid.uuid4().hex[:10]}",
                run_id=run.id,
                case_id=case_def.case_id,
                case_name=case_def.name,
                category=run.phase,
                version=run.version,
                passed=passed,
                score=score,
                expected_answer=case_def.expected_answer,
                actual_answer=_make_actual(case_def, passed, actual_variant),
                retrieved_memories=json.dumps(_make_retrieved(case_def), ensure_ascii=False),
                error_message=None if passed else _make_error(case_def, idx),
                latency_ms=random.randint(350, 3200),
                created_at=run.run_at + timedelta(seconds=idx * random.randint(3, 10)),
            ))
    return results


def _make_actual(case_def: TestCaseDefinition, passed: bool, variant: str) -> str:
    if passed:
        return f"[PASS][{case_def.case_id}] {case_def.expected_answer}"
    if variant == "wrong_0":
        return f"[FAIL] 混淆了其他客户的信息，给出了无关数据"
    if variant == "wrong_1":
        return f"[FAIL] 遗漏了关键参数，只回答了部分信息"
    if variant == "wrong_2":
        return f"[FAIL] 输出了重复内容，没有理解用户真正意图"
    if variant == "wrong_3":
        return f"[FAIL] 没有调用记忆，回答为通用模板"
    return f"[FAIL] 与预期偏差较大 - 原问题: {case_def.query}"


def _make_retrieved(case_def: TestCaseDefinition):
    return [
        {
            "id": f"mem_{case_def.case_id}_01",
            "relevance": round(random.uniform(0.75, 0.99), 3),
            "snippet": f"{case_def.name} 相关上下文片段 A...",
            "source": "long_term_memory",
        },
        {
            "id": f"mem_{case_def.case_id}_02",
            "relevance": round(random.uniform(0.55, 0.85), 3),
            "snippet": f"{case_def.name} 相关上下文片段 B...",
            "source": "short_term_memory",
        },
    ]


def _make_error(case_def: TestCaseDefinition, idx: int) -> str:
    reasons = [
        f"关键实体缺失 - 未回忆起 {case_def.name} 的核心参数",
        f"上下文混淆 - 混入了其他会话的信息",
        f"检索失败 - 相关记忆评分不足，未进入 Top-K",
        f"幻觉生成 - 生成了不在任何记忆中的内容",
        f"格式错误 - 输出结构不符合评测要求",
    ]
    return reasons[idx % len(reasons)]


def main():
    init_db()

    with get_session() as session:
        # Clean existing evaluation data
        for model in [TestCaseResult, TestRun, TestCaseDefinition]:
            session.query(model).delete()
        session.commit()

        # 1. Definitions
        defs = build_case_defs()
        session.add_all(defs)
        session.commit()
        print(f"Seeded {len(defs)} case definitions")

        # 2. Runs
        runs = build_runs()
        session.add_all(runs)
        session.commit()
        print(f"Seeded {len(runs)} test runs")

        # 3. Results
        defs_by_cat: dict[str, list[TestCaseDefinition]] = {}
        for d in defs:
            defs_by_cat.setdefault(d.category, []).append(d)
        results = build_results(defs_by_cat, runs)
        session.add_all(results)
        session.commit()
        print(f"Seeded {len(results)} case results")

    print("Done.")


if __name__ == "__main__":
    main()
