"""Transactional in-memory adapter used by tests and local deterministic demos."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from typing import Self

from research_forge.domain.execution import Operation
from research_forge.domain.mission import Attempt, AuditEvent, Mission, OutboxEvent, Task


@dataclass(slots=True)
class _State:
    missions: dict[str, Mission] = field(default_factory=dict)
    tasks: dict[str, Task] = field(default_factory=dict)
    attempts: dict[str, Attempt] = field(default_factory=dict)
    operations: dict[str, Operation] = field(default_factory=dict)
    operation_keys: dict[str, str] = field(default_factory=dict)
    audits: list[AuditEvent] = field(default_factory=list)
    outbox: list[OutboxEvent] = field(default_factory=list)


class InMemoryUnitOfWork:
    """A copy-on-write UoW preserving the production transaction boundary."""

    def __init__(self) -> None:
        self._state = _State()
        self._working: _State | None = None

    @property
    def audits(self) -> tuple[AuditEvent, ...]:
        return tuple(self._state.audits)

    @property
    def outbox(self) -> tuple[OutboxEvent, ...]:
        return tuple(self._state.outbox)

    def __enter__(self) -> Self:
        if self._working is not None:
            raise RuntimeError("Nested unit-of-work contexts are not supported.")
        self._working = deepcopy(self._state)
        return self

    def __exit__(self, exc_type: object, exc_value: object, traceback: object) -> bool | None:
        if exc_type is not None:
            self.rollback()
        elif self._working is not None:
            self.rollback()
            raise RuntimeError("Unit of work exited without an explicit commit.")
        return None

    def add_mission(self, mission: Mission) -> None:
        self._write().missions[str(mission.mission_id)] = mission

    def add_task(self, task: Task) -> None:
        self._write().tasks[str(task.task_id)] = task

    def add_attempt(self, attempt: Attempt) -> None:
        self._write().attempts[str(attempt.attempt_id)] = attempt

    def add_audit_event(self, event: AuditEvent) -> None:
        self._write().audits.append(event)

    def add_outbox_event(self, event: OutboxEvent) -> None:
        self._write().outbox.append(event)

    def add_operation(self, operation: Operation) -> None:
        state = self._write()
        operation_id = state.operation_keys.get(operation.idempotency_key)
        if operation_id is not None and operation_id != operation.operation_id:
            raise ValueError(f"Duplicate idempotency key: {operation.idempotency_key}")
        state.operations[operation.operation_id] = operation
        state.operation_keys[operation.idempotency_key] = operation.operation_id

    def get_mission(self, mission_id: str) -> Mission | None:
        return self._read().missions.get(mission_id)

    def get_task(self, task_id: str) -> Task | None:
        return self._read().tasks.get(task_id)

    def get_attempt(self, attempt_id: str) -> Attempt | None:
        return self._read().attempts.get(attempt_id)

    def get_operation_by_idempotency_key(self, idempotency_key: str) -> Operation | None:
        state = self._read()
        operation_id = state.operation_keys.get(idempotency_key)
        return state.operations.get(operation_id) if operation_id is not None else None

    def commit(self) -> None:
        if self._working is None:
            raise RuntimeError("No active unit-of-work context to commit.")
        self._state = self._working
        self._working = None

    def rollback(self) -> None:
        self._working = None

    def _read(self) -> _State:
        return self._working if self._working is not None else self._state

    def _write(self) -> _State:
        if self._working is None:
            raise RuntimeError("Writes require an active unit-of-work context.")
        return self._working
