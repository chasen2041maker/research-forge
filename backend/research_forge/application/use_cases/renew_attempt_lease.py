"""Renew a worker lease with owner, epoch, and optimistic-version checks."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

from research_forge.application.ports.system import Clock
from research_forge.application.ports.unit_of_work import UnitOfWork
from research_forge.application.use_cases.claim_baseline_attempt import AttemptNotFound


@dataclass(frozen=True, slots=True)
class HeartbeatView:
    attempt_id: str
    epoch: int
    version: int


class RenewAttemptLease:
    """Extend a claimed Attempt lease without exposing persistence details to workers."""

    def __init__(self, *, unit_of_work: UnitOfWork, clock: Clock, lease_duration: timedelta) -> None:
        if lease_duration <= timedelta(0):
            raise ValueError("lease_duration must be positive")
        self._unit_of_work = unit_of_work
        self._clock = clock
        self._lease_duration = lease_duration

    def execute(self, *, attempt_id: str, owner: str, epoch: int, expected_version: int) -> HeartbeatView:
        now = self._clock.now()
        with self._unit_of_work:
            attempt = self._unit_of_work.get_attempt(attempt_id)
            if attempt is None:
                raise AttemptNotFound(attempt_id)
            attempt.heartbeat(
                owner=owner,
                epoch=epoch,
                expected_version=expected_version,
                now=now,
                lease_expires_at=now + self._lease_duration,
            )
            self._unit_of_work.commit()
        return HeartbeatView(attempt_id=attempt_id, epoch=epoch, version=attempt.version)
