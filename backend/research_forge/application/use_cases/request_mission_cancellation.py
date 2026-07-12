"""Persist a cancellation request before a worker stops external execution."""

from __future__ import annotations

from research_forge.application.ports.system import Clock, IdGenerator
from research_forge.application.ports.unit_of_work import UnitOfWork
from research_forge.application.use_cases.claim_baseline_attempt import AttemptNotFound
from research_forge.domain.mission import AuditEvent


class RequestMissionCancellation:
    """Move an active Mission to CANCELLING without pretending its sandbox stopped."""

    def __init__(self, *, unit_of_work: UnitOfWork, clock: Clock, id_generator: IdGenerator) -> None:
        self._unit_of_work = unit_of_work
        self._clock = clock
        self._id_generator = id_generator

    def execute(self, *, mission_id: str) -> None:
        now = self._clock.now()
        with self._unit_of_work:
            mission = self._unit_of_work.get_mission(mission_id)
            if mission is None:
                raise AttemptNotFound(f"mission {mission_id}")
            mission.request_cancel()
            self._unit_of_work.add_audit_event(
                AuditEvent(
                    event_id=self._id_generator.new("audit"),
                    aggregate_type="mission",
                    aggregate_id=mission_id,
                    event_type="mission.cancellation_requested",
                    occurred_at=now,
                    data={},
                )
            )
            self._unit_of_work.commit()
