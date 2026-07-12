"""Resolve a persisted approval and schedule a new child Attempt rather than waking a blocked worker."""

from __future__ import annotations

from dataclasses import dataclass

from research_forge.application.ports.system import Clock, IdGenerator
from research_forge.application.ports.unit_of_work import UnitOfWork
from research_forge.domain.mission import Attempt, AttemptId, AuditEvent, OutboxEvent


class ApprovalNotFound(ValueError):
    """Raised when a decision targets an Approval absent from the durable source of truth."""


@dataclass(frozen=True, slots=True)
class ApprovalResolutionView:
    approval_id: str
    status: str
    resumed_attempt_id: str | None


class ResolveApproval:
    """Approve/reject a durable proposal; approved work resumes through a fresh child Attempt."""

    def __init__(self, *, unit_of_work: UnitOfWork, clock: Clock, id_generator: IdGenerator) -> None:
        self._unit_of_work = unit_of_work
        self._clock = clock
        self._id_generator = id_generator

    def execute(self, *, approval_id: str, approved: bool, decided_by: str) -> ApprovalResolutionView:
        if not decided_by.strip():
            raise ValueError("Approval decision requires a non-empty decided_by identity.")
        now = self._clock.now()
        with self._unit_of_work:
            approval = self._unit_of_work.get_approval(approval_id)
            if approval is None:
                raise ApprovalNotFound(approval_id)
            mission = self._unit_of_work.get_mission(str(approval.mission_id))
            task = self._unit_of_work.get_task(str(approval.task_id))
            parent_attempt = self._unit_of_work.get_attempt(str(approval.attempt_id))
            if mission is None or task is None or parent_attempt is None:
                raise ValueError("Approval references incomplete Mission state.")
            if not approved:
                approval.reject(decided_by=decided_by, now=now)
                task.fail()
                mission.fail()
                self._unit_of_work.add_audit_event(
                    AuditEvent(
                        event_id=self._id_generator.new("audit"),
                        aggregate_type="mission",
                        aggregate_id=str(mission.mission_id),
                        event_type="repair.approval_rejected",
                        occurred_at=now,
                        data={"approval_id": approval_id, "decided_by": decided_by},
                    )
                )
                self._unit_of_work.commit()
                return ApprovalResolutionView(approval_id, str(approval.status), None)
            approval.approve(decided_by=decided_by, now=now)
            mission.start()
            child_attempt = Attempt(
                attempt_id=AttemptId(self._id_generator.new("attempt")),
                task_id=task.task_id,
                attempt_number=parent_attempt.attempt_number + 1,
                lease_epoch=0,
                created_at=now,
                resume_from_attempt_id=parent_attempt.attempt_id,
            )
            self._unit_of_work.add_attempt(child_attempt)
            event_payload = {
                "approval_id": approval_id,
                "attempt_id": str(child_attempt.attempt_id),
                "resume_from_attempt_id": str(parent_attempt.attempt_id),
            }
            self._unit_of_work.add_audit_event(
                AuditEvent(
                    event_id=self._id_generator.new("audit"),
                    aggregate_type="mission",
                    aggregate_id=str(mission.mission_id),
                    event_type="repair.approval_approved",
                    occurred_at=now,
                    data=event_payload,
                )
            )
            self._unit_of_work.add_outbox_event(
                OutboxEvent(
                    event_id=self._id_generator.new("outbox"),
                    topic="repair_attempt.ready",
                    aggregate_id=str(mission.mission_id),
                    occurred_at=now,
                    payload=event_payload,
                )
            )
            self._unit_of_work.commit()
        return ApprovalResolutionView(approval_id, str(approval.status), str(child_attempt.attempt_id))
