"""Structured JSON logging configuration for synctx services."""

from __future__ import annotations

import logging
import os
import re

from pythonjsonlogger.json import JsonFormatter


_SECRET_PATTERN = re.compile(
    r"(token|password|secret|api_key|credential|auth)[=:]\s*\S+",
    re.IGNORECASE,
)
_RENDER_TOKEN_PATTERN = re.compile(r'''(/(?:render|open)/)[^/\s?"'<>\\]+''')


def _mask_render_tokens(value: str) -> str:
    return _RENDER_TOKEN_PATTERN.sub(r"\1***", value)


class RenderTokenFormatter(logging.Formatter):
    def __init__(self, formatter):
        super().__init__()
        self.formatter = formatter

    def format(self, record):
        return _mask_render_tokens(self.formatter.format(record))


class SynctxJsonFormatter(JsonFormatter):
    """JSON formatter with service context and secret masking."""

    def __init__(self, service_name: str = "", **kwargs):
        super().__init__(
            fmt="%(timestamp)s %(levelname)s %(name)s %(message)s",
            timestamp=True,
            **kwargs,
        )
        self.service_name = service_name

    def add_fields(self, log_record, record, message_dict):
        super().add_fields(log_record, record, message_dict)
        log_record["service"] = self.service_name
        log_record["level"] = record.levelname
        if hasattr(record, "request_id"):
            log_record["request_id"] = record.request_id
        if hasattr(record, "repo_id"):
            log_record["repo_id"] = record.repo_id
        if hasattr(record, "run_id"):
            log_record["run_id"] = record.run_id
        if hasattr(record, "duration_ms"):
            log_record["duration_ms"] = record.duration_ms

    def serialize_log_record(self, log_record):
        for key, value in log_record.items():
            if isinstance(value, str):
                log_record[key] = _SECRET_PATTERN.sub(r"\1=***", value)
        return _mask_render_tokens(super().serialize_log_record(log_record))


def configure_logging(service_name: str = "", level: str | None = None) -> None:
    """Configure JSON structured logging for a synctx service.

    In development (SYNCTX_LOG_FORMAT=text), uses plain text for readability.
    """
    log_level = level or os.getenv("SYNCTX_LOG_LEVEL", "INFO")
    log_format = os.getenv("SYNCTX_LOG_FORMAT", "json")

    root = logging.getLogger()
    root.setLevel(log_level.upper())

    if root.handlers:
        root.handlers.clear()

    handler = logging.StreamHandler()

    if log_format == "text":
        handler.setFormatter(
            RenderTokenFormatter(logging.Formatter(f"%(asctime)s [{service_name}] %(levelname)s %(name)s: %(message)s"))
        )
    else:
        handler.setFormatter(SynctxJsonFormatter(service_name=service_name))

    root.addHandler(handler)

    for name in ("uvicorn", "uvicorn.access", "uvicorn.error"):
        for access_handler in logging.getLogger(name).handlers:
            formatter = access_handler.formatter or logging.Formatter()
            if not isinstance(formatter, RenderTokenFormatter):
                access_handler.setFormatter(RenderTokenFormatter(formatter))

    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)
