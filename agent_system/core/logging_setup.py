"""Strukturiertes Logging (JSON-Lines) mit automatischer Secret-Schwaerzung.

Jeder Log-Eintrag traegt - sofern vorhanden - ``job_id``, ``step_id`` und
``agent``, damit sich ein kompletter Auftrag im Log nachverfolgen laesst.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from .secrets import redact

LOGGER_NAME = "agent_system"
_CONTEXT_FIELDS = ("job_id", "step_id", "agent", "event")


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        entry = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S"),
            "level": record.levelname,
            "logger": record.name,
            "msg": redact(record.getMessage()),
        }
        for f in _CONTEXT_FIELDS:
            value = getattr(record, f, None)
            if value is not None:
                entry[f] = value
        if record.exc_info:
            entry["exc"] = redact(self.formatException(record.exc_info))
        return json.dumps(entry, ensure_ascii=False)


class RedactingFilter(logging.Filter):
    """Schwaerzt Secrets auch fuer fremde (Nicht-JSON-)Handler."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = redact(record.getMessage())
        record.args = ()
        return True


def get_logger(name: str | None = None) -> logging.Logger:
    return logging.getLogger(f"{LOGGER_NAME}.{name}" if name else LOGGER_NAME)


def setup_logging(level: str = "INFO", log_file: Path | str | None = None, console: bool = False) -> logging.Logger:
    logger = logging.getLogger(LOGGER_NAME)
    logger.setLevel(level.upper())
    for h in list(logger.handlers):
        logger.removeHandler(h)
        h.close()
    formatter = JsonFormatter()
    if log_file:
        Path(log_file).parent.mkdir(parents=True, exist_ok=True)
        fh = logging.FileHandler(log_file, encoding="utf-8")
        fh.setFormatter(formatter)
        fh.addFilter(RedactingFilter())
        logger.addHandler(fh)
    if console:
        sh = logging.StreamHandler()
        sh.setFormatter(formatter)
        sh.addFilter(RedactingFilter())
        logger.addHandler(sh)
    if not logger.handlers:
        logger.addHandler(logging.NullHandler())
    logger.propagate = False
    return logger
