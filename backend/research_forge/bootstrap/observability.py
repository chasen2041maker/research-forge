"""Structured, redacted process logging for the shipped VS-001 host roles."""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Mapping
from datetime import datetime, timezone


CONTEXT_FIELDS = frozenset({"request_id", "mission_id", "task_id", "attempt_id", "operation_id", "agent_run_id", "trace_id"})
SECRET_PATTERNS = (
    (re.compile(r"(?i)\b(bearer\s+)[a-z0-9._-]+"), r"\1[REDACTED]"),
    (re.compile(r"(?i)\b(password|secret|token)=([^\s,&]+)"), r"\1=[REDACTED]"),
)


class ResearchForgeJsonFormatter(logging.Formatter):
    """Emit bounded correlation IDs and redact common credential forms."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "timestamp": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": _redact(record.getMessage()),
        }
        context = getattr(record, "research_forge_context", None)
        if isinstance(context, Mapping):
            payload.update({key: _redact(str(value)) for key, value in context.items() if key in CONTEXT_FIELDS and value is not None})
        error_code = getattr(record, "error_code", None)
        if isinstance(error_code, str) and error_code:
            payload["error_code"] = error_code
        if record.exc_info is not None and record.exc_info[0] is not None:
            payload["exception_type"] = record.exc_info[0].__name__
        return json.dumps(payload, ensure_ascii=False, sort_keys=True)


def configure_json_logging(level: str) -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(ResearchForgeJsonFormatter())
    logging.basicConfig(level=level, handlers=[handler], force=True)


def _redact(value: str) -> str:
    for pattern, replacement in SECRET_PATTERNS:
        value = pattern.sub(replacement, value)
    return value
