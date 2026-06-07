"""structlog setup with sensitive-field redaction and a pipe-delimited format.

Output line format:  timestamp | level | component | event | key=value ...
Destinations: a rotating file (from config) plus stderr in development.
"""

from __future__ import annotations

import logging
import logging.handlers
import sys
from pathlib import Path
from typing import Any

import structlog

# Keys whose values must never be written to logs.
_SENSITIVE_KEYS = {
    "bot_token",
    "token",
    "password",
    "secret",
    "secrets",
    "credentials",
    "session",
    "text",
    "raw_text",
    "message",
    "body",
}
_REDACTED = "***REDACTED***"

_configured = False
# Literal secret values (e.g. the bot token) scrubbed from ALL log values,
# even when they appear embedded in a third-party error string or message.
_secrets: list[str] = []


def _scrub(value: Any) -> Any:
    if isinstance(value, str) and _secrets:
        for secret in _secrets:
            if secret and secret in value:
                value = value.replace(secret, _REDACTED)
    return value


def _redact(_: Any, __: str, event_dict: dict) -> dict:
    for key in list(event_dict):
        if key.lower() in _SENSITIVE_KEYS:
            event_dict[key] = _REDACTED
        else:
            event_dict[key] = _scrub(event_dict[key])
    return event_dict


def _pipe_renderer(_: Any, __: str, event_dict: dict) -> str:
    timestamp = event_dict.pop("timestamp", "")
    level = str(event_dict.pop("level", "")).upper()
    component = event_dict.pop("logger", event_dict.pop("component", "-"))
    event = event_dict.pop("event", "")
    head = f"{timestamp} | {level} | {component} | {event}"
    rest = " ".join(f"{k}={v}" for k, v in event_dict.items())
    return f"{head} | {rest}" if rest else head


def configure_logging(
    log_file: str | Path,
    log_max_bytes: int = 10_000_000,
    *,
    dev_stderr: bool = True,
    level: int = logging.INFO,
    secrets: list[str] | None = None,
) -> None:
    """Configure structlog + stdlib logging. Idempotent within a process.

    `secrets` are literal values scrubbed from every log line (e.g. the bot token).
    """
    global _configured
    if secrets:
        _secrets.extend(s for s in secrets if s)
    if _configured:
        return

    # Third-party loggers that would otherwise leak request URLs (with the token).
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)

    shared_processors = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso", utc=False),
        _redact,
    ]

    structlog.configure(
        processors=[
            *shared_processors,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    formatter = structlog.stdlib.ProcessorFormatter(
        processor=_pipe_renderer,
        foreign_pre_chain=shared_processors,
    )

    root = logging.getLogger()
    root.setLevel(level)
    root.handlers.clear()

    log_path = Path(log_file)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    file_handler = logging.handlers.RotatingFileHandler(
        log_path, maxBytes=log_max_bytes, backupCount=5, encoding="utf-8"
    )
    file_handler.setFormatter(formatter)
    root.addHandler(file_handler)

    if dev_stderr:
        stderr_handler = logging.StreamHandler(sys.stderr)
        stderr_handler.setFormatter(formatter)
        root.addHandler(stderr_handler)

    _configured = True


def get_logger(component: str) -> structlog.stdlib.BoundLogger:
    """Return a logger bound to a component name (used as the pipe 'component')."""
    return structlog.get_logger(component)
