"""os_mem logger — 统一的日志输出（基于标准 logging）。

用法（os_mem 各模块）:
    from os_mem.logger import get_logger
    logger = get_logger("os_mem.stub")
    logger.info("...")
"""
import logging


class LoggerHelper:
    """日志辅助类，提供便捷的日志记录功能"""

    def __init__(self, name: str = "MemOs", level: int = logging.INFO):
        self.logger = logging.getLogger(name)
        self.logger.setLevel(level)

        # 如果处理器已存在则不重复添加
        if not self.logger.handlers:
            # 创建标准输出处理器（打印到终端）
            handler = logging.StreamHandler()
            formatter = logging.Formatter(
                '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
            )
            handler.setFormatter(formatter)
            self.logger.addHandler(handler)

    def debug(self, message: str):
        self.logger.debug(message)

    def info(self, message: str):
        self.logger.info(message)

    def warning(self, message: str):
        self.logger.warning(message)

    def error(self, message: str):
        self.logger.error(message)

    def critical(self, message: str):
        self.logger.critical(message)

# @classmethod
# def get_logger(cls, name: str = "MemOs") -> LoggerHelper:
#     """获取日志辅助实例"""
#     return LoggerHelper(name)
def get_logger(name: str = "os_mem") -> LoggerHelper:
    """获取 os_mem 命名空间下的日志辅助实例。"""
    return LoggerHelper(name)
