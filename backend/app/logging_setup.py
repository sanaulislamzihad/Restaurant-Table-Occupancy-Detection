"""Logging: the console plus a rotating file, for our own logs and uvicorn's.

Everything goes through loguru. Uvicorn uses the standard ``logging`` module,
so its records are handed to loguru as well and end up in the same file.
"""

from __future__ import annotations

import inspect
import logging
import sys
from pathlib import Path
from typing import TYPE_CHECKING

from loguru import logger

if TYPE_CHECKING:
    from app.settings import Settings

LOG_FILE_NAME = "backend.log"


class InterceptHandler(logging.Handler):
    """Passes standard-library log records on to loguru."""

    def emit(self, record: logging.LogRecord) -> None:
        try:
            level: str | int = logger.level(record.levelname).name
        except ValueError:
            level = record.levelno
        # Point loguru at the code that logged, not at this handler or the logging module.
        frame, depth = inspect.currentframe(), 0
        while frame is not None:
            filename = frame.f_code.co_filename
            is_logging = filename == logging.__file__
            is_frozen = "importlib" in filename and "_bootstrap" in filename
            if depth > 0 and not (is_logging or is_frozen):
                break
            frame, depth = frame.f_back, depth + 1
        logger.opt(depth=depth, exception=record.exc_info).log(level, record.getMessage())


def setup_logging(settings: Settings) -> Path:
    """Log to the console and to LOG_DIR/backend.log; returns the log file's path."""
    log_file = settings.log_dir / LOG_FILE_NAME
    settings.log_dir.mkdir(parents=True, exist_ok=True)
    logger.remove()
    logger.add(sys.stderr, level=settings.log_level)
    logger.add(
        log_file,
        level=settings.log_level,
        rotation=f"{settings.log_rotation_mb} MB",
        retention=settings.log_retention_files,
        encoding="utf-8",
        enqueue=True,  # safe from every thread, never blocks on disk
        diagnose=False,  # no variable values (which could hold passwords) in logged tracebacks
    )
    logging.basicConfig(handlers=[InterceptHandler()], level=0, force=True)
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        uvicorn_logger = logging.getLogger(name)
        uvicorn_logger.handlers = []
        uvicorn_logger.propagate = True
    return log_file
