"""Public data shapes for Studio consumers; no LangGraph state types leak through here."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ExplorationSnapshot:
    """A completed Studio run represented as an opaque JSON-compatible snapshot."""

    run_id: str
    state: Mapping[str, object]

    @classmethod
    def create(cls, *, run_id: str, state: Mapping[str, object]) -> ExplorationSnapshot:
        if not run_id.strip():
            raise ValueError("run_id must not be blank")
        if not isinstance(state, Mapping):
            raise ValueError("state must be a mapping")
        return cls(run_id=run_id, state=dict(state))
