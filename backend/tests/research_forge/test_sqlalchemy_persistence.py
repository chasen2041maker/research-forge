"""SQLAlchemy adapter contract tests; production schema setup remains migration-owned."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from sqlite3 import Connection as SqliteConnection

import pytest
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import Session, sessionmaker

from research_forge.adapters.outbound.persistence import SqlAlchemyUnitOfWork
from research_forge.adapters.outbound.persistence.models import ApprovalRow, AuditEventRow, Base, OutboxEventRow
from research_forge.domain.approval import Approval, ApprovalStatus
from research_forge.domain.artifact import ArtifactRef
from research_forge.domain.errors import OptimisticLockConflict
from research_forge.domain.execution import Operation, OperationStatus, OperationType
from research_forge.domain.mission import (
    Attempt,
    AttemptId,
    AuditEvent,
    Mission,
    MissionId,
    OutboxEvent,
    Task,
    TaskId,
    TaskType,
)


def _session_factory() -> sessionmaker[Session]:
    engine = create_engine("sqlite+pysqlite:///:memory:")

    @event.listens_for(engine, "connect")
    def enable_foreign_keys(connection: SqliteConnection, record: object) -> None:
        del record
        connection.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)


def _seed(unit_of_work: SqlAlchemyUnitOfWork, now: datetime) -> None:
    mission = Mission.create(
        mission_id=MissionId("mission-1"),
        spec_sha256="a" * 64,
        normalized_spec_json="{}",
        created_at=now,
    )
    mission.mark_ready()
    task = Task(TaskId("task-1"), mission.mission_id, TaskType.BASELINE_REPRODUCTION, now)
    attempt = Attempt(AttemptId("attempt-1"), task.task_id, 1, 0, now)
    with unit_of_work:
        unit_of_work.add_mission(mission)
        unit_of_work.add_task(task)
        unit_of_work.add_attempt(attempt)
        unit_of_work.add_audit_event(
            AuditEvent("audit-1", "mission", "mission-1", "mission.created", now, {"source": "test"})
        )
        unit_of_work.add_outbox_event(
            OutboxEvent("outbox-1", "baseline_attempt.ready", "mission-1", now, {"attempt_id": "attempt-1"})
        )
        unit_of_work.commit()


def test_sqlalchemy_uow_persists_state_audit_and_outbox_atomically() -> None:
    session_factory = _session_factory()
    unit_of_work = SqlAlchemyUnitOfWork(session_factory)
    now = datetime(2026, 7, 12, tzinfo=timezone.utc)
    _seed(unit_of_work, now)

    with unit_of_work:
        mission = unit_of_work.get_mission("mission-1")
        task = unit_of_work.get_task("task-1")
        attempt = unit_of_work.get_attempt("attempt-1")
        assert mission is not None and task is not None and attempt is not None
        assert mission.original_spec_json == "{}"
        mission.start()
        task.start()
        attempt.claim(owner="worker-a", now=now, lease_expires_at=now + timedelta(seconds=30))
        unit_of_work.commit()

    with unit_of_work:
        persisted = unit_of_work.get_attempt("attempt-1")
        assert persisted is not None
        assert persisted.lease_owner == "worker-a"
        assert persisted.lease_epoch == 1
        unit_of_work.commit()
    with session_factory() as session:
        assert len(session.scalars(select(AuditEventRow)).all()) == 1
        assert len(session.scalars(select(OutboxEventRow)).all()) == 1


def test_sqlalchemy_uow_rejects_a_stale_mission_version() -> None:
    session_factory = _session_factory()
    now = datetime(2026, 7, 12, tzinfo=timezone.utc)
    _seed(SqlAlchemyUnitOfWork(session_factory), now)
    first = SqlAlchemyUnitOfWork(session_factory)
    second = SqlAlchemyUnitOfWork(session_factory)

    with first:
        first_mission = first.get_mission("mission-1")
        assert first_mission is not None
        first_mission.start()
        with second:
            second_mission = second.get_mission("mission-1")
            assert second_mission is not None
            second_mission.start()
            second.commit()
        with pytest.raises(OptimisticLockConflict):
            first.commit()


def test_sqlalchemy_uow_rejects_stale_task_operation_and_approval_versions() -> None:
    session_factory = _session_factory()
    now = datetime(2026, 7, 12, tzinfo=timezone.utc)
    _seed(SqlAlchemyUnitOfWork(session_factory), now)
    operation = Operation(
        operation_id="operation-1",
        idempotency_key="attempt-1:sandbox",
        attempt_id=AttemptId("attempt-1"),
        operation_type=OperationType.SANDBOX_RUN,
        input_hash="a" * 64,
        lease_epoch=1,
        target_ref_or_path="worktree",
        created_at=now,
        updated_at=now,
    )
    approval = Approval(
        approval_id="approval-1",
        mission_id=MissionId("mission-1"),
        task_id=TaskId("task-1"),
        attempt_id=AttemptId("attempt-1"),
        action_type="CANDIDATE_COMMIT",
        action_hash="b" * 64,
        risk_level="HIGH",
        scope="CANDIDATE_COMMIT",
        requested_at=now,
        expires_at=now + timedelta(minutes=5),
    )
    seed = SqlAlchemyUnitOfWork(session_factory)
    with seed:
        seed.add_operation(operation)
        seed.add_approval(approval)
        seed.commit()

    first = SqlAlchemyUnitOfWork(session_factory)
    second = SqlAlchemyUnitOfWork(session_factory)
    with first:
        task = first.get_task("task-1")
        assert task is not None
        task.start()
        with second:
            competing = second.get_task("task-1")
            assert competing is not None
            competing.start()
            second.commit()
        with pytest.raises(OptimisticLockConflict, match="Task task-1"):
            first.commit()

    first = SqlAlchemyUnitOfWork(session_factory)
    second = SqlAlchemyUnitOfWork(session_factory)
    with first:
        pending = first.get_operation_by_idempotency_key("attempt-1:sandbox")
        assert pending is not None
        pending.begin(now)
        with second:
            competing = second.get_operation_by_idempotency_key("attempt-1:sandbox")
            assert competing is not None
            competing.begin(now)
            second.commit()
        with pytest.raises(OptimisticLockConflict, match="Operation operation-1"):
            first.commit()

    first = SqlAlchemyUnitOfWork(session_factory)
    second = SqlAlchemyUnitOfWork(session_factory)
    with first:
        pending = first.get_approval("approval-1")
        assert pending is not None
        pending.approve(decided_by="reviewer-a", now=pending.requested_at)
        with second:
            competing = second.get_approval("approval-1")
            assert competing is not None
            competing.approve(decided_by="reviewer-b", now=competing.requested_at)
            second.commit()
        with pytest.raises(OptimisticLockConflict, match="Approval approval-1"):
            first.commit()


def test_sqlalchemy_uow_lists_only_stale_reconcilable_operations() -> None:
    session_factory = _session_factory()
    unit_of_work = SqlAlchemyUnitOfWork(session_factory)
    now = datetime(2026, 7, 12, tzinfo=timezone.utc)
    _seed(unit_of_work, now)
    stale = Operation(
        operation_id="operation-stale",
        idempotency_key="attempt-1:sandbox",
        attempt_id=AttemptId("attempt-1"),
        operation_type=OperationType.SANDBOX_RUN,
        input_hash="a" * 64,
        lease_epoch=1,
        target_ref_or_path="worktree",
        created_at=now,
        updated_at=now,
    )
    fresh = Operation(
        operation_id="operation-fresh",
        idempotency_key="attempt-1:bundle",
        attempt_id=AttemptId("attempt-1"),
        operation_type=OperationType.BUNDLE_BUILD,
        input_hash="b" * 64,
        lease_epoch=1,
        target_ref_or_path="bundle",
        created_at=now,
        updated_at=now + timedelta(minutes=5),
    )
    with unit_of_work:
        unit_of_work.add_operation(stale)
        unit_of_work.add_operation(fresh)
        unit_of_work.commit()
    with unit_of_work:
        operations = unit_of_work.get_stale_operations(
            updated_before=now + timedelta(minutes=1),
            statuses=(OperationStatus.PREPARED, OperationStatus.EXECUTING),
            limit=10,
        )
        unit_of_work.commit()

    assert tuple(operation.operation_id for operation in operations) == ("operation-stale",)


def test_sqlalchemy_uow_persists_approval_and_resumed_attempt() -> None:
    session_factory = _session_factory()
    unit_of_work = SqlAlchemyUnitOfWork(session_factory)
    now = datetime(2026, 7, 12, tzinfo=timezone.utc)
    _seed(unit_of_work, now)

    with unit_of_work:
        approval = Approval(
            approval_id="approval-1",
            mission_id=MissionId("mission-1"),
            task_id=TaskId("task-1"),
            attempt_id=AttemptId("attempt-1"),
            action_type="CANDIDATE_COMMIT",
            action_hash="b" * 64,
            risk_level="HIGH",
            scope="CANDIDATE_COMMIT",
            requested_at=now,
            expires_at=now + timedelta(minutes=5),
            patch_artifact=ArtifactRef("b" * 64, 42, "text/x-diff; charset=utf-8"),
        )
        approval.approve(decided_by="reviewer", now=now)
        resumed = Attempt(
            AttemptId("attempt-2"),
            TaskId("task-1"),
            2,
            0,
            now,
            resume_from_attempt_id=AttemptId("attempt-1"),
        )
        unit_of_work.add_approval(approval)
        unit_of_work.add_attempt(resumed)
        unit_of_work.commit()

    reloaded = SqlAlchemyUnitOfWork(session_factory)
    with reloaded:
        approval = reloaded.get_approval("approval-1")
        resumed = reloaded.get_attempt("attempt-2")
        assert approval is not None and approval.status is ApprovalStatus.APPROVED
        assert approval.decided_by == "reviewer"
        assert approval.patch_artifact == ArtifactRef("b" * 64, 42, "text/x-diff; charset=utf-8")
        assert resumed is not None and resumed.resume_from_attempt_id == AttemptId("attempt-1")
        reloaded.commit()
    with session_factory() as session:
        assert session.get(ApprovalRow, "approval-1") is not None
