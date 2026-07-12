"""Query completed baseline outcome before retrying an already-delivered queue message."""

from __future__ import annotations

from dataclasses import dataclass

from research_forge.application.ports.unit_of_work import UnitOfWork
from research_forge.application.use_cases.claim_baseline_attempt import AttemptNotFound
from research_forge.domain.mission import MissionStatus


@dataclass(frozen=True, slots=True)
class ExistingBundleView:
    sha256: str
    size_bytes: int
    uri: str


class GetBaselineOutcome:
    """Read only durable business state; a queue retry never creates a second Mission result."""

    def __init__(self, *, unit_of_work: UnitOfWork) -> None:
        self._unit_of_work = unit_of_work

    def execute(self, attempt_id: str) -> ExistingBundleView | None:
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
            bundle = self._unit_of_work.get_bundle(str(mission.mission_id))
            self._unit_of_work.commit()
        if mission.status is not MissionStatus.COMPLETED or bundle is None:
            return None
        return ExistingBundleView(
            sha256=bundle.artifact.sha256,
            size_bytes=bundle.artifact.size_bytes,
            uri=bundle.artifact.uri,
        )
