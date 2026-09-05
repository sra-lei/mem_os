"""os_mem logger — 基于 loguru 的统一日志入口。

双 sink 架构（兼顾开发体验与采集标准）：
  - 控制台：人类可读彩色格式，便于本地调试
  - 文件  ：JSON Lines（logs/app.jsonl），便于 Filebeat / Fluentd / Loki 采集

接口保持兼容（调用方零改动）：
    from os_mem.infra.logger import get_logger
    logger = get_logger("os_mem.storage")
    logger.info("...")
    logger.error("...")
"""
from __future__ import annotations

import json
import os
import sys
from datetime import timezone
from pathlib import Path
from typing import Any

from loguru import logger as _loguru_root

# ------------------------------------------------------------------ #
#  常量
# ------------------------------------------------------------------ #
LOG_DIR: Path = Path(os.environ.get("MEMOS_LOG_DIR", "logs")).resolve()
LOG_FILE: Path = LOG_DIR / "app.jsonl"

# ------------------------------------------------------------------ #
#  JSONL 采集格式 — 通过自定义 sink（可调用对象）写入文件
#  避开 loguru 的「add() 对 file sink 做特殊处理」导致的参数不兼容问题。
# ------------------------------------------------------------------ #
class _JSONLSink:
    """JSON Lines sink：每条日志一行 JSON，采集标准格式。

    使用 callable sink 而不是 format+patcher，因为文件路径走 FileSink
    分支时 open() kwargs 不接受 patcher。Callable sink 由 loguru 直接回调，
    所有写控制都在自己手里。

    轮转/保留/压缩通过 loguru 官方 logger.add() 内部调用路径支持；
    但 callable sink 不支持 rotation/retention/compression，因此这里采用
    轻量策略：按天在文件名里加日期后缀（app-YYYY-MM-DD.jsonl），
    超过 14 天的老文件在 init 阶段清理一次。
    """

    def __init__(self, log_dir: Path, base_name: str = "app", retention_days: int = 14) -> None:
        self._log_dir = log_dir
        self._base_name = base_name
        self._retention_days = retention_days
        self._log_dir.mkdir(parents=True, exist_ok=True)
        self._current_date: str = ""
        self._fp = None
        self._cleanup_old()

    # ----- 轮转+清理 -----
    def _today(self) -> str:
        from datetime import datetime, timezone
        return datetime.now(timezone.utc).strftime("%Y-%m-%d")

    def _ensure_file(self) -> None:
        today = self._today()
        if self._fp is not None and self._current_date == today:
            return
        if self._fp is not None:
            try:
                self._fp.close()
            except Exception:
                pass
        self._current_date = today
        path = self._log_dir / f"{self._base_name}-{today}.jsonl"
        self._fp = open(path, "a", encoding="utf-8", buffering=1)

    def _cleanup_old(self) -> None:
        import glob
        import time
        cutoff = time.time() - self._retention_days * 86400
        for path in glob.glob(str(self._log_dir / f"{self._base_name}-*.jsonl*")):
            try:
                if os.path.getmtime(path) < cutoff:
                    os.remove(path)
            except OSError:
                pass

    # ----- loguru callable sink 入口 -----
    def __call__(self, message: Any) -> None:
        """`message` 是 loguru 的 Message 对象（带 record 属性）。"""
        record: dict[str, Any] = message.record
        payload: dict[str, Any] = {
            # UTC 时间，带 Z 后缀（采集端无需猜时区）
            "timestamp": (
                record["time"]
                .astimezone(timezone.utc)
                .isoformat(timespec="milliseconds")
                .replace("+00:00", "Z")
            ),
            "level": record["level"].name,
            "module": str(record["extra"].get("module", "os_mem")),
            "message": str(record["message"]),
            "pid": record["process"].id,
            "thread": record["thread"].name,
        }
        # exception 优先级：
        #   1) LoggerHelper.exception() 通过 bind(exception_obj=...) 传入的字典
        #   2) loguru record.exception 自动捕获的 (type, value, tb)
        exc = None
        bound = record["extra"].get("exception_obj")
        if isinstance(bound, dict):
            exc = bound
        else:
            record_exc = record.get("exception")
            if record_exc is not None and record_exc[0] is not None:
                exc = {
                    "type": record_exc[0].__name__,
                    "value": str(record_exc[1]),
                }
        if exc is not None:
            payload["exception"] = exc
        self._ensure_file()
        if self._fp is not None:
            self._fp.write(
                json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n"
            )


# ------------------------------------------------------------------ #
#  loguru 全局配置 — 只在首次 import 时执行一次
# ------------------------------------------------------------------ #
def _setup_loguru() -> None:
    _loguru_root.remove()

    # --- 控制台 sink：人类可读彩色 ---
    _loguru_root.add(
        sys.stderr,
        level="INFO",
        format=(
            "<green>{time:YYYY-MM-DD HH:mm:ss.SSS}</green> | "
            "<level>{level: <8}</level> | "
            "<cyan>{extra[module]}</cyan> | "
            "<level>{message}</level>"
        ),
        colorize=True,
        backtrace=False,
        diagnose=False,
        enqueue=True,
    )

    # --- 文件 sink：JSON Lines（用 callable sink） ---
    _loguru_root.add(
        _JSONLSink(LOG_DIR),
        level="DEBUG",
        enqueue=True,
        backtrace=False,
        diagnose=False,
    )


_setup_loguru()


# ------------------------------------------------------------------ #
#  对外兼容 API — 和原 LoggerHelper 一致
# ------------------------------------------------------------------ #
class LoggerHelper:
    """兼容 wrapper：绑定 module 名到 loguru 的 bound logger。

    保持原接口（debug/info/warning/error/critical），调用方零改动。
    """

    def __init__(self, name: str = "os_mem", level: int | None = None) -> None:
        self._module = name
        self._logger = _loguru_root.bind(module=name)

    # ---- 兼容接口 ----
    def debug(self, message: str, *args: Any, **kwargs: Any) -> None:
        self._logger.debug(message, *args, **kwargs)

    def info(self, message: str, *args: Any, **kwargs: Any) -> None:
        self._logger.info(message, *args, **kwargs)

    def warning(self, message: str, *args: Any, **kwargs: Any) -> None:
        self._logger.warning(message, *args, **kwargs)

    def error(self, message: str, *args: Any, **kwargs: Any) -> None:
        self._logger.error(message, *args, **kwargs)

    def critical(self, message: str, *args: Any, **kwargs: Any) -> None:
        self._logger.critical(message, *args, **kwargs)

    # ---- 额外：异常上下文（控制台不带栈，JSONL 含 exception 字段） ----
    def exception(self, message: str, *args: Any, **kwargs: Any) -> None:
        """在 except 块中调用：JSONL 会带上 exception 字段（type/value），
        控制台只输出一行 ERROR，避免刷屏。"""
        import sys
        exc_type, exc_val, _tb = sys.exc_info()
        exc_info = None
        if exc_type is not None:
            exc_info = {"type": exc_type.__name__, "value": str(exc_val)}
        self._logger.bind(exception_obj=exc_info).error(message, *args, **kwargs)


def get_logger(name: str = "os_mem") -> LoggerHelper:
    """获取指定模块名的 logger 实例（loguru bound logger 包装）。"""
    return LoggerHelper(name)
