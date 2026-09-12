"""os_mem.extractor —— 记忆提取域（被编排的领域执行器）。

定位：不属 utils 小工具，也不属 core 业务编排——纯数据变换、无存储/网络副作用
（LLM 经注入回调或 caller），可离线单测。结构：

- ``fact_extractor.py``    ：FactExtractor（校验/分段/LLM 提取委托/并行编排/全败
  降级/去重，LLM 结构化链路任务语义；正则兜底/R1 剪枝在 RegularExtractor）
- ``regular_extractor.py`` ：不依赖 LLM 的确定性正则提取（RegularExtractor）——
  verbatim 精确信息句兜底（金额/编号/%/时刻/日期，宽进）/ R1 覆盖剪枝
- ``extraction_core.py``   ：单一恢复循环（``ExtractionCore``，provider 无关）
- ``callers/``             ：provider 无关 caller 框架（``callers/framework.py``：
  ExtractionCaller 协议 / 工厂按 profile.caller 分发）+ 各 provider 实现
  （``callers/deepseek_caller.py``：DeepSeek 具体实现，含 SYSTEM_PROMPT/
  REPAIR_PROMPT 模板与渲染、build_extract_complete 薄兼容、指纹）
- ``model/models.py``      ：提取域数据类单一存放点（NormalizedKey / CallResult /
  ChunkCaps / ModelProfile，纯数据无策略）
- ``utils/``               ：共享纯函数/常量（extract_utils：split_text_midpoint /
  dedup_facts / EXTRACTION_STATS_KEYS / empty_extraction_stats；token_utils：
  fact_tokens·norm_token 精确信息 token 口径；normalize：D4 key 归一）——单一实现源
- ``llm_util.py``          ：默认模型数据画像构造（build_default_profile）

编排与存储由 core/services/struc_mem_service 负责；本包不在此 re-export——
各入口直接 import 具体模块。
"""
