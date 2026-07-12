"""Real PostgreSQL migration and source-of-truth contract, run by the dedicated CI service job."""

from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from research_forge.adapters.outbound.persistence import SqlAlchemyUnitOfWork
from research_forge.adapters.outbound.persistence.models import AuditEventRow, MissionRow, OutboxEventRow
from research_forge.domain.mission import Attempt, AttemptId, AuditEvent, Mission, MissionId, OutboxEvent, Task, TaskId, TaskType


pytestmark = pytest.mark.postgres
command = pytest.importorskip("alembic.command")
Config = pytest.importorskip("alembic.config").Config


def test_postgres_migration_and_uow_are_the_durable_source_of_truth() -> None:
    database_url = os.getenv("RF_TEST_POSTGRES_URL")
    if not database_url:
        pytest.skip("Set RF_TEST_POSTGRES_URL to run the PostgreSQL source-of-truth contract.")
    pytest.importorskip("psycopg2")
    config = Config(str(Path(__file__).resolve().parents[2] / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "head")
    engine = create_engine(database_url, pool_pre_ping=True)
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    try:
        now = datetime(2026, 7, 12, tzinfo=timezone.utc)
        unit_of_work = SqlAlchemyUnitOfWork(session_factory)
        mission = Mission.create(
            mission_id=MissionId("postgres-mission-1"),
            spec_sha256="a" * 64,
            normalized_spec_json='{"schema_version":1}',
            original_spec_json='{"schema_version":1}',
            created_at=now,
        )
        mission.mark_ready()
        task = Task(TaskId("postgres-task-1"), mission.mission_id, TaskType.BASELINE_REPRODUCTION, now)
        attempt = Attempt(AttemptId("postgres-attempt-1"), task.task_id, 1, 0, now)
        with unit_of_work:
            unit_of_work.add_mission(mission)
            unit_of_work.add_task(task)
            unit_of_work.add_attempt(attempt)
            unit_of_work.add_audit_event(
                AuditEvent("postgres-audit-1", "mission", str(mission.mission_id), "mission.created", now, {})
            )
            unit_of_work.add_outbox_event(
                OutboxEvent(
                    "postgres-outbox-1",
                    "baseline_attempt.ready",
                    str(mission.mission_id),
                    now,
                    {"attempt_id": str(attempt.attempt_id)},
                )
            )
            unit_of_work.commit()

        reloaded = SqlAlchemyUnitOfWork(session_factory)
        with reloaded:
            persisted = reloaded.get_mission(str(mission.mission_id))
            assert persisted is not None
            assert persisted.spec_sha256 == mission.spec_sha256
            assert persisted.original_spec_json == mission.original_spec_json
            assert reloaded.get_unpublished_outbox_events(10)[0].event_id == "postgres-outbox-1"
            reloaded.commit()
        with session_factory() as session:
            assert session.scalar(select(MissionRow.mission_id)) == "postgres-mission-1"
            assert session.scalar(select(AuditEventRow.event_id)) == "postgres-audit-1"
            assert session.scalar(select(OutboxEventRow.event_id)) == "postgres-outbox-1"
    finally:
        engine.dispose()
        command.downgrade(config, "base")
