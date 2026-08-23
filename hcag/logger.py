"""JSON-lines file logger per §2.11.3.

Always-on structured logging. Correlates with OTEL spans via trace_id when
tracing is enabled.
"""

from __future__ import annotations

import json
import logging
import logging.handlers
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import LogConfig

_LEVELS = {
    "DEBUG": logging.DEBUG,
    "INFO": logging.INFO,
    "WARN": logging.WARNING,
    "WARNING": logging.WARNING,
    "ERROR": logging.ERROR,
}


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname if record.levelname != "WARNING" else "WARN",
            "event": getattr(record, "event", record.getMessage()),
        }
        # Merge extra fields; skip stdlib LogRecord internals
        reserved = {
            "name", "msg", "args", "levelname", "levelno", "pathname", "filename",
            "module", "exc_info", "exc_text", "stack_info", "lineno", "funcName",
            "created", "msecs", "relativeCreated", "thread", "threadName",
            "processName", "process", "message", "event",
        }
        for k, v in record.__dict__.items():
            if k not in reserved and not k.startswith("_"):
                payload[k] = v
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str, ensure_ascii=False)


class HcagLogger:
    """Thin structured wrapper around a stdlib logger.

    Each call takes an `event` string and arbitrary keyword fields, mirroring
    the JSON-lines schema in §2.11.3.
    """

    def __init__(self, log: logging.Logger) -> None:
        self._log = log

    def _emit(self, level: int, event: str, **fields: Any) -> None:
        self._log.log(level, event, extra={"event": event, **fields})

    def debug(self, event: str, **fields: Any) -> None:
        self._emit(logging.DEBUG, event, **fields)

    def info(self, event: str, **fields: Any) -> None:
        self._emit(logging.INFO, event, **fields)

    def warn(self, event: str, **fields: Any) -> None:
        self._emit(logging.WARNING, event, **fields)

    def error(self, event: str, **fields: Any) -> None:
        self._emit(logging.ERROR, event, **fields)


def build_logger(cfg: LogConfig, name: str = "hcag") -> HcagLogger:
    logger = logging.getLogger(name)
    logger.setLevel(_LEVELS[cfg.level])
    # Avoid duplicate handlers on repeat initialization
    if not any(isinstance(h, logging.handlers.RotatingFileHandler) for h in logger.handlers):
        path = Path(cfg.file_path).expanduser().resolve()
        path.parent.mkdir(parents=True, exist_ok=True)
        handler = logging.handlers.RotatingFileHandler(
            path,
            maxBytes=cfg.rotation_size_mb * 1024 * 1024,
            backupCount=cfg.rotation_keep,
            encoding="utf-8",
        )
        handler.setFormatter(JsonFormatter())
        logger.addHandler(handler)
    logger.propagate = False
    return HcagLogger(logger)
