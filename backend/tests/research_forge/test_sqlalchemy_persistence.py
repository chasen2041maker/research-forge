"""SQLAlchemy adapter contract tests; production schema setup remains migration-owned."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from research_forge.adapters.outbound.persistence import SqlAlchemyUnitOfWork
from research_forge.adapters.outbound.persistence.models import AuditEventRow, Base, OutboxEventRow
from research_forge.domain.errors import OptimisticLockConflict
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
