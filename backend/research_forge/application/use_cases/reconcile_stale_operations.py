"""Durably requeue stale cross-store effects through the existing Attempt delivery path."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

from research_forge.application.ports.system import Clock, IdGenerator
from research_forge.application.ports.unit_of_work import UnitOfWork
from research_forge.domain.execution import OperationStatus
from research_forge.domain.mission import AuditEvent, OutboxEvent, TaskType


@dataclass(frozen=True, slots=True)
class ReconciliationView:
    operation_ids: tuple[str, ...]


class ReconcileStaleOperations:
    """Request one idempotent redelivery for stale PREPARED or EXECUTING operations."""

    def __init__(self, *, unit_of_work: UnitOfWork, clock: Clock, id_generator: IdGenerator, stale_after: timedelta) -> None:
        if stale_after <= timedelta(0):
            raise ValueError("Stale-operation interval must be positive.")
        self._unit_of_work = unit_of_work
        self._clock = clock
        self._id_generator = id_generator
        self._stale_after = stale_after

    def execute(self, *, limit: int = 100) -> ReconciliationView:
        now = self._clock.now()
        with self._unit_of_work:
            operations = self._unit_of_work.get_stale_operations(
                updated_before=now - self._stale_after,
                statuses=(OperationStatus.PREPARED, OperationStatus.EXECUTING),
                limit=limit,
            )
            for operation in operations:
                operation.request_recovery(now)
                attempt_id = str(operation.attempt_id)
                attempt = self._unit_of_work.get_attempt(attempt_id)
                if attempt is None:
                    raise RuntimeError(f"Operation {operation.operation_id} references a missing Attempt.")
                task = self._unit_of_work.get_task(str(attempt.task_id))
                if task is None:
                    raise RuntimeError(f"Operation {operation.operation_id} references a missing Task.")
                topic = "baseline_attempt.ready" if task.task_type is TaskType.BASELINE_REPRODUCTION else "repair_attempt.ready"
                self._unit_of_work.add_audit_event(AuditEvent(
                    event_id=self._id_generator.new("audit"), aggregate_type="operation", aggregate_id=operation.operation_id,
                    event_type="operation.recovery_requested", occurred_at=now,
                    data={"attempt_id": attempt_id, "idempotency_key": operation.idempotency_key},
                ))
                self._unit_of_work.add_outbox_event(OutboxEvent(
                    event_id=self._id_generator.new("outbox"), topic=topic, aggregate_id=attempt_id,
                    occurred_at=now, payload={"attempt_id": attempt_id, "operation_id": operation.operation_id},
                ))
            self._unit_of_work.commit()
        return ReconciliationView(tuple(operation.operation_id for operation in operations))
