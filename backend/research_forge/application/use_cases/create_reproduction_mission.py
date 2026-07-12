"""Create the initial baseline Mission/Task/Attempt transaction for VS-001."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from research_forge.application.dto.reproduction_spec import (
    JsonSchemaReproductionSpecValidator,
)
from research_forge.application.ports.system import Clock, IdGenerator
from research_forge.application.ports.unit_of_work import UnitOfWork
from research_forge.application.ports.reproduction_prerequisites import ReproductionPrerequisiteVerifier
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


@dataclass(frozen=True, slots=True)
class MissionView:
    mission_id: str
    task_id: str
    attempt_id: str
    status: str
    spec_sha256: str


class CreateReproductionMission:
    """Validate a ReproductionSpec and atomically create its baseline work."""

    def __init__(
        self,
        *,
        spec_validator: JsonSchemaReproductionSpecValidator,
        unit_of_work: UnitOfWork,
        clock: Clock,
        id_generator: IdGenerator,
        prerequisite_verifier: ReproductionPrerequisiteVerifier,
    ) -> None:
        self._spec_validator = spec_validator
        self._unit_of_work = unit_of_work
        self._clock = clock
        self._id_generator = id_generator
        self._prerequisite_verifier = prerequisite_verifier

    def execute(self, raw_spec: Mapping[str, Any]) -> MissionView:
        spec = self._spec_validator.validate(raw_spec)
        self._verify_prerequisites(spec.payload)
        now = self._clock.now()

        mission = Mission.create(
            mission_id=MissionId(self._id_generator.new("mission")),
            spec_sha256=spec.sha256,
            normalized_spec_json=spec.normalized_json,
            created_at=now,
        )
        mission.mark_ready()
        task = Task(
            task_id=TaskId(self._id_generator.new("task")),
            mission_id=mission.mission_id,
            task_type=TaskType.BASELINE_REPRODUCTION,
            created_at=now,
        )
        attempt = Attempt(
            attempt_id=AttemptId(self._id_generator.new("attempt")),
            task_id=task.task_id,
            attempt_number=1,
            lease_epoch=0,
            created_at=now,
        )
        event_payload: dict[str, object] = {
            "mission_id": str(mission.mission_id),
            "task_id": str(task.task_id),
            "attempt_id": str(attempt.attempt_id),
            "spec_sha256": spec.sha256,
        }
        audit_event = AuditEvent(
            event_id=self._id_generator.new("audit"),
            aggregate_type="mission",
            aggregate_id=str(mission.mission_id),
            event_type="mission.created",
            occurred_at=now,
            data=event_payload,
        )
        outbox_event = OutboxEvent(
            event_id=self._id_generator.new("outbox"),
            topic="baseline_attempt.ready",
            aggregate_id=str(mission.mission_id),
            occurred_at=now,
            payload=event_payload,
        )

        with self._unit_of_work:
            self._unit_of_work.add_mission(mission)
            self._unit_of_work.add_task(task)
            self._unit_of_work.add_attempt(attempt)
            self._unit_of_work.add_audit_event(audit_event)
            self._unit_of_work.add_outbox_event(outbox_event)
            self._unit_of_work.commit()

        return MissionView(
            mission_id=str(mission.mission_id),
            task_id=str(task.task_id),
            attempt_id=str(attempt.attempt_id),
            status=str(mission.status),
            spec_sha256=spec.sha256,
        )

    def _verify_prerequisites(self, spec: Mapping[str, Any]) -> None:
        paper = spec["paper"]
        repository = spec["repository"]
        execution = spec["execution"]
        self._prerequisite_verifier.verify(
            paper_artifact_id=paper["artifact_id"],
            paper_sha256=paper["sha256"],
            repository_url_or_path=repository["url_or_path"],
            commit_sha=repository["commit_sha"],
            image_digest=execution["image_digest"],
        )
