# 方案：提取任务与 LLM 自愈适配器解耦（待评审）

日期：2026-09-09（v2 修正：恢复策略收敛于 provider 内部，v1 于同日废弃）· 状态：**待评审（未实施）**
关联：docs/实验记录-提取prompt精简AB-2026-09-09.md（prompt 迭代暴露：prompt 优化与模型强绑定）；
docs/方案-事实提取鲁棒性与成本优化.md（截断/成本修复已完成）；layer2 评测 run_98df5b6320 / run_7a2a539541。
涉及：`os_mem.extraction`（extractor / prompt）+ `os_mem.infra.llm`（base_client / deepseek_client / factory）
+ `configs/mem_settings`（提取 knobs）。

## 1. 问题：伪分离——client 与 prompt 表面解耦，实为 deepseek 专属耦合

现状分层：通用 client（`infra/llm`）与任务 prompt（`extraction/prompt.py`）分文件，看似干净，
但适配层偷偷硬编码 deepseek-v4-flash 假设，换模型会静默退化：

| # | 硬编码点 | 现状代码 | 换模型后果 |
|---|---|---|---|
| 1 | `response_format={'type':'json_object'}` 写死 | `_ExtractComplete.__init__` | moonshot 走 json-schema、部分 provider 无 json_object |
| 2 | 截断语义只存在于 DeepSeek | `DeepSeekClient.chat_outcome`；`outcome()` `hasattr` 回退 | 无 `chat_outcome` 的 client **静默退化**成重试风暴语义，无告警 |
| 3 | 分段/预算 knobs 全局化 | `mem_settings.DEEPSEEK_EXTRACT_*` | 全按 deepseek 输出预算+啰嗦程度标定，换模型整套错配 |
| 4 | 恢复策略写在任务层 | `FactExtractor.extract_chunk` 内 repair/切段分支 | 恢复策略（repair 安全模式、截断路由）是模型相关决策，却暴露给任务 |
| 5 | prompt 措辞策略（宁多勿漏/宁缺毋滥） | SYSTEM_PROMPT | 与模型天性强相关（AB 实证：同文本换语气，数字保真 0-25%） |

**结论**：三层混淆。真正正交的分层是——
- **任务语义**（JSON schema、类别白名单、分段编排、去重、verbatim 兜底、降级）：provider 无关；
- **模型恢复策略**（截断检测、repair/重生成/切段、重试、温度微调）：**每个模型各不同**，
  是"怎么跟这个模型要到合法结果"的实现细节 → 收敛在 provider 内部，**不暴露给任务层，
  也不进通用 schema**；
- **模型数据画像**（prompt 文本、max_tokens、单次调用预算上限）：纯数据，随 provider/model 换。

## 2. 目标架构（v2 修正）

```
FactExtractor（任务语义 · 不变其责）
  分段编排 → 对每段调 caller.extract(dialog_text) → 拿到「合法 facts 或明确失败」
  之后 dedup / verbatim 兜底 / prune / 降级 raw_conversation（仍是任务语义）
        │ 注入 validate（任务schema权：validate_response + 类别白名单）
        ▼
┌──────────────────────────────────────────────┐
│ 自愈 extraction caller（每 provider/model 一个实现）│
│   内部私有：response_format 协商、截断检测、      │
│   repair / 重生成+预算收紧 / 对半切段（策略=代码）│
│   重试次数、温度、usage 统计                      │
└──────────────────────────────────────────────┘
        │
        ▼
   ChatClient（纯传输：chat / chat_outcome —— 后者仅被 caller 内部使用）
```

- **任务侧契约**（FactExtractor 只认识这个）：
  ```python
  class ExtractionCaller(Protocol):
      def extract(
          self,
          dialog_text: str,
          *,
          validate: Callable[[str], list[MemoryFact]],   # 任务注入的 schema 校验
      ) -> CallResult
      # CallResult = { facts: list[MemoryFact] | None(全败), stats: {...遥测} }
  ```
  FactExtractor **看不到** finish_reason / repair / 截断信号——`outcome`、`repair` 鸭子类型接口
  从任务层退场（`chat_outcome` 若保留仅作 caller 内部实现细节）。
- **恢复循环在 caller 内部**：generate → `validate` 注入校验 → 不合法/截断 → 按本模型策略
  （repair 部分 JSON / 带预算收紧提示重生成 / 切段）→ 重试至上限 → 干净失败。
  repair 的安全默认 = **drop-partial**（只保留完整条目，未知模型不冒险续写=不编造精确值）；
  `continue` 续写模式只对验证过的强模型开——这些决策**写在各 caller 的代码里**，不进共享 schema。
- **模型数据画像缩水为纯数据**（供工厂选择 caller 与拼 prompt）：
  ```python
  @dataclass(frozen=True)
  class ModelProfile:
      provider: str
      model: str
      system_prompt: str        # 模板（含 {categories_section}/{max_facts} 占位）
      max_output_tokens: int
      chunk_budget: ChunkCaps   # 单次调用输入上限（字符/消息数/overlap/max_facts）——数据
      temperature: float
      caller: str               # 指向 provider 内自愈实现（策略注册点，不含策略字段）
  ```
  策略**没有** schema 字段（无 repair_mode/truncation_policy 等枚举）——策略随 caller 实现走。
- 分段编排仍在任务层（对话语义），但分段上限取 profile 数据（`chunk_budget`），
  避免任务层硬编码模型预算。

## 3. 边界与取舍

- v1 只做单 provider（现 `LLM_PROVIDERS="deepseek"`）；Failover 多路时各路走各自 caller，留待后续。
- answer / judge prompt 同构问题列范围外（`tests/eval/llm.py` / `moonshot_judger`），
  后续可套同一 caller 模式，本次不动。
- 不做通用 plugin 框架；caller = 每 provider 一个类 + 注册表（如 extraction callers 的
  `register_caller("deepseek", DeepSeekExtractionCaller)`），3 个 provider 内不过度设计。
- 开放问题（不阻塞架构，落地后单独小实验定）：截断空返回时 deepseek caller 内部用
  「重生成+预算收紧」还是「对半切段」——两者都是 caller 内部策略，可各自模型各选。
- config_snapshot 扩展记 provider+model+prompt 指纹，run 可比性保留。

## 4. 实施步骤（先等价迁移、再策略搬家；分 commit，纯逻辑单测；不跑评测）

1. 新增 `extraction/callers/`：`ExtractionCaller`/`CallResult` 契约 + deepseek caller 首版
   ——把现 `_ExtractComplete`（prompt 拼装 + chat 调用）迁入，行为不变；
   FactExtractor 先经薄适配层调用（任务侧接口暂保持，便于等价验证）
2. 策略搬家：`extract_chunk` 内 repair/切段分支删除，恢复循环移入 deepseek caller
   （validate 注入）；任务侧 `outcome`/`repair` 接口退场
3. `ModelProfile` 纯数据画像 + 工厂按 client/provider 解析（默认画像从现状固化，逐字节等价）；
   chunk knobs 改由 profile.chunk_budget 供给任务层分段
4. 观测：caller 遥测（calls/截断/repair/降级）+ eval config_snapshot 增记 model/profile
5. 单测：caller 恢复循环（fake client 注入各失败形态）、validate 注入、profile 解析兜底、
   FactExtractor 契约简化后全量回归；**全仓现有 unit 保持全绿 = 等价迁移通过**
6. 提交即推送；prompt 内容迭代（AB v1.5）此后只改 profile 数据 / caller 内部策略

## 5. 风险

| 风险 | 缓解 |
|---|---|
| 重构引入行为漂移 | caller 首版从现状固化逐字节等价 + 155 单测绿 +（可选）提取账回归对比 |
| 策略搬进 caller 后任务层失控感 | 契约面收窄到 `extract(dialog_text, validate)`——校验权仍由任务注入持有 |
| repair 续写编造精确值（弱模型） | caller 内安全默认 drop-partial；continue 仅强模型显式开启 |
| failover/多模型错配 | v1 单 provider；caller+profile 注册缺省显式告警 |
