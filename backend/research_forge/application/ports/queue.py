"""Transport-only task queue port; business truth remains in the Unit of Work."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol


class AttemptRoute(StrEnum):
    """The only two reviewed execution lanes; they must never share a worker stream."""

    BASELINE = "baseline"
    REPAIR = "repair"


@dataclass(frozen=True, slots=True)
class AttemptDelivery:
    """Transport receipt needed to acknowledge exactly one at-least-once delivery."""

    attempt_id: str
    route: AttemptRoute
    message_id: str


class TaskQueue(Protocol):
    """Carry versioned Attempt deliveries without becoming the Mission source of truth."""

    def publish(self, attempt_id: str, *, route: AttemptRoute) -> None: ...

    def receive(self, *, route: AttemptRoute, consumer_name: str) -> AttemptDelivery | None: ...

    def acknowledge(self, delivery: AttemptDelivery) -> None: ...
