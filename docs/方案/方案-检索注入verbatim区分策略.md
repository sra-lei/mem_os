# 方案：检索注入 verbatim 区分策略（区分型 verbatim 准入）

> 状态：**v1 已实现并验证（11/20 → 14/20）；v2 重构为单一职责策略链、去除 Enable 开关，
> 行为不变并已复测**（2026-09-07，layer1 struct/assert/top_k=15）
> 对应代码：`src/os_mem/core/retrieval_strategies.py`
> 目的：解决 layer1 struct 评测"库里事实完备但 top-15 注入覆盖不足 → 召回率低"的检索侧瓶颈，
> 并记录"为什么区分型 verbatim 策略优先于提取侧收敛（C）与提取收紧（B）"的决策依据。

---

## 一、问题与背景

layer1（数字密集长对话）struct 评测在 top_k=15、moonshot 判分下稳定在 55%–65%
（run_835b=55%、run_7979=60%）。2026-09-06 落地的三层归因审计
（`tests/audit_run_attribution.py`）对 run_7979 / run_835b 逐 case 复核得到：

- **覆盖漏（库里有、没进注入窗口）是绝对主因**：run_7979 失败 case 的期望点缺失合计
  `提取漏 3 : 覆盖漏 26 : 回答漏 3`，7/8 失败主导层 = retrieval；
- 三个"提取漏"中真漏仅 1 个（case 18 的 `Freedom 2045` 基金名，见下），其余为推导值/别名伪漏；
- **verbatim 句是部分关键信息的唯一载体**：case 18 的三账户余额只存在于 verbatim 兜底句
  （结构化 19 条未存余额）；case 20 的单价明细（Emma $325 / Olivia $292.50 等）同理；
- **现有 diversity 策略（结构化优先 + verbatim ≤1/3 补位）压 verbatim 压过了头**：
  run_835b（verbatim 霸榜时代）→ run_7979（策略生效后）对比，case 18 从 0.5 跌到 0.1、
  case 13 覆盖缺从 5 增到 7 —— 净通过率 +1 case（55→60%）掩盖了这组负向。

结论：当前瓶颈不在"记太多"，而在**注入窗口对"结构化未覆盖、仅 verbatim 承载的
信息"不可及**。verbatim 对 layer1 不是纯噪音，其中一部分是唯一答案载体；现有
cap 一刀切，把噪音和资产一起压了。

## 二、术语

| 词 | 含义 |
|---|---|
| 提取漏 | 期望信息点在记忆库（struct_memories）中不存在 |
| 覆盖漏 | 库里有，但未进本次 top-15 注入窗口 |
| 回答漏 | 进窗但答案未给出（污染/回答层） |
| verbatim | 正则兜底入库的原文句，key=`verbatim_<sha1>`，每句唯一 key |
| 结构化 fact | LLM 提取事实，key 为语义槽（如 `checking_account_number`） |
| 区分型 verbatim 准入 | 按"是否携带窗口内尚未覆盖的信息"决定 verbatim 是否注入，而非一律压到 ≤1/3 |

## 三、决策依据：为什么"区分型 verbatim 策略"优先（对既有排序的修正）

早前结论曾主张 **C 优先（verbatim key 收敛为根）→ B（提取收紧）并入 C → A 作为过渡调参**。
审计证据（§一）对该排序的三点修正：

1. **机制根因 ≠ 当前瓶颈**：覆盖漏 26 点是注入/排序层问题（A 的战场），不是提取收敛问题。
   C 的收益面（多版本收敛、防污染）只对应 3 个回答漏点 + 02/20 类污染，是少数派；
2. **C 的版本收敛治不了结构化污染**：02 的 `48h vs 24-48h` 冲突是两条**不同 key 的结构化事实**
   （LLM key 不稳定），verbatim 指纹归组不解决它——那需要 §10 的 LLM key 纪律 + 新旧判定；
3. **验证成本排序相反**：提取/入库改动（C、B）按 agents.md §4.3 必须清 conv_meta + mem_os
   全量重跑（≈50min+ 起）；检索/注入侧改动（A）**无需清缓存**，缓存 run ≈12min 即可验证。
   拿最高的验证成本修占比最小的病灶，是原排序的错误。

因此修正为：**A（区分型 verbatim 注入）优先**——它命中主导缺失层、成本最低；
C 的"按语义内容去重而非按 key 字符串"思想**下沉到检索侧**先兑现（信息唯一性判定），
全量 key 收敛 + §10 新旧判定留待多会话（layer2/3）成为主战场时再上。

**翻案条件**：layer2/3 多会话下，跨会话同 key 多版本、旧值污染放大，届时 C/§10 权重上升，
本排序可能再次反转（C 成为第一步）。

## 四、设计（v1）

### 4.1 目标行为

对检索候选（fetch_k = top_k × 3，RRF 融合后）做准入选择，注入窗口满足：

1. 结构化 fact 仍按 (category, key) 去重、相关性序优先（继承现 diversity 优点）；
2. verbatim 准入条件（满足才占用窗口位）：
   a. **非噪声**：疑问句/口语碎片（沿用 `_VERBATIM_NOISE`）过滤；新增比较/调整类标记
      （`instead of` / `rather than` / `would be` / `no wait` 等）—— 治 case 20 的
      `$308.75 instead of $617.50 for that week` 中间值；
   b. **携带可核验 token**：句内无金额/编号/≥4 位数字 token 的 verbatim 视为无信息载体，跳过；
   c. **信息唯一**：其数值 token 未被已入窗事实（结构化或已准入 verbatim）覆盖——覆盖即冗余，
      跳过（治 02/17 类多版本同值扎堆）；
   d. **硬上限**：verbatim 总量 ≤ ceil(top_k / 2)（防英文兜底句风暴霸榜，继承 835b 教训的
      安全网，但上限从 1/3 放宽到 1/2 且只对"准入后"的 carrier 计数）。
3. 无 verbatim 满足准入时，窗口由结构化事实填满（与现状一致）。

### 4.2 伪代码

```
window = []            # 注入结果（按相关性序）
coverage = set()       # 已入窗事实的数值 token 集
seen_structured = set()
vbudget = ceil(top_k / 2)

for hit in hits:                       # hits 按 RRF 相关性升序
    if len(window) >= top_k: break
    if 非 verbatim:
        if (cat, key) in seen_structured: continue
        seen_structured.add((cat, key))
        window.append(hit); coverage |= tokens(hit)
        continue
    # verbatim
    if vbudget <= 0: continue
    if noise(hit): continue             # 疑问/碎片 + 比较/调整标记
    toks = tokens(hit)
    if not toks or toks <= coverage: continue   # 无载体 或 冗余
    window.append(hit); coverage |= toks; vbudget -= 1

return window[:top_k]
```

### 4.3 变更点与口径（v2 重构：单一职责策略链，无 Enable 开关）

- 改动仅限 `retrieval_strategies.py`（策略层）与调用方 `struc_mem_service.py`
  （fetch 放大取回改为无条件生效）；
- 数值 token 口径与 `tests/eval/judge/impl/assert_judger.py` /
  `tests/audit_run_attribution.py` 对齐（金额/代码/卡片/≥4 位数字，归一化去 $ 千分位连字符）；
  os_mem 不得 import tests，故正则在本文件内私有复制一份并注明同步义务；
- **v2（2026-09-07）**：不做大而全的单体策略 + 运行时开关，拆成固定顺序、单一职责的
  小策略链 ``STRATEGY_CHAIN``，**默认全部加载，删除 `ENABLE_DIVERSITY` /
  `ENABLE_VERBATIM_GATE` 开关**：
  1. `VerbatimNoiseFilter`——剔除噪声 verbatim（问句/碎片/比较调整句）；
  2. `StructuredKeyDedup`——结构化事实 (category, key) 去重保首见；
  3. `RedundantVerbatimFilter`——剔除无新数值信息的 verbatim（信息唯一性准入）；
  4. `VerbatimQuota`——verbatim ≤ max(ceil(top_k/3), top_k − 结构化数)；
  5. `StructuredQuota`——结构化 ≤ top_k − verbatim 数（为 carrier 让位）；
  终装配：结构化在前、verbatim 补位，截断 top_k。
- 基线对比不再靠运行时开关：用旧版本代码跑同 run，或对 run 落库结果对照
  （v1→v2 重构行为不变，重构后复测见 §八）。

## 五、验证方案

1. 离线单测（`tests/unit/test_retrieval_strategies.py`）：
   carrier 准入（余额句带窗口未覆盖 token → 入窗）、冗余跳过（同值 verbatim 不入）、
   无 token verbatim 跳过、噪声标记过滤、verbatim 硬上限、既有回归（结构化优先）全绿；
2. 评测 A/B（同库缓存数据、同 judge=assert、同 top_k=15，仅策略不同）：
   基线（现代码，已跑）vs v1（新策略）；用 `tests/audit_run_attribution.py` 复核两轮，
   判据：case 18/13/20 覆盖漏下降、case 02/17 污染不新增、净通过率不降；
3. 说明：assert 与 moonshot 分数不可跨判分对比，两轮之间同口径（assert）可比；
   若净收益为正，再补一轮 moonshot 全量确认（口径一致后与 55%/60% 历史对照）。

## 六、被否方案

| 方案 | 否决理由 |
|---|---|
| B 先行（提取收紧：过程值/问句负面样本） | 审计显示污染仅 3 个回答漏点，非瓶颈；且与 case 18 真缺口（结构化欠抓 + 兜底盲区）方向相反，收紧会让 18 更糟；改提取需清缓存全量重跑 |
| C 先行（verbatim key 收敛为根） | 收益面窄（版本收敛），治不了结构化 key 不稳定的 02 类；验证成本最高；当前瓶颈在注入覆盖，不在收敛 |
| 全量放开 verbatim（835b 时代行为） | run_835b 55% 已证伪：英文句霸榜挤掉结构化，污染类 case 恶化 |
| 纯 top_k 上调（15→30） | 注入变长、token 成本升、噪音同步放大；未先解决"窗内构成"就加窗，收益不确定 |
| 检索侧 LLM 判噪 | 每 query 一次额外 LLM 调用，成本/延迟不可接受，且判据不稳定 |

## 七、附录：业界机制对照

- **RAG 检索后处理**：top-k 命中后普遍做去重/多样性/重排（MMR 等），本策略同属该层；
  差异点：本项目去重依据是**数值信息唯一性**而非纯向量相似度（数字精确回忆场景下
  token 级唯一性更贴近判分口径）；
- **RRF 融合**（Milvus hybrid_search）：dense+sparse 取交集排序，sparse 路对英文 verbatim
  天然强（本项目 BM25 analyzer 对中文不切词，中文结构化事实只走 dense 路——是后续可优化点，
  不在本方案范围）；
- **记忆系统"每槽一版"**：SQLite 侧 (user,key) 冲突 UPDATE 已是该语义；verbatim 指纹 key
  绕过了它，本方案先在检索侧以信息唯一性近似收敛，入库侧全量收敛（C/§10）另行立项。

## 八、验证结果（v1 已落地，2026-09-07）

实验设计：同一缓存库（conv_meta 全 COMPLETED，无提取变动）、同判分（assert）、同 top_k=15，
仅策略不同（旧版 structured 优先 + verbatim ≤1/3 vs v1 区分准入）。离线单测
`tests/unit/test_retrieval_strategies.py` 14 个全绿（含 6 个 v1 新用例）。

**v2 重构复测（2026-09-07）**：单一职责策略链与 v1 准入语义逐一等价（见 §4.3），
单测重写为 18 个（组件级 + 链级）全绿；layer1 assert 复测结果与 run_c087f9ee 一致
（14/20，失败集 01 11 13 15 16 19，窗口构成 verbatim 5/structured 10）——重构不改行为。

| 指标 | 基线 run_c887cb12（旧策略） | v1 run_c087f9ee（区分准入） |
|---|---|---|
| 通过 | **11/20** | **14/20** |
| 失败 case | 01 11 13 15 16 17 18 19 20 | 01 11 13 15 16 19 |
| 期望点缺失合计 | 提取漏 4 / 覆盖漏 30 / 回答漏 6 | 提取漏 3 / 覆盖漏 18 / 回答漏 3 |

**净效果（审计工具逐 case 复核）**：
- **恢复 3 个 case（无新增失败）**：17 veterinary、18 retirement_planning（0.0→通过，四个
  余额靠 carrier verbatim 入窗）、20 daycare_enrollment（0.25→通过，单价明细入窗）；
- 覆盖漏从 30 → 18 个期望点（-40%），回答漏 6 → 3；仍失败 case 的覆盖缺同步收窄
  （13 tax：7→6、19 wedding：4→3、11 mortgage：窗口内容轮换但总数仍差 2）；
- 窗口构成从"全 structured"变为"structured 10 + carrier ≤5"的混合（`verbatim 5/structured 10`），
  与设计一致。

**代价与遗留（诚实记录）**：
1. **15 college 代价可见**：课程代码（mat151/cs101/eng101/fye100）多由结构化课程事实承载，
  固定 1/3 预留槽会挤掉部分结构化事实——15 覆盖缺 4→6，但分数 0.38→0.46（carrier 英文课表
  句仍补回部分信息），仍失败。预留比例（1/3）与"按信息唯一性逐槽竞价"是后续调参点；
2. **13/11 的残留覆盖缺**是 fetch 层问题（候选 45 内没有对应事实/排序不足），非窗口策略可解
  ——下一杠杆是 fetch_k/top_k 或 RRF 调参；
3. **提取侧真缺口仍在**：01 的路由号 123006800、18 的基金名 Freedom 2045（裸年份不进兜底
  正则、结构化也未抓）——属 C/§10 立项内容，本方案不覆盖；
4. 判分口径为 assert；moonshot 语义判分复测已执行并确认收益（见下 §八-续 run_b6d6bac455）。

### §八-续：后续杠杆实验数据（2026-09-07 晚，均为缓存库、同提取 prompt 指纹）

> 两轮属于**不同杠杆**，通过率不可直接互比（judge 与 top_k 均不同）；每轮与同口径历史对照。

**杠杆 A：策略本身（moonshot 语义判分确认）—— run_b6d6bac455**

| 指标 | run_7979（策略前 moonshot） | run_b6d6bac455（区分策略 moonshot） |
|---|---|---|
| 通过 | 12/20（60%） | **14/20（70%）** |
| 失败 case | 02 11 13 14 15 17 18 20 | 11 13 14 15 17 18 |
| 覆盖漏合计 | 26 | 15 |

- 与 assert 口径的 14/20（run_c087f9ee）一致 → **策略收益跨判分口径成立**（55/60 → 70%）；
- 02、20 恢复通过（污染类在语义判分下被策略净收益覆盖）；窗口构成 verbatim 5/structured 10 确认策略生效；
- 残余归因：覆盖漏 15（11/13/15，fetch 层 + 1/3 预留槽代价）、回答层（17：窗内有 $47/$859 未答，
  同 case 在 assert run 通过 → answer LLM 单样本方差）、真提取漏（18 的 2045 基金名恒存）。

**杠杆 B：窗口大小（top_k 15 → 30）—— run_bf483e00c1**

| 指标 | run_c087f9ee（assert/top_k15） | run_bf483e00c1（assert/top_k30） |
|---|---|---|
| 通过 | 14/20 | **16/20（80%）** |
| 失败 case | 01 11 13 15 16 19 | 01 03 13 16 |
| 覆盖漏合计 | 18 | 5 |

- **正收益**：恢复 11/15/19（目标值分散多槽的覆盖型 case）；13 覆盖漏 6 → 4；
- **代价（收益递减信号）**：回答漏升（03 medical 0.0——确认号 MV-789234 在窗内结构化+verbatim
  双份仍漏答；13 窗内有值未答 3 点；16 的 $36,250 同理）→ 宽窗稀释答案模型的答全率；
- **残余**：01 路由号恒为真提取漏；13 的 4 个金额点（1525/340/1210/450）连 fetch=90 都排不进
  ——排序问题，非加窗可解；
- **注意**：judge 与 top_k 同时变化，80% 不能直接与 70%（moonshot）比；待补 top_k=30 + moonshot
  确认口径一致的窗口收益。

**小结**：策略杠杆已在 assert + moonshot 双口径验证（~70-80% 区间）；窗口杠杆显示覆盖漏持续下降
但回答层开始反向——下一步不做纯加窗，改做"两层预算/按信息量竞价"或先补 k30-moonshot 确认轮。

### §八-续2：跨机独立复现确认（2026-09-07，Hermes 云服务器）

> 前述 v1/v2 实验与落库 run 均在开发机完成；本机（云服务器 119.91.103.179，Hermes
> 运行环境）在 git 同步后以同代码、同口径（layer1 struct/assert/top_k=15、同缓存库
> conv_meta 全 COMPLETED）独立复跑两轮，验证结论跨机可复现。两机 memories.db 的
> layer1 提取同源自 09-05/06（失败集完全一致即佐证），检索/判分代码同步至同一 commit。

| 轮次 | 代码版本（策略） | run_id | 通过 | 失败 case | 归因（提取漏/覆盖漏/回答漏） |
|---|---|---|---|---|---|
| 基线复现 | 3d70105（431f9ca 旧策略：结构化优先+verbatim≤1/3） | run_2acd8235bd | 11/20 | 01 11 13 15 16 17 18 19 20 | 1 / 33 / 7 |
| v2 复测 | 995e770（7047cd5 策略链 v2 固定链） | run_80f3624109 | 14/20 | 01 11 13 15 16 19 | 1 / 20 / 6 |

**对照结论**：
- **基线失败集与开发机 run_c887cb12 完全一致**（01 11 13 15 16 17 18 19 20）→ 旧策略
  行为与提取库跨机稳定，11/20 是可复现基线；
- **v2 复测失败集与开发机 run_c087f9ee 完全一致**（01 11 13 15 16 19）→ 策略链 v2
  重构"行为不变"跨机成立；覆盖漏 33→20（-39%）与开发机 v1 收益（30→18）同量级；
- 残留失败逐条归因亦与开发机一致：01 路由号 123006800 为真提取漏；11/13/15/16/19
  为 fetch 层（候选 45 内仍无对应事实/排序不足）+ 少量回答层代价（16 的 $36,250
  双份在窗仍未答出）。

**口径注**：`tests/audit_run_attribution.py` 对两机 run 均可用（只读 memos.db），但
落库 `retrieved_memories` 为纯文本注入列表（不含 key/category 字段），审计工具无法
据此分类窗口内 structured/verbatim 构成（旧 run 输出窗口分类多为 unmatched）；本节
仅采信其**逐层缺失计数**（按数值 token 与库/窗口文本比对，可靠）。"verbatim
5/structured 10"类窗口构成数字仅实验侧（策略层直读 hits）可得。
