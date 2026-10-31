"""日志：文件日志（便于排查识别失败）+ 内存缓冲区（供 UI 实时显示）。"""

from __future__ import annotations

import logging
import sys
from collections import deque
from datetime import datetime
from logging.handlers import RotatingFileHandler
from typing import Callable, Deque

from .paths import log_dir

LOG_FORMAT = "%(asctime)s %(levelname)-7s %(name)s | %(message)s"
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

_buffer: Deque[tuple[str, str]] = deque(maxlen=2000)
_listeners: list[Callable[[str, str], None]] = []


class QtSignalHandler(logging.Handler):
    """把日志同时推送到 UI（通过回调，避免 core 层依赖 Qt）。"""

    def emit(self, record: logging.LogRecord) -> None:
        try:
            message = record.getMessage()
        except Exception:  # pragma: no cover - 格式化异常不应影响主流程
            message = str(record.msg)
        level = record.levelname
        _buffer.append((level, message))
        for callback in list(_listeners):
            try:
                callback(level, message)
            except Exception:  # pragma: no cover
                pass


def add_listener(callback: Callable[[str, str], None]) -> None:
    if callback not in _listeners:
        _listeners.append(callback)


def remove_listener(callback: Callable[[str, str], None]) -> None:
    if callback in _listeners:
        _listeners.remove(callback)


def recent(limit: int = 300) -> list[tuple[str, str]]:
    return list(_buffer)[-limit:]


def log_file_path() -> str:
    return str(log_dir() / f"seqaudio_{datetime.now():%Y%m%d}.log")


def setup_logging(level: int = logging.INFO, to_console: bool = True) -> None:
    root = logging.getLogger()
    if getattr(root, "_seqaudio_configured", False):
        return
    root.setLevel(level)
    formatter = logging.Formatter(LOG_FORMAT, DATE_FORMAT)

    try:
        file_handler = RotatingFileHandler(
            log_file_path(), maxBytes=4 * 1024 * 1024, backupCount=3, encoding="utf-8"
        )
        file_handler.setFormatter(formatter)
        root.addHandler(file_handler)
    except OSError:
        pass

    if to_console and sys.stderr is not None:
        stream = logging.StreamHandler(sys.stderr)
        stream.setFormatter(formatter)
        root.addHandler(stream)

    ui_handler = QtSignalHandler()
    root.addHandler(ui_handler)

    # 第三方库降噪
    for noisy in ("httpx", "httpcore", "urllib3"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    root._seqaudio_configured = True  # type: ignore[attr-defined]


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
