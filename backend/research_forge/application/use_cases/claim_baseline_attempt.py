"""Claim and renew durable worker leases for the baseline attempt."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

from research_forge.application.ports.system import Clock
from research_forge.application.ports.unit_of_work import UnitOfWork
from research_forge.domain.mission import AuditEvent, MissionStatus, TaskStatus


class AttemptNotFound(ValueError):
    """Raised when a worker is asked to operate on an unknown attempt."""


@dataclass(frozen=True, slots=True)
class LeaseView:
    attempt_id: str
    owner: str
    epoch: int
    version: int


class ClaimBaselineAttempt:
    """Atomically acquire a lease and start the baseline mission/task."""

    def __init__(self, *, unit_of_work: UnitOfWork, clock: Clock, lease_duration: timedelta) -> None:
        if lease_duration <= timedelta(0):
            raise ValueError("lease_duration must be positive")
        self._unit_of_work = unit_of_work
        self._clock = clock
        self._lease_duration = lease_duration

    def execute(self, *, attempt_id: str, owner: str) -> LeaseView:
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
            if mission.status is MissionStatus.READY:
                mission.start()
            attempt.claim(owner=owner, now=now, lease_expires_at=now + self._lease_duration)
            if task.status is not TaskStatus.RUNNING:
                task.start()
            self._unit_of_work.add_audit_event(
                AuditEvent(
                    event_id=f"lease-{attempt.attempt_id}-{attempt.lease_epoch}",
                    aggregate_type="attempt",
                    aggregate_id=str(attempt.attempt_id),
                    event_type="attempt.claimed",
                    occurred_at=now,
                    data={"owner": owner, "epoch": attempt.lease_epoch},
                )
            )
            self._unit_of_work.commit()
        return LeaseView(
            attempt_id=attempt_id,
            owner=owner,
            epoch=attempt.lease_epoch,
            version=attempt.version,
        )
