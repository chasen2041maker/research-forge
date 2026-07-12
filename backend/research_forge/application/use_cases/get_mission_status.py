"""Read the durable Mission timeline without exposing repositories or ORM rows to inbound adapters."""

from __future__ import annotations

from dataclasses import dataclass

from research_forge.application.ports.unit_of_work import UnitOfWork


class MissionNotFound(ValueError):
    """Raised when a requested Mission no longer exists in the durable source of truth."""


@dataclass(frozen=True, slots=True)
class AttemptStatusView:
    attempt_id: str
    task_id: str
    status: str
    lease_epoch: int
    failure_code: str | None


@dataclass(frozen=True, slots=True)
class TaskStatusView:
    task_id: str
    task_type: str
    status: str
    attempts: tuple[AttemptStatusView, ...]


@dataclass(frozen=True, slots=True)
class MissionStatusView:
    mission_id: str
    status: str
    spec_sha256: str
    tasks: tuple[TaskStatusView, ...]
    bundle_sha256: str | None


class GetMissionStatus:
    """Construct a stable, read-only timeline view from the transactional Mission source of truth."""

    def __init__(self, *, unit_of_work: UnitOfWork) -> None:
        self._unit_of_work = unit_of_work

    def execute(self, mission_id: str) -> MissionStatusView:
        with self._unit_of_work:
            mission = self._unit_of_work.get_mission(mission_id)
            if mission is None:
                raise MissionNotFound(mission_id)
            tasks = tuple(
                TaskStatusView(
                    task_id=str(task.task_id),
                    task_type=str(task.task_type),
                    status=str(task.status),
                    attempts=tuple(
                        AttemptStatusView(
                            attempt_id=str(attempt.attempt_id),
                            task_id=str(attempt.task_id),
                            status=str(attempt.status),
                            lease_epoch=attempt.lease_epoch,
                            failure_code=attempt.failure_code,
                        )
                        for attempt in self._unit_of_work.get_attempts_for_task(str(task.task_id))
                    ),
                )
                for task in self._unit_of_work.get_tasks_for_mission(mission_id)
            )
            bundle = self._unit_of_work.get_bundle(mission_id)
            self._unit_of_work.commit()
        return MissionStatusView(
            mission_id=mission_id,
            status=str(mission.status),
            spec_sha256=mission.spec_sha256,
            tasks=tasks,
            bundle_sha256=bundle.artifact.sha256 if bundle is not None else None,
        )
