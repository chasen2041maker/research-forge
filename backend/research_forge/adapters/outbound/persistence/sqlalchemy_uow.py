"""PostgreSQL/SQLAlchemy Unit of Work; schema creation is deliberately left to migrations."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import Self

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from research_forge.domain.artifact import ArtifactKind, ArtifactRef, ArtifactRegistration
from research_forge.domain.approval import Approval, ApprovalStatus
from research_forge.domain.errors import OptimisticLockConflict
from research_forge.domain.evidence import Claim, ClaimStatus, ClaimType, EvidenceLink, EvidenceType, MetricRecord
from research_forge.domain.execution import Operation, OperationStatus, OperationType
from research_forge.domain.mission import (
    Attempt,
    AttemptId,
    AttemptStatus,
    AuditEvent,
    Mission,
    MissionId,
    MissionStatus,
    OutboxEvent,
    Task,
    TaskId,
    TaskStatus,
    TaskType,
)
from research_forge.adapters.outbound.persistence.models import (
    ArtifactRow,
    ApprovalRow,
    AttemptRow,
    AuditEventRow,
    BundleRow,
    ClaimRow,
    EvidenceRow,
    MetricRow,
    MissionRow,
    OperationRow,
    OutboxEventRow,
    TaskRow,
)


class SqlAlchemyUnitOfWork:
    """Persist all mutable business facts transactionally in PostgreSQL through SQLAlchemy."""

    def __init__(self, session_factory: Callable[[], Session]) -> None:
        self._session_factory = session_factory
        self._session: Session | None = None
        self._committed = False
        self._missions: dict[str, Mission] = {}
        self._mission_versions: dict[str, int | None] = {}
        self._tasks: dict[str, Task] = {}
        self._attempts: dict[str, Attempt] = {}
        self._attempt_versions: dict[str, int | None] = {}
        self._operations: dict[str, Operation] = {}
        self._artifacts: dict[str, ArtifactRegistration] = {}
        self._metrics: dict[str, MetricRecord] = {}
        self._claims: dict[str, Claim] = {}
        self._evidence: dict[str, EvidenceLink] = {}
        self._audits: dict[str, AuditEvent] = {}
        self._outbox: dict[str, OutboxEvent] = {}
        self._bundles: dict[str, ArtifactRegistration] = {}
        self._approvals: dict[str, Approval] = {}

    def __enter__(self) -> Self:
        if self._session is not None:
            raise RuntimeError("Nested SQLAlchemy UoW contexts are not supported.")
        self._reset_buffers()
        self._session = self._session_factory()
        self._committed = False
        return self

    def __exit__(self, exc_type: object, exc_value: object, traceback: object) -> bool | None:
        session = self._require_session()
        try:
            if exc_type is not None or not self._committed:
                session.rollback()
        finally:
            session.close()
            self._session = None
        return None

    def add_mission(self, mission: Mission) -> None:
        self._require_session()
        self._missions[str(mission.mission_id)] = mission
        self._mission_versions.setdefault(str(mission.mission_id), None)

    def add_task(self, task: Task) -> None:
        self._require_session()
        self._tasks[str(task.task_id)] = task

    def add_attempt(self, attempt: Attempt) -> None:
        self._require_session()
        self._attempts[str(attempt.attempt_id)] = attempt
        self._attempt_versions.setdefault(str(attempt.attempt_id), None)

    def add_audit_event(self, event: AuditEvent) -> None:
        self._require_session()
        self._audits[event.event_id] = event

    def add_outbox_event(self, event: OutboxEvent) -> None:
        self._require_session()
        self._outbox[event.event_id] = event

    def add_operation(self, operation: Operation) -> None:
        self._require_session()
        self._operations[operation.operation_id] = operation

    def add_artifact(self, registration: ArtifactRegistration) -> None:
        self._require_session()
        self._artifacts[registration.operation_id] = registration

    def add_metric(self, metric: MetricRecord) -> None:
        self._require_session()
        self._metrics[str(metric.attempt_id)] = metric

    def add_claim(self, claim: Claim) -> None:
        self._require_session()
        self._claims[claim.claim_id] = claim

    def add_evidence_link(self, link: EvidenceLink) -> None:
        self._require_session()
        self._evidence[self._evidence_id(link)] = link

    def add_bundle(self, mission_id: str, artifact: ArtifactRegistration) -> None:
        self._require_session()
        self._bundles[mission_id] = artifact

    def add_approval(self, approval: Approval) -> None:
        self._require_session()
        self._approvals[approval.approval_id] = approval

    def get_mission(self, mission_id: str) -> Mission | None:
        if mission_id in self._missions:
            return self._missions[mission_id]
        row = self._require_session().get(MissionRow, mission_id)
        if row is None:
            return None
        mission = Mission(
            mission_id=MissionId(row.mission_id),
            spec_sha256=row.spec_sha256,
            normalized_spec_json=row.normalized_spec_json,
            created_at=row.created_at,
            status=MissionStatus(row.status),
            version=row.version,
        )
        self._missions[mission_id] = mission
        self._mission_versions[mission_id] = row.version
        return mission

    def get_task(self, task_id: str) -> Task | None:
        if task_id in self._tasks:
            return self._tasks[task_id]
        row = self._require_session().get(TaskRow, task_id)
        if row is None:
            return None
        task = Task(
            task_id=TaskId(row.task_id),
            mission_id=MissionId(row.mission_id),
            task_type=TaskType(row.task_type),
            created_at=row.created_at,
            status=TaskStatus(row.status),
        )
        self._tasks[task_id] = task
        return task

    def get_tasks_for_mission(self, mission_id: str) -> tuple[Task, ...]:
        task_ids = self._require_session().scalars(
            select(TaskRow.task_id).where(TaskRow.mission_id == mission_id).order_by(TaskRow.created_at)
        )
        return tuple(task for task_id in task_ids if (task := self.get_task(task_id)) is not None)

    def get_attempt(self, attempt_id: str) -> Attempt | None:
        if attempt_id in self._attempts:
            return self._attempts[attempt_id]
        row = self._require_session().get(AttemptRow, attempt_id)
        if row is None:
            return None
        attempt = Attempt(
            attempt_id=AttemptId(row.attempt_id),
            task_id=TaskId(row.task_id),
            attempt_number=row.attempt_number,
            lease_epoch=row.lease_epoch,
            created_at=row.created_at,
            status=AttemptStatus(row.status),
            lease_owner=row.lease_owner,
            lease_expires_at=row.lease_expires_at,
            heartbeat_at=row.heartbeat_at,
            version=row.version,
            failure_code=row.failure_code,
            resume_from_attempt_id=AttemptId(row.resume_from_attempt_id) if row.resume_from_attempt_id else None,
        )
        self._attempts[attempt_id] = attempt
        self._attempt_versions[attempt_id] = row.version
        return attempt

    def get_attempts_for_task(self, task_id: str) -> tuple[Attempt, ...]:
        attempt_ids = self._require_session().scalars(
            select(AttemptRow.attempt_id).where(AttemptRow.task_id == task_id).order_by(AttemptRow.attempt_number)
        )
        return tuple(attempt for attempt_id in attempt_ids if (attempt := self.get_attempt(attempt_id)) is not None)

    def get_operation_by_idempotency_key(self, idempotency_key: str) -> Operation | None:
        row = self._require_session().scalar(
            select(OperationRow).where(OperationRow.idempotency_key == idempotency_key)
        )
        if row is None:
            return None
        cached = self._operations.get(row.operation_id)
        if cached is not None:
            return cached
        operation = self._operation_from_row(row)
        self._operations[operation.operation_id] = operation
        return operation

    def get_artifact_by_operation_id(self, operation_id: str) -> ArtifactRegistration | None:
        cached = self._artifacts.get(operation_id)
        if cached is not None:
            return cached
        row = self._require_session().get(ArtifactRow, operation_id)
        if row is None:
            return None
        registration = self._artifact_from_row(row)
        self._artifacts[operation_id] = registration
        return registration

    def get_artifacts_for_attempt(self, attempt_id: str) -> tuple[ArtifactRegistration, ...]:
        rows = self._require_session().scalars(
            select(ArtifactRow).where(ArtifactRow.attempt_id == attempt_id).order_by(ArtifactRow.created_at)
        )
        artifacts = tuple(self._artifact_from_row(row) for row in rows)
        self._artifacts.update({artifact.operation_id: artifact for artifact in artifacts})
        return artifacts

    def get_metric_by_attempt_id(self, attempt_id: str) -> MetricRecord | None:
        cached = self._metrics.get(attempt_id)
        if cached is not None:
            return cached
        row = self._require_session().scalar(select(MetricRow).where(MetricRow.attempt_id == attempt_id))
        if row is None:
            return None
        metric = self._metric_from_row(row)
        self._metrics[attempt_id] = metric
        return metric

    def get_claims_for_mission(self, mission_id: str) -> tuple[Claim, ...]:
        rows = self._require_session().scalars(
            select(ClaimRow).where(ClaimRow.mission_id == mission_id).order_by(ClaimRow.claim_id)
        )
        claims: list[Claim] = []
        for row in rows:
            claim = self._claims.get(row.claim_id)
            if claim is None:
                claim = Claim(
                    claim_id=row.claim_id,
                    mission_id=MissionId(row.mission_id),
                    attempt_id=AttemptId(row.attempt_id),
                    claim_type=ClaimType(row.claim_type),
                    status=ClaimStatus(row.status),
                    statement=row.statement,
                    created_at=row.created_at,
                )
                self._claims[claim.claim_id] = claim
            claims.append(claim)
        return tuple(claims)

    def get_evidence_for_claim(self, claim_id: str) -> tuple[EvidenceLink, ...]:
        rows = self._require_session().scalars(
            select(EvidenceRow).where(EvidenceRow.claim_id == claim_id).order_by(EvidenceRow.evidence_id)
        )
        return tuple(
            EvidenceLink(
                claim_id=row.claim_id,
                evidence_type=EvidenceType(row.evidence_type),
                artifact=ArtifactRef(row.artifact_sha256, row.artifact_size_bytes, row.artifact_media_type),
            )
            for row in rows
        )

    def get_bundle(self, mission_id: str) -> ArtifactRegistration | None:
        cached = self._bundles.get(mission_id)
        if cached is not None:
            return cached
        row = self._require_session().get(BundleRow, mission_id)
        if row is None:
            return None
        bundle = ArtifactRegistration(
            artifact=ArtifactRef(row.sha256, row.size_bytes, row.media_type),
            kind=ArtifactKind.BUNDLE,
            attempt_id=AttemptId(row.attempt_id),
            operation_id=row.operation_id,
            created_at=row.created_at,
        )
        self._bundles[mission_id] = bundle
        return bundle

    def get_approval(self, approval_id: str) -> Approval | None:
        cached = self._approvals.get(approval_id)
        if cached is not None:
            return cached
        row = self._require_session().get(ApprovalRow, approval_id)
        if row is None:
            return None
        approval = Approval(
            approval_id=row.approval_id,
            mission_id=MissionId(row.mission_id),
            task_id=TaskId(row.task_id),
            attempt_id=AttemptId(row.attempt_id),
            action_type=row.action_type,
            action_hash=row.action_hash,
            risk_level=row.risk_level,
            scope=row.scope,
            requested_at=row.requested_at,
            expires_at=row.expires_at,
            status=ApprovalStatus(row.status),
            decided_at=row.decided_at,
            decided_by=row.decided_by,
        )
        self._approvals[approval_id] = approval
        return approval

    def get_approvals_for_mission(self, mission_id: str) -> tuple[Approval, ...]:
        approval_ids = self._require_session().scalars(
            select(ApprovalRow.approval_id)
            .where(ApprovalRow.mission_id == mission_id)
            .order_by(ApprovalRow.requested_at)
        )
        return tuple(
            approval
            for approval_id in approval_ids
            if (approval := self.get_approval(approval_id)) is not None
        )

    def get_unpublished_outbox_events(self, limit: int) -> tuple[OutboxEvent, ...]:
        if limit <= 0:
            raise ValueError("Outbox fetch limit must be positive.")
        rows = self._require_session().scalars(
            select(OutboxEventRow)
            .where(OutboxEventRow.published_at.is_(None))
            .order_by(OutboxEventRow.occurred_at)
            .limit(limit)
        )
        events = tuple(
            OutboxEvent(
                event_id=row.event_id,
                topic=row.topic,
                aggregate_id=row.aggregate_id,
                occurred_at=row.occurred_at,
                payload=dict(row.payload),
            )
            for row in rows
        )
        self._outbox.update({event.event_id: event for event in events})
        return events

    def mark_outbox_event_published(self, event_id: str, published_at: datetime) -> None:
        if self._require_session().get(OutboxEventRow, event_id) is None:
            raise ValueError(f"Outbox event not found: {event_id}")
        self._published_outbox_at.setdefault(event_id, published_at)

    def commit(self) -> None:
        session = self._require_session()
        self._flush_missions(session)
        self._flush_tasks(session)
        self._flush_attempts(session)
        for operation in self._operations.values():
            session.merge(self._operation_row(operation))
        for artifact in self._artifacts.values():
            session.merge(self._artifact_row(artifact))
        for metric in self._metrics.values():
            session.merge(self._metric_row(metric))
        for claim in self._claims.values():
            session.merge(self._claim_row(claim))
        for evidence in self._evidence.values():
            session.merge(self._evidence_row(evidence))
        for audit in self._audits.values():
            session.merge(self._audit_row(audit))
        for outbox in self._outbox.values():
            session.merge(self._outbox_row(outbox))
        for event_id, published_at in self._published_outbox_at.items():
            session.execute(
                update(OutboxEventRow)
                .where(OutboxEventRow.event_id == event_id, OutboxEventRow.published_at.is_(None))
                .values(published_at=published_at)
            )
        for mission_id, bundle in self._bundles.items():
            session.merge(self._bundle_row(mission_id, bundle))
        for approval in self._approvals.values():
            session.merge(self._approval_row(approval))
        session.commit()
        self._committed = True

    def rollback(self) -> None:
        self._require_session().rollback()

    def _flush_missions(self, session: Session) -> None:
        for mission_id, mission in self._missions.items():
            values = {
                "spec_sha256": mission.spec_sha256,
                "normalized_spec_json": mission.normalized_spec_json,
                "status": str(mission.status),
                "version": mission.version,
                "created_at": mission.created_at,
            }
            original_version = self._mission_versions[mission_id]
            if original_version is None:
                session.add(MissionRow(mission_id=mission_id, **values))
            else:
                result = session.execute(
                    update(MissionRow)
                    .where(MissionRow.mission_id == mission_id, MissionRow.version == original_version)
                    .values(**values)
                )
                if result.rowcount != 1:
                    raise OptimisticLockConflict(f"Mission {mission_id} was updated by another transaction.")

    def _flush_tasks(self, session: Session) -> None:
        for task in self._tasks.values():
            session.merge(
                TaskRow(
                    task_id=str(task.task_id),
                    mission_id=str(task.mission_id),
                    task_type=str(task.task_type),
                    status=str(task.status),
                    created_at=task.created_at,
                )
            )

    def _flush_attempts(self, session: Session) -> None:
        for attempt_id, attempt in self._attempts.items():
            values = {
                "task_id": str(attempt.task_id),
                "attempt_number": attempt.attempt_number,
                "status": str(attempt.status),
                "lease_owner": attempt.lease_owner,
                "lease_epoch": attempt.lease_epoch,
                "lease_expires_at": attempt.lease_expires_at,
                "heartbeat_at": attempt.heartbeat_at,
                "version": attempt.version,
                "failure_code": attempt.failure_code,
                "resume_from_attempt_id": str(attempt.resume_from_attempt_id) if attempt.resume_from_attempt_id else None,
                "created_at": attempt.created_at,
            }
            original_version = self._attempt_versions[attempt_id]
            if original_version is None:
                session.add(AttemptRow(attempt_id=attempt_id, **values))
            else:
                result = session.execute(
                    update(AttemptRow)
                    .where(AttemptRow.attempt_id == attempt_id, AttemptRow.version == original_version)
                    .values(**values)
                )
                if result.rowcount != 1:
                    raise OptimisticLockConflict(f"Attempt {attempt_id} lease/version is stale.")

    @staticmethod
    def _operation_from_row(row: OperationRow) -> Operation:
        return Operation(
            operation_id=row.operation_id,
            idempotency_key=row.idempotency_key,
            attempt_id=AttemptId(row.attempt_id),
            operation_type=OperationType(row.operation_type),
            input_hash=row.input_hash,
            lease_epoch=row.lease_epoch,
            target_ref_or_path=row.target_ref_or_path,
            created_at=row.created_at,
            updated_at=row.updated_at,
            expected_parent_sha=row.expected_parent_sha,
            external_result_ref=row.external_result_ref,
            error_code=row.error_code,
            status=OperationStatus(row.status),
        )

    @staticmethod
    def _artifact_from_row(row: ArtifactRow) -> ArtifactRegistration:
        return ArtifactRegistration(
            artifact=ArtifactRef(row.sha256, row.size_bytes, row.media_type),
            kind=ArtifactKind(row.kind),
            attempt_id=AttemptId(row.attempt_id),
            operation_id=row.operation_id,
            created_at=row.created_at,
        )

    @staticmethod
    def _metric_from_row(row: MetricRow) -> MetricRecord:
        return MetricRecord(
            metric_id=row.metric_id,
            attempt_id=AttemptId(row.attempt_id),
            artifact=ArtifactRef(row.artifact_sha256, row.artifact_size_bytes, row.artifact_media_type),
            json_pointer=row.json_pointer,
            value=row.value,
            comparator=row.comparator,
            expected_value=row.expected_value,
            tolerance=row.tolerance,
            unit=row.unit,
            commit_sha=row.commit_sha,
            command=tuple(row.command),
            environment_digest=row.environment_digest,
            dataset_sha256=row.dataset_sha256,
        )

    @staticmethod
    def _operation_row(operation: Operation) -> OperationRow:
        return OperationRow(
            operation_id=operation.operation_id,
            idempotency_key=operation.idempotency_key,
            attempt_id=str(operation.attempt_id),
            operation_type=str(operation.operation_type),
            input_hash=operation.input_hash,
            expected_parent_sha=operation.expected_parent_sha,
            target_ref_or_path=operation.target_ref_or_path,
            external_result_ref=operation.external_result_ref,
            lease_epoch=operation.lease_epoch,
            status=str(operation.status),
            error_code=operation.error_code,
            created_at=operation.created_at,
            updated_at=operation.updated_at,
        )

    @staticmethod
    def _artifact_row(registration: ArtifactRegistration) -> ArtifactRow:
        return ArtifactRow(
            operation_id=registration.operation_id,
            attempt_id=str(registration.attempt_id),
            kind=str(registration.kind),
            sha256=registration.artifact.sha256,
            size_bytes=registration.artifact.size_bytes,
            media_type=registration.artifact.media_type,
            created_at=registration.created_at,
        )

    @staticmethod
    def _metric_row(metric: MetricRecord) -> MetricRow:
        return MetricRow(
            metric_id=metric.metric_id,
            attempt_id=str(metric.attempt_id),
            artifact_sha256=metric.artifact.sha256,
            artifact_size_bytes=metric.artifact.size_bytes,
            artifact_media_type=metric.artifact.media_type,
            json_pointer=metric.json_pointer,
            value=metric.value,
            comparator=metric.comparator,
            expected_value=metric.expected_value,
            tolerance=metric.tolerance,
            unit=metric.unit,
            commit_sha=metric.commit_sha,
            command=list(metric.command),
            environment_digest=metric.environment_digest,
            dataset_sha256=metric.dataset_sha256,
        )

    @staticmethod
    def _claim_row(claim: Claim) -> ClaimRow:
        return ClaimRow(
            claim_id=claim.claim_id,
            mission_id=str(claim.mission_id),
            attempt_id=str(claim.attempt_id),
            claim_type=str(claim.claim_type),
            status=str(claim.status),
            statement=claim.statement,
            created_at=claim.created_at,
        )

    @staticmethod
    def _evidence_id(link: EvidenceLink) -> str:
        return f"{link.claim_id}:{link.evidence_type}:{link.artifact.sha256}"

    @classmethod
    def _evidence_row(cls, link: EvidenceLink) -> EvidenceRow:
        return EvidenceRow(
            evidence_id=cls._evidence_id(link),
            claim_id=link.claim_id,
            evidence_type=str(link.evidence_type),
            artifact_sha256=link.artifact.sha256,
            artifact_size_bytes=link.artifact.size_bytes,
            artifact_media_type=link.artifact.media_type,
        )

    @staticmethod
    def _audit_row(event: AuditEvent) -> AuditEventRow:
        return AuditEventRow(
            event_id=event.event_id,
            aggregate_type=event.aggregate_type,
            aggregate_id=event.aggregate_id,
            event_type=event.event_type,
            occurred_at=event.occurred_at,
            data=dict(event.data),
        )

    @staticmethod
    def _outbox_row(event: OutboxEvent) -> OutboxEventRow:
        return OutboxEventRow(
            event_id=event.event_id,
            topic=event.topic,
            aggregate_id=event.aggregate_id,
            occurred_at=event.occurred_at,
            payload=dict(event.payload),
            published_at=None,
        )

    @staticmethod
    def _bundle_row(mission_id: str, registration: ArtifactRegistration) -> BundleRow:
        return BundleRow(
            mission_id=mission_id,
            operation_id=registration.operation_id,
            attempt_id=str(registration.attempt_id),
            sha256=registration.artifact.sha256,
            size_bytes=registration.artifact.size_bytes,
            media_type=registration.artifact.media_type,
            created_at=registration.created_at,
        )

    @staticmethod
    def _approval_row(approval: Approval) -> ApprovalRow:
        return ApprovalRow(
            approval_id=approval.approval_id,
            mission_id=str(approval.mission_id),
            task_id=str(approval.task_id),
            attempt_id=str(approval.attempt_id),
            action_type=approval.action_type,
            action_hash=approval.action_hash,
            risk_level=approval.risk_level,
            scope=approval.scope,
            requested_at=approval.requested_at,
            expires_at=approval.expires_at,
            status=str(approval.status),
            decided_at=approval.decided_at,
            decided_by=approval.decided_by,
        )

    def _require_session(self) -> Session:
        if self._session is None:
            raise RuntimeError("SQLAlchemy UoW access requires an active context.")
        return self._session

    def _reset_buffers(self) -> None:
        self._missions = {}
        self._mission_versions = {}
        self._tasks = {}
        self._attempts = {}
        self._attempt_versions = {}
        self._operations = {}
        self._artifacts = {}
        self._metrics = {}
        self._claims = {}
        self._evidence = {}
        self._audits = {}
        self._outbox = {}
        self._published_outbox_at = {}
        self._bundles = {}
        self._approvals = {}
