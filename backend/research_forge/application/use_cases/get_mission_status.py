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
class ApprovalStatusView:
    approval_id: str
    task_id: str
    attempt_id: str
    action_hash: str
    risk_level: str
    scope: str
    status: str
    requested_at: str
    expires_at: str
    decided_by: str | None


@dataclass(frozen=True, slots=True)
class MissionStatusView:
    mission_id: str
    status: str
    spec_sha256: str
    tasks: tuple[TaskStatusView, ...]
    approvals: tuple[ApprovalStatusView, ...]
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
            approvals = tuple(
                ApprovalStatusView(
                    approval_id=approval.approval_id,
                    task_id=str(approval.task_id),
                    attempt_id=str(approval.attempt_id),
                    action_hash=approval.action_hash,
                    risk_level=approval.risk_level,
                    scope=approval.scope,
                    status=str(approval.status),
                    requested_at=approval.requested_at.isoformat(),
                    expires_at=approval.expires_at.isoformat(),
                    decided_by=approval.decided_by,
                )
                for approval in self._unit_of_work.get_approvals_for_mission(mission_id)
            )
            self._unit_of_work.commit()
        return MissionStatusView(
            mission_id=mission_id,
            status=str(mission.status),
            spec_sha256=mission.spec_sha256,
            tasks=tasks,
            approvals=approvals,
            bundle_sha256=bundle.artifact.sha256 if bundle is not None else None,
        )
