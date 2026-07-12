"""Deterministic in-process task transport for tests and local demos."""

from __future__ import annotations

from research_forge.application.ports.queue import AttemptDelivery, AttemptRoute


class ImmediateQueue:
    """A transport fake that intentionally has no business-state persistence role."""

    def __init__(self) -> None:
        self._pending: dict[AttemptRoute, list[AttemptDelivery]] = {
            AttemptRoute.BASELINE: [],
            AttemptRoute.REPAIR: [],
        }
        self._sequence = 0
        self.acknowledged: list[str] = []

    def publish(self, attempt_id: str, *, route: AttemptRoute) -> None:
        if not attempt_id:
            raise ValueError("Attempt ID must not be blank.")
        self._sequence += 1
        self._pending[route].append(AttemptDelivery(attempt_id, route, f"immediate-{self._sequence}"))

    def receive(self, *, route: AttemptRoute, consumer_name: str) -> AttemptDelivery | None:
        if not consumer_name:
            raise ValueError("Queue consumer name must not be blank.")
        pending = self._pending[route]
        return pending[0] if pending else None

    def acknowledge(self, delivery: AttemptDelivery) -> None:
        pending = self._pending[delivery.route]
        if not pending or pending[0] != delivery:
            raise ValueError("Queue acknowledgement must match the next delivered receipt.")
        pending.pop(0)
        self.acknowledged.append(delivery.attempt_id)
