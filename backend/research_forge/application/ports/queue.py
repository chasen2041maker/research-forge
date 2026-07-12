"""Transport-only task queue port; business truth remains in the Unit of Work."""

from __future__ import annotations

from typing import Protocol


class TaskQueue(Protocol):
    """Carry Attempt IDs and acknowledgements without storing mission state."""

    def publish(self, attempt_id: str) -> None: ...

    def receive(self) -> str | None: ...

    def acknowledge(self, attempt_id: str) -> None: ...
