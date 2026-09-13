"""``os_mem.core.extract.callers`` —— provider 无关的 caller 上层 + 各 provider 具体实现。

结构：
- ``base_caller.py``      ：provider 无关框架（``ExtractionCaller`` 协议 /
  ``build_extraction_caller`` 工厂，按 ``client.client_name()`` 分发）
- ``deepseek_caller.py``  ：DeepSeek 具体实现（DeepSeekExtractionCaller +
  prompt 模板/渲染/指纹 + ``build_caller`` 工厂）
"""
from .base_caller import ExtractionCaller, build_extraction_caller
from .deepseek_caller import DeepSeekExtractionCaller, build_caller

__all__ = [
    'ExtractionCaller',
    'build_extraction_caller',
    'DeepSeekExtractionCaller',
    'build_caller',
]