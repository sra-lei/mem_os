"""``os_mem.core.extract.callers`` —— provider 无关的 caller 上层。

具体 provider 实现（``deepseek_caller`` 等）不经本包顶层 re-export——
工厂 ``build_extraction_caller`` 按 ``client.client_name()`` 懒加载实现模块
（注册表见 ``base_caller._CALLER_IMPL_MODULES``），消费方一律深路径导入
具体实现或仅经工厂获取，避免上层包反向绑定实现。

结构：
- ``base_caller.py``：``ExtractionCaller`` 协议 + ``build_extraction_caller`` 工厂
- ``deepseek_caller.py``：DeepSeek 具体实现（深路径导入，不在此导出）
"""
from .base_caller import ExtractionCaller, build_extraction_caller

__all__ = ['ExtractionCaller', 'build_extraction_caller']
