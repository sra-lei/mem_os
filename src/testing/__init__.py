"""Evaluation domain package: framework (runner + judge + LLM), API, DB.

The memory contract (Memory / MemoryProvider) lives in the os_mem package;
this framework only consumes it. Dependency direction: testing -> os_mem.

Evolution roadmap (do NOT jump to the endgame):
  v0.1  base:          single-conversation recall (layer1)  <- current
  v0.2  multi_session: cross-conversation retrieval (layer2) <- same runner, real memory provider needed
  v0.3  proactive:     synthesis + proactive service (layer3)
  later phases (EvalView需求文档.md 11.x): real LLM providers, judge caching/audit,
        progress SSE, exports — added when the memory system works.
"""
