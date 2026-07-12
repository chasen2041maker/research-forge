"""Lease, cancellation, operation, and transaction tests for VS-001."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from research_forge.adapters.outbound.persistence import InMemoryUnitOfWork
from research_forge.application.use_cases import (
    ClaimBaselineAttempt,
    ReconcileStaleOperations,
    RenewAttemptLease,
    RequestMissionCancellation,
)
from research_forge.domain.errors import InvalidMissionTransition, LeaseLost, OperationConflict
from research_forge.domain.execution import Operation, OperationStatus, OperationType
from research_forge.domain.mission import Attempt, AttemptId, Mission, MissionId, MissionStatus, Task, TaskId, TaskType


class _MutableClock:
    def __init__(self) -> None:
        self.current = datetime(2026, 7, 12, tzinfo=timezone.utc)

    def now(self) -> datetime:
        return self.current

    def advance(self, seconds: int) -> None:
        self.current += timedelta(seconds=seconds)


class _Ids:
    def __init__(self) -> None:
        self.value = 0

    def new(self, kind: str) -> str:
        self.value += 1
        return f"{kind}-{self.value}"


def _seed_attempt(uow: InMemoryUnitOfWork, clock: _MutableClock) -> Attempt:
    mission = Mission.create(
        mission_id=MissionId("mission-1"),
        spec_sha256="a" * 64,
        normalized_spec_json="{}",
        created_at=clock.now(),
    )
    mission.mark_ready()
    task = Task(TaskId("task-1"), mission.mission_id, TaskType.BASELINE_REPRODUCTION, clock.now())
    attempt = Attempt(AttemptId("attempt-1"), task.task_id, 1, 0, clock.now())
    with uow:
        uow.add_mission(mission)
        uow.add_task(task)
        uow.add_attempt(attempt)
        uow.commit()
    return attempt


def test_expired_lease_can_be_reclaimed_but_old_worker_cannot_heartbeat() -> None:
    clock = _MutableClock()
    uow = InMemoryUnitOfWork()
    _seed_attempt(uow, clock)
    claimer = ClaimBaselineAttempt(unit_of_work=uow, clock=clock, lease_duration=timedelta(seconds=30))
    heartbeat = RenewAttemptLease(unit_of_work=uow, clock=clock, lease_duration=timedelta(seconds=30))

    first = claimer.execute(attempt_id="attempt-1", owner="worker-a")
    clock.advance(31)
    second = claimer.execute(attempt_id="attempt-1", owner="worker-b")

    assert (first.epoch, second.epoch) == (1, 2)
    with pytest.raises(LeaseLost):
        heartbeat.execute(
            attempt_id="attempt-1",
            owner="worker-a",
            epoch=first.epoch,
            expected_version=second.version,
        )


def test_heartbeat_requires_current_epoch_and_advances_version() -> None:
    clock = _MutableClock()
    uow = InMemoryUnitOfWork()
    _seed_attempt(uow, clock)
    lease = ClaimBaselineAttempt(unit_of_work=uow, clock=clock, lease_duration=timedelta(seconds=30)).execute(
        attempt_id="attempt-1", owner="worker-a"
    )

    renewed = RenewAttemptLease(unit_of_work=uow, clock=clock, lease_duration=timedelta(seconds=30)).execute(
        attempt_id="attempt-1",
        owner=lease.owner,
        epoch=lease.epoch,
        expected_version=lease.version,
    )

    assert renewed.version == lease.version + 1


def test_cancellation_is_persisted_before_any_worker_reports_cancelled() -> None:
    clock = _MutableClock()
    uow = InMemoryUnitOfWork()
    _seed_attempt(uow, clock)
    ClaimBaselineAttempt(unit_of_work=uow, clock=clock, lease_duration=timedelta(seconds=30)).execute(
        attempt_id="attempt-1", owner="worker-a"
    )

    RequestMissionCancellation(unit_of_work=uow, clock=clock, id_generator=_Ids()).execute(mission_id="mission-1")

    mission = uow.get_mission("mission-1")
    assert mission is not None
    assert mission.status is MissionStatus.CANCELLING
    assert uow.audits[-1].event_type == "mission.cancellation_requested"


def test_unit_of_work_rolls_back_all_writes_when_an_exception_escapes() -> None:
    uow = InMemoryUnitOfWork()
    clock = _MutableClock()
    operation = Operation(
        operation_id="operation-1",
        idempotency_key="attempt-1:cas:log",
        attempt_id=AttemptId("attempt-1"),
        operation_type=OperationType.CAS_PUT,
        input_hash="a" * 64,
        lease_epoch=1,
        target_ref_or_path="logs/execution.log",
        created_at=clock.now(),
        updated_at=clock.now(),
    )

    with pytest.raises(RuntimeError, match="rollback"):
        with uow:
            uow.add_operation(operation)
            raise RuntimeError("rollback")

    assert uow.get_operation_by_idempotency_key(operation.idempotency_key) is None


def test_operation_succeeds_idempotently_but_rejects_conflicting_result() -> None:
    clock = _MutableClock()
    operation = Operation(
        operation_id="operation-1",
        idempotency_key="attempt-1:cas:log",
        attempt_id=AttemptId("attempt-1"),
        operation_type=OperationType.CAS_PUT,
        input_hash="a" * 64,
        lease_epoch=1,
        target_ref_or_path="logs/execution.log",
        created_at=clock.now(),
        updated_at=clock.now(),
    )

    operation.succeed(external_result_ref="sha256:" + "b" * 64, now=clock.now())
    operation.succeed(external_result_ref="sha256:" + "b" * 64, now=clock.now())

    assert operation.status is OperationStatus.SUCCEEDED
    with pytest.raises(OperationConflict, match="conflicts"):
        operation.succeed(external_result_ref="sha256:" + "c" * 64, now=clock.now())


def test_reconciler_requeues_each_stale_operation_once_through_the_outbox() -> None:
    clock = _MutableClock()
    uow = InMemoryUnitOfWork()
    _seed_attempt(uow, clock)
    operation = Operation(
        operation_id="operation-1",
        idempotency_key="attempt-1:sandbox",
        attempt_id=AttemptId("attempt-1"),
        operation_type=OperationType.SANDBOX_RUN,
        input_hash="a" * 64,
        lease_epoch=1,
        target_ref_or_path="worktree",
        created_at=clock.now(),
        updated_at=clock.now(),
    )
    with uow:
        uow.add_operation(operation)
        uow.commit()
    clock.advance(61)
    reconciler = ReconcileStaleOperations(
        unit_of_work=uow,
        clock=clock,
        id_generator=_Ids(),
        stale_after=timedelta(seconds=60),
    )

    first = reconciler.execute()
    second = reconciler.execute()

    assert first.operation_ids == ("operation-1",)
    assert second.operation_ids == ()
    assert uow.outbox[-1].topic == "baseline_attempt.ready"
    assert uow.outbox[-1].payload["attempt_id"] == "attempt-1"


def test_mission_state_machine_refuses_skipped_completion() -> None:
    mission = Mission.create(
        mission_id=MissionId("mission-1"),
        spec_sha256="a" * 64,
        normalized_spec_json="{}",
        created_at=_MutableClock().now(),
    )
    mission.mark_ready()

    with pytest.raises(InvalidMissionTransition):
        mission.complete()
