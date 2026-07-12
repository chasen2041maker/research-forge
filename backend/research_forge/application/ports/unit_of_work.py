"""Transactional persistence port for Mission creation."""

from __future__ import annotations

from typing import Protocol, Self

from research_forge.domain.mission import Attempt, AuditEvent, Mission, OutboxEvent, Task


class UnitOfWork(Protocol):
    """Persists business state, audit events, and outbox events atomically."""

    def __enter__(self) -> Self: ...

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: object | None,
    ) -> bool | None: ...

    def add_mission(self, mission: Mission) -> None: ...

    def add_task(self, task: Task) -> None: ...

    def add_attempt(self, attempt: Attempt) -> None: ...

    def add_audit_event(self, event: AuditEvent) -> None: ...

    def add_outbox_event(self, event: OutboxEvent) -> None: ...

    def commit(self) -> None: ...

    def rollback(self) -> None: ...
