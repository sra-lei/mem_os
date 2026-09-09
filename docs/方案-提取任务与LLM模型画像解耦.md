# 方案：提取任务与 LLM 模型画像解耦（待评审）

日期：2026-09-09 · 状态：**待评审（未实施）**
关联：docs/实验记录-提取prompt精简AB-2026-09-09.md（prompt 文本迭代暴露的问题——
prompt 优化与模型强绑定）；docs/方案-事实提取鲁棒性与成本优化.md（截断/成本修复已完成，
下一步杠杆在 prompt 与模型协同）；layer2 评测 run_98df5b6320 / run_7a2a539541。
涉及：`os_mem.extraction.prompt`（现 prompt 与适配器）+ `os_mem.infra.llm`
（base_client / deepseek_client / factory）+ `configs/mem_settings`（提取 knobs）+
`extraction/extractor.py`（chunk knobs 读取）。

## 1. 问题：伪分离——client 与 prompt 表面解耦，实为 deepseek 专属耦合

现状分层：通用 client（`infra/llm`：ChatClient 契约 / DeepSeekClient / FailoverClient 工厂）与
任务 prompt（`extraction/prompt.py`：SYSTEM_PROMPT / REPAIR_PROMPT / _ExtractComplete 适配器）
分文件放置，看似干净。但**适配层偷偷硬编码了 deepseek-v4-flash 的假设**，换模型会静默退化：

| # | 硬编码点 | 现状代码 | 换模型后果 |
|---|---|---|---|
| 1 | `response_format={'type':'json_object'}` 写死 | `_ExtractComplete.__init__` | moonshot 走 json-schema（`moonshot_judger._RES_SCHEMA`）、部分 provider 无 json_object——机制不同需按模型适配 |
| 2 | 截断语义只存在于 DeepSeek | `DeepSeekClient.chat_outcome`；适配器 `outcome()` 里 `hasattr(client,'chat_outcome')` 回退 | 无 `chat_outcome` 的 client **静默退化**成旧的重试风暴语义（刚修的病复发），无告警 |
| 3 | 分段/输出预算 knobs 全局化 | `mem_settings.DEEPSEEK_EXTRACT_MAX_CHARS=4500 / MAX_MSGS=35 / MAX_FACTS=60 / MAX_TOKENS=8192` | 全部按 deepseek 的输出预算 + 啰嗦程度标定；换输出 16k / 措辞精简的模型，整套参数错配 |
| 4 | prompt 措辞策略（宁多勿漏 vs 宁缺毋滥） | SYSTEM_PROMPT 文本 | 与模型天性强相关（AB 实验实证：同文本换语气保真率 0-25%） |

**结论**：提取任务其实有两个正交层，现在混在一起——
- **任务契约**（JSON schema、校验、分段编排、降级、去重）：provider 无关 → `FactExtractor` 已正确内聚；
- **模型画像**（prompt 文本、response_format 机制、输出预算→分段参数、截断/重试策略、温度、示例风格）：**与具体模型强绑定**，当前散落在 prompt.py 常量 + settings + client 实现三处，无统一载体。

## 2. 目标架构（最小调整）

```
FactExtractor（纯逻辑 · 任务契约 · 不变）
     ↑ 注入 complete 回调（outcome + repair）
┌──────────────────────────────────────────────┐
│ extraction adapter factory                   │
│   build_complete(client, model_profile)      │  ← 绑定点：client + 画像合一
└──────────────────────────────────────────────┘
model_profile 注册表（按 provider:model）:
  { system_prompt 模板, repair_prompt, response_format 模式,
    max_output_tokens, 分段 knobs（或按预算推导）, max_facts,
    温度, 截断/重试策略, prompt_fingerprint }
```

- `FactExtractor` / `extractor.py` 保持纯逻辑：chunk knobs 已参数化（chunk_dialog 的
  max_chars/max_msgs/overlap），只需把「读全局 settings」改为「调用方按 profile 传入」。
- 新增 `extraction/profile.py`：
  ```python
  @dataclass(frozen=True)
  class ModelProfile:
      provider: str
      model: str
      system_prompt: str          # 模板（含 {categories_section}/{max_facts} 占位）
      repair_prompt: str
      response_format: dict | None
      max_output_tokens: int
      max_chunk_chars: int
      max_chunk_msgs: int
      chunk_overlap: int
      max_facts: int
      temperature: float
      # 截断策略跟随 client 能力：有 chat_outcome 走切段，否则走旧重试（v1 显式标注不静默）

  EXTRACTION_PROFILES: dict[str, ModelProfile]   # key = "deepseek:deepseek-v4-flash"
  def resolve_profile(client, provider=None, model=None) -> ModelProfile  # 缺省兜底 __default__
  ```
- 默认 profile = 从现状固化（deepseek-v4-flash：现行 prompt + knobs），**行为逐字节不变**
  ——重构本身是等价迁移，prompt 内容的后续迭代（如 AB v1.5）只改 profile 数据不动代码。
- `prompt.py` 改造：`build_extract_complete(client, profile=None)`；`_ExtractComplete` 构造收
  profile；`profile=None` 时按 client 推导（向后兼容单测与临时调用）。
- settings 中 DEEPSEEK_EXTRACT_* 保留为 profile 默认值来源，profile 解析时读取（避免双源漂移，
  迁移期只读，不立即删除）。

## 3. 边界与取舍

- **v1 只做单 provider 场景**（现配置 `LLM_PROVIDERS="deepseek"`）；FailoverClient 多路时
  每路 client 对应各自 profile 的解析留待后续（现状 failover 属预留路径，未启用）。
- **answer / judge prompt 同构问题列范围外**：`tests/eval/llm.py`（answer）与
  `moonshot_judger` 的 prompt 同样是"模型专属文本 + 全局 settings"，可后续套同一模式，
  本次不动（评测侧非提取链路）。
- **不做通用 plugin/框架抽象**：画像注册表 + 兜底足够，3 个 provider 内不过度设计。
- prompt 指纹：config_snapshot 已记 extract.system 模板指纹；profile 引入后扩展记录
  provider+model+profile 版本（run 可比性保留）。

## 4. 实施步骤（行为不变的等价重构，分 commit，纯逻辑单测；不跑评测）

1. `extraction/profile.py`：ModelProfile + 注册表 + resolve_profile（默认画像从现状固化）
2. `prompt.py`：build_extract_complete / _ExtractComplete 收 profile（None 兼容）；
   response_format / max_facts 渲染走 profile
3. `extractor.py` 调用链：extract_structured_facts / StrucMemService 持有 profile，
   chunk knobs 由 profile 传入（替换直接读 settings）
4. 观测：eval config_snapshot 增记 provider/model/profile 指纹
5. 单测：profile 解析（已知 key/未知 key/兜底）、build_complete(profile) 消息拼装断言、
   profile=None 兼容等价性；全仓现有 155 unit 保持全绿 = 等价迁移通过
6. 提交即推送；完成后 prompt 内容迭代（AB v1.5）改 profile 数据即可复测

## 5. 风险

| 风险 | 缓解 |
|---|---|
| 重构引入行为漂移 | 第 1 步 profile 从现状固化逐字节等价 + 155 单测绿 + （可选）提取账回归对比 |
| 双源漂移（settings vs profile） | 迁移期 settings 只读不删，profile 解析单点读取 |
| failover/多模型下画像错配 | v1 明确单 provider；resolve 缺省兜底显式告警 |
