"""An injected runner boundary so callers never need to import the legacy graph."""

from __future__ import annotations

from collections.abc import Callable, Mapping

from co_scientist.public_api.models import ExplorationSnapshot

ExplorationRunner = Callable[[str], Mapping[str, object]]


def run_exploration(
    *,
    question: str,
    run_id: str,
    runner: ExplorationRunner,
) -> ExplorationSnapshot:
    """Execute an injected Studio runner and expose only its completed snapshot."""
    if not question.strip():
        raise ValueError("question must not be blank")
    return ExplorationSnapshot.create(run_id=run_id, state=runner(question))
