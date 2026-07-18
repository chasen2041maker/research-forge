"""A small, dependency-free event contract for inspectable Studio Agent runs.

Events are intentionally operational rather than conversational: they identify a run and step,
record the selected role/tool/outcome, and keep summaries bounded. Full prompts, source code,
secrets, and provider payloads do not belong in the Studio trace.
"""

from __future__ import annotations

from contextvars import ContextVar, Token
from datetime import UTC, datetime
from typing import Any, Callable


AgentEventCallback = Callable[[dict[str, Any]], None]

_callback: ContextVar[AgentEventCallback | None] = ContextVar(
    "co_scientist_agent_event_callback",
    default=None,
)
_run_id: ContextVar[str] = ContextVar("co_scientist_agent_event_run_id", default="")


def _summary(value: object, *, limit: int = 280) -> str:
    """Return a bounded, single-line human-readable summary for an event."""
    text = "" if value is None else str(value)
    text = " ".join(text.split())
    return text[:limit]


def set_agent_event_context(
    run_id: str,
    callback: AgentEventCallback | None,
) -> tuple[Token[str], Token[AgentEventCallback | None]]:
    """Bind event delivery to one synchronous pipeline invocation."""
    return _run_id.set(run_id), _callback.set(callback)


def reset_agent_event_context(tokens: tuple[Token[str], Token[AgentEventCallback | None]]) -> None:
    """Restore the caller's ContextVar values after the pipeline finishes."""
    run_token, callback_token = tokens
    _run_id.reset(run_token)
    _callback.reset(callback_token)


def emit_agent_event(
    event_type: str,
    *,
    step_id: str,
    agent_name: str,
    agent_role: str,
    model_role: str | None = None,
    input_summary: object = "",
    output_summary: object = "",
    tool_name: str | None = None,
    duration_ms: int | None = None,
    input_tokens: int | None = None,
    output_tokens: int | None = None,
    cost_usd: float | None = None,
    fallback: bool = False,
    parent_step_id: str | None = None,
    outcome: str = "SUCCEEDED",
    details: dict[str, Any] | None = None,
) -> None:
    """Emit one JSON-safe event if the current pipeline asked for observation.

    Missing provider telemetry remains ``None`` rather than a made-up zero. Event delivery is
    best-effort so that a UI observer can never break a research run.
    """
    callback = _callback.get()
    if callback is None:
        return

    event: dict[str, Any] = {
        "run_id": _run_id.get(),
        "step_id": step_id,
        "agent_name": agent_name,
        "agent_role": agent_role,
        "event_type": event_type,
        "model_role": model_role,
        "input_summary": _summary(input_summary),
        "output_summary": _summary(output_summary),
        "tool_name": tool_name,
        "duration_ms": duration_ms,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cost_usd": cost_usd,
        "fallback": fallback,
        "parent_step_id": parent_step_id,
        "outcome": outcome,
        "details": details or {},
        "occurred_at": datetime.now(UTC).isoformat(),
    }
    try:
        callback(event)
    except Exception:
        # The callback writes to a process-local view model; no observer failure should change
        # the result or error semantics of the actual graph.
        return
