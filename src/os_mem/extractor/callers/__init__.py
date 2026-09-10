"""``os_mem.extractor.callers`` —— provider 无关的 caller 上层 + 各 provider 具体实现。

结构：
- ``framework.py``         ：provider 无关框架（``ExtractionCaller`` 协议 /
  ``build_extraction_caller`` 工厂，re-export ``ExtractionCore``）
- ``deepseek_caller.py``   ：DeepSeek 具体实现（DeepSeekExtractionCaller +
  prompt 模板/渲染/指纹 + build_extract_complete 薄兼容）
"""
