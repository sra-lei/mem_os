"""os_mem.core.extract —— 记忆提取域（被编排的领域执行器）。

域内公共类型 ``ExtractionCore`` 经本包导出；其余模块走显式深路径导入
（extractor/ callers/ model/ utils/）。
"""

from .extract_core import ExtractionCore

__all__ = ['ExtractionCore']
