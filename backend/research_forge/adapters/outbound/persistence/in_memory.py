"""Transactional in-memory adapter used by tests and local deterministic demos."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime
from typing import Self

from research_forge.domain.artifact import ArtifactRegistration
from research_forge.domain.approval import Approval
from research_forge.domain.execution import Operation, OperationStatus
from research_forge.domain.evidence import Claim, EvidenceLink, MetricRecord
from research_forge.domain.mission import Attempt, AuditEvent, Mission, OutboxEvent, Task


@dataclass(slots=True)
class _State:
    missions: dict[str, Mission] = field(default_factory=dict)
    tasks: dict[str, Task] = field(default_factory=dict)
    attempts: dict[str, Attempt] = field(default_factory=dict)
    operations: dict[str, Operation] = field(default_factory=dict)
    operation_keys: dict[str, str] = field(default_factory=dict)
    artifacts_by_operation: dict[str, ArtifactRegistration] = field(default_factory=dict)
    metrics_by_attempt: dict[str, MetricRecord] = field(default_factory=dict)
    claims: dict[str, Claim] = field(default_factory=dict)
    evidence_by_claim: dict[str, list[EvidenceLink]] = field(default_factory=dict)
    bundles_by_mission: dict[str, ArtifactRegistration] = field(default_factory=dict)
    approvals: dict[str, Approval] = field(default_factory=dict)
    audits: list[AuditEvent] = field(default_factory=list)
    outbox: list[OutboxEvent] = field(default_factory=list)
    published_outbox_at: dict[str, datetime] = field(default_factory=dict)


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

    @property
    def artifacts(self) -> tuple[ArtifactRegistration, ...]:
        return tuple(self._state.artifacts_by_operation.values())

    @property
    def metrics(self) -> tuple[MetricRecord, ...]:
        return tuple(self._state.metrics_by_attempt.values())

    @property
    def bundles(self) -> tuple[ArtifactRegistration, ...]:
        return tuple(self._state.bundles_by_mission.values())

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

    def add_artifact(self, registration: ArtifactRegistration) -> None:
        state = self._write()
        existing = state.artifacts_by_operation.get(registration.operation_id)
        if existing is not None and existing != registration:
            raise ValueError(f"Conflicting artifact registration for operation {registration.operation_id}")
        state.artifacts_by_operation[registration.operation_id] = registration

    def add_metric(self, metric: MetricRecord) -> None:
        state = self._write()
        attempt_id = str(metric.attempt_id)
        existing = state.metrics_by_attempt.get(attempt_id)
        if existing is not None and existing != metric:
            raise ValueError(f"Conflicting metric registration for attempt {attempt_id}")
        state.metrics_by_attempt[attempt_id] = metric

    def add_claim(self, claim: Claim) -> None:
        state = self._write()
        existing = state.claims.get(claim.claim_id)
        if existing is not None and existing != claim:
            raise ValueError(f"Conflicting claim registration: {claim.claim_id}")
        state.claims[claim.claim_id] = claim

    def add_evidence_link(self, link: EvidenceLink) -> None:
        state = self._write()
        links = state.evidence_by_claim.setdefault(link.claim_id, [])
        if link not in links:
            links.append(link)

    def add_bundle(self, mission_id: str, artifact: ArtifactRegistration) -> None:
        state = self._write()
        existing = state.bundles_by_mission.get(mission_id)
        if existing is not None and existing != artifact:
            raise ValueError(f"Conflicting bundle registration for mission {mission_id}")
        state.bundles_by_mission[mission_id] = artifact

    def add_approval(self, approval: Approval) -> None:
        state = self._write()
        existing = state.approvals.get(approval.approval_id)
        if existing is not None and existing != approval:
            raise ValueError(f"Conflicting approval registration: {approval.approval_id}")
        state.approvals[approval.approval_id] = approval

    def get_mission(self, mission_id: str) -> Mission | None:
        return self._read().missions.get(mission_id)

    def get_task(self, task_id: str) -> Task | None:
        return self._read().tasks.get(task_id)

    def get_tasks_for_mission(self, mission_id: str) -> tuple[Task, ...]:
        return tuple(task for task in self._read().tasks.values() if str(task.mission_id) == mission_id)

    def get_attempt(self, attempt_id: str) -> Attempt | None:
        return self._read().attempts.get(attempt_id)

    def get_attempts_for_task(self, task_id: str) -> tuple[Attempt, ...]:
        return tuple(attempt for attempt in self._read().attempts.values() if str(attempt.task_id) == task_id)

    def get_operation_by_idempotency_key(self, idempotency_key: str) -> Operation | None:
        state = self._read()
        operation_id = state.operation_keys.get(idempotency_key)
        return state.operations.get(operation_id) if operation_id is not None else None

    def get_stale_operations(
        self, *, updated_before: datetime, statuses: tuple[OperationStatus, ...], limit: int
    ) -> tuple[Operation, ...]:
        if limit <= 0:
            raise ValueError("Stale-operation limit must be positive.")
        return tuple(
            operation
            for operation in sorted(self._read().operations.values(), key=lambda item: (item.updated_at, item.operation_id))
            if operation.status in statuses and operation.updated_at <= updated_before
        )[:limit]

    def get_artifact_by_operation_id(self, operation_id: str) -> ArtifactRegistration | None:
        return self._read().artifacts_by_operation.get(operation_id)

    def get_artifacts_for_attempt(self, attempt_id: str) -> tuple[ArtifactRegistration, ...]:
        return tuple(
            artifact
            for artifact in self._read().artifacts_by_operation.values()
            if str(artifact.attempt_id) == attempt_id
        )

    def get_metric_by_attempt_id(self, attempt_id: str) -> MetricRecord | None:
        return self._read().metrics_by_attempt.get(attempt_id)

    def get_claims_for_mission(self, mission_id: str) -> tuple[Claim, ...]:
        return tuple(claim for claim in self._read().claims.values() if str(claim.mission_id) == mission_id)

    def get_evidence_for_claim(self, claim_id: str) -> tuple[EvidenceLink, ...]:
        return tuple(self._read().evidence_by_claim.get(claim_id, []))

    def get_bundle(self, mission_id: str) -> ArtifactRegistration | None:
        return self._read().bundles_by_mission.get(mission_id)

    def get_approval(self, approval_id: str) -> Approval | None:
        return self._read().approvals.get(approval_id)

    def get_unpublished_outbox_events(self, limit: int) -> tuple[OutboxEvent, ...]:
        if limit <= 0:
            raise ValueError("Outbox fetch limit must be positive.")
        state = self._read()
        return tuple(event for event in state.outbox if event.event_id not in state.published_outbox_at)[:limit]

    def mark_outbox_event_published(self, event_id: str, published_at: datetime) -> None:
        state = self._write()
        if not any(event.event_id == event_id for event in state.outbox):
            raise ValueError(f"Outbox event not found: {event_id}")
        state.published_outbox_at.setdefault(event_id, published_at)

    def get_approvals_for_mission(self, mission_id: str) -> tuple[Approval, ...]:
        return tuple(
            approval
            for approval in self._read().approvals.values()
            if str(approval.mission_id) == mission_id
        )

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
