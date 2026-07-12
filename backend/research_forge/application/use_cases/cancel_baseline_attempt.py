"""Stop the sandbox before making a requested Mission cancellation terminal."""

from __future__ import annotations

from research_forge.application.ports.sandbox import SandboxExecutor
from research_forge.application.ports.system import Clock, IdGenerator
from research_forge.application.ports.unit_of_work import UnitOfWork
from research_forge.application.use_cases.claim_baseline_attempt import AttemptNotFound
from research_forge.domain.errors import CancellationRequested
from research_forge.domain.mission import AuditEvent, MissionStatus


class CancelBaselineAttempt:
    """Confirm cancellation only after the sandbox boundary accepted the stop request."""

    def __init__(
        self,
        *,
        unit_of_work: UnitOfWork,
        sandbox_executor: SandboxExecutor,
        clock: Clock,
        id_generator: IdGenerator,
    ) -> None:
        self._unit_of_work = unit_of_work
        self._sandbox_executor = sandbox_executor
        self._clock = clock
        self._id_generator = id_generator

    def execute(
        self,
        *,
        attempt_id: str,
        owner: str,
        epoch: int,
        expected_version: int,
        sandbox_operation_id: str,
    ) -> None:
        self._assert_cancelling(attempt_id, owner, epoch, expected_version)
        self._sandbox_executor.cancel(sandbox_operation_id)
        now = self._clock.now()
        with self._unit_of_work:
            attempt = self._unit_of_work.get_attempt(attempt_id)
            if attempt is None:
                raise AttemptNotFound(attempt_id)
            task = self._unit_of_work.get_task(str(attempt.task_id))
            if task is None:
                raise AttemptNotFound(f"task for attempt {attempt_id}")
            mission = self._unit_of_work.get_mission(str(task.mission_id))
            if mission is None:
                raise AttemptNotFound(f"mission for attempt {attempt_id}")
            attempt.cancel(owner=owner, epoch=epoch, expected_version=expected_version, now=now)
            task.cancel()
            mission.confirm_cancel()
            self._unit_of_work.add_audit_event(
                AuditEvent(
                    event_id=self._id_generator.new("audit"),
                    aggregate_type="mission",
                    aggregate_id=str(mission.mission_id),
                    event_type="mission.cancelled",
                    occurred_at=now,
                    data={"sandbox_operation_id": sandbox_operation_id},
                )
            )
            self._unit_of_work.commit()

    def _assert_cancelling(self, attempt_id: str, owner: str, epoch: int, expected_version: int) -> None:
        now = self._clock.now()
        with self._unit_of_work:
            attempt = self._unit_of_work.get_attempt(attempt_id)
            if attempt is None:
                raise AttemptNotFound(attempt_id)
            task = self._unit_of_work.get_task(str(attempt.task_id))
            if task is None:
                raise AttemptNotFound(f"task for attempt {attempt_id}")
            mission = self._unit_of_work.get_mission(str(task.mission_id))
            if mission is None:
                raise AttemptNotFound(f"mission for attempt {attempt_id}")
            if mission.status is not MissionStatus.CANCELLING:
                raise CancellationRequested("Cancellation must be requested before a sandbox is stopped.")
            attempt.assert_active_lease(owner=owner, epoch=epoch, expected_version=expected_version, now=now)
            self._unit_of_work.commit()
