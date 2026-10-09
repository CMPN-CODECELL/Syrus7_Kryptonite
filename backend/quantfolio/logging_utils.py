"""Structured JSON logging with request/run correlation and secret redaction.

Every record is one JSON object per line. `request_id` and `run_id` come from
context variables set by the API middleware and the worker, so library code
does not need to pass them around. Known secret values (IBM token) and
token-shaped strings are redacted before anything is written.
"""

from __future__ import annotations

import contextvars
import json
import logging
import logging.handlers
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .settings import secret_values

request_id_var: contextvars.ContextVar[str | None] = contextvars.ContextVar("request_id", default=None)
run_id_var: contextvars.ContextVar[str | None] = contextvars.ContextVar("run_id", default=None)

REDACTED = "[REDACTED]"

# Patterns that look like credentials even when we don't know the exact value.
_SECRET_PATTERNS = [
    re.compile(r"(?i)(token|api[_-]?key|secret|password|authorization)(\s*[=:]\s*|\"\s*:\s*\")([^\s\"',;]{4,})"),
    re.compile(r"(?i)bearer\s+[A-Za-z0-9._\-]{8,}"),
]
# Windows/POSIX absolute paths are stripped from user-facing messages (not from logs).
_PATH_RE = re.compile(r"([A-Za-z]:\\[^\s\"']+|/(?:[\w.\-]+/){2,}[\w.\-]*)")


def redact(text: str) -> str:
    """Remove known secret values and credential-shaped substrings."""
    if not text:
        return text
    out = text
    for val in secret_values():
        out = out.replace(val, REDACTED)
    out = _SECRET_PATTERNS[0].sub(lambda m: f"{m.group(1)}{m.group(2)}{REDACTED}", out)
    out = _SECRET_PATTERNS[1].sub(f"Bearer {REDACTED}", out)
    return out


def safe_message(text: str, limit: int = 500) -> str:
    """Redacted, path-free, length-bounded message that is safe to return to clients."""
    out = redact(str(text))
    out = _PATH_RE.sub("<path>", out)
    out = out.replace("\r", " ").replace("\n", " ").strip()
    if len(out) > limit:
        out = out[: limit - 1] + "…"
    return out


def _redact_obj(obj: Any) -> Any:
    if isinstance(obj, str):
        return redact(obj)
    if isinstance(obj, dict):
        return {
            k: (REDACTED if isinstance(k, str) and re.search(r"(?i)token|secret|password|api[_-]?key", k) else _redact_obj(v))
            for k, v in obj.items()
        }
    if isinstance(obj, (list, tuple)):
        return [_redact_obj(v) for v in obj]
    return obj


_RESERVED = set(vars(logging.LogRecord("", 0, "", 0, "", (), None))) | {"message", "asctime"}


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        rid = request_id_var.get()
        if rid:
            payload["request_id"] = rid
        run = run_id_var.get()
        if run:
            payload["run_id"] = run
        for key, value in record.__dict__.items():
            if key not in _RESERVED and not key.startswith("_"):
                payload[key] = value
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        payload = _redact_obj(payload)
        return json.dumps(payload, default=str, ensure_ascii=False)


_configured = False


def configure_logging(level: str = "INFO", log_file: Path | None = None) -> None:
    """Idempotent root logger setup: JSON lines to stderr (+ optional file)."""
    global _configured
    root = logging.getLogger()
    if _configured:
        root.setLevel(level)
        return
    for h in list(root.handlers):
        root.removeHandler(h)
    fmt = JsonFormatter()
    sh = logging.StreamHandler(sys.stderr)
    sh.setFormatter(fmt)
    root.addHandler(sh)
    if log_file is not None:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        fh = logging.handlers.RotatingFileHandler(log_file, maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8", delay=True)
        fh.setFormatter(fmt)
        root.addHandler(fh)
    root.setLevel(level)
    # uvicorn's own access log duplicates our request log; keep its errors.
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    for name in ("qiskit", "stevedore", "urllib3", "yfinance", "peewee", "matplotlib"):
        logging.getLogger(name).setLevel(logging.WARNING)
    _configured = True
