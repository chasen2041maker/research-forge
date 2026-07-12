"""Deterministic in-process task transport for tests and local demos."""

from __future__ import annotations


class ImmediateQueue:
    """A transport fake that intentionally has no business-state persistence role."""

    def __init__(self) -> None:
        self._pending: list[str] = []
        self.acknowledged: list[str] = []

    def publish(self, attempt_id: str) -> None:
        self._pending.append(attempt_id)

    def receive(self) -> str | None:
        return self._pending[0] if self._pending else None

    def acknowledge(self, attempt_id: str) -> None:
        if not self._pending or self._pending[0] != attempt_id:
            raise ValueError("Queue acknowledgement must match the next delivered attempt ID.")
        self._pending.pop(0)
        self.acknowledged.append(attempt_id)
