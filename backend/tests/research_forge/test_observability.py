"""Structured process logging must retain IDs without leaking credentials."""

from __future__ import annotations

import json
import logging

from research_forge.bootstrap.observability import ResearchForgeJsonFormatter


def test_json_formatter_redacts_secrets_and_keeps_only_known_context() -> None:
    logger = logging.getLogger("research_forge.test")
    record = logger.makeRecord(
        logger.name, logging.ERROR, __file__, 1, "Broker refused Bearer abc.def-123 password=plain-text", (), None,
        extra={"research_forge_context": {"attempt_id": "attempt-1", "token": "must-not-appear"}, "error_code": "SANDBOX_UNAVAILABLE"},
    )
    payload = json.loads(ResearchForgeJsonFormatter().format(record))
    assert payload["attempt_id"] == "attempt-1"
    assert payload["error_code"] == "SANDBOX_UNAVAILABLE"
    assert "abc.def-123" not in payload["message"]
    assert "plain-text" not in payload["message"]
    assert "token" not in payload
