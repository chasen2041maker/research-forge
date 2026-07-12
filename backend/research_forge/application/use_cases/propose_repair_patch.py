"""Build the bounded repair decision input without giving a DecisionEngine any side-effect capability."""

from __future__ import annotations

import json
from dataclasses import dataclass

from research_forge.application.dto.repair import ActionProposal, DecisionRequest
from research_forge.application.ports.artifacts import ArtifactStore
from research_forge.application.ports.decision import DecisionEngine
from research_forge.application.ports.system import Clock
from research_forge.application.ports.unit_of_work import UnitOfWork
from research_forge.application.use_cases.claim_baseline_attempt import AttemptNotFound
from research_forge.domain.artifact import ArtifactKind
from research_forge.domain.errors import OperationConflict
from research_forge.domain.mission import MissionStatus, TaskType


@dataclass(frozen=True, slots=True)
class RepairProposalView:
    proposal: ActionProposal


class ProposeRepairPatch:
    """Read verified baseline evidence, then ask a decision-only adapter for exactly one patch proposal."""

    def __init__(
        self,
        *,
        unit_of_work: UnitOfWork,
        artifact_store: ArtifactStore,
        decision_engine: DecisionEngine,
        clock: Clock,
    ) -> None:
        self._unit_of_work = unit_of_work
        self._artifact_store = artifact_store
        self._decision_engine = decision_engine
        self._clock = clock

    def execute(
        self,
        *,
        attempt_id: str,
        owner: str,
        epoch: int,
        expected_version: int,
    ) -> RepairProposalView:
        request = self._load_request(attempt_id, owner, epoch, expected_version)
        proposal = self._decision_engine.propose(request)
        if proposal.action_type != "APPLY_PATCH" or not _is_unified_diff(proposal.unified_diff):
            raise OperationConflict("Repair DecisionEngine may only propose one non-empty unified APPLY_PATCH diff.")
        return RepairProposalView(proposal=proposal)

    def _load_request(
        self, attempt_id: str, owner: str, epoch: int, expected_version: int
    ) -> DecisionRequest:
        now = self._clock.now()
        with self._unit_of_work:
            attempt = self._unit_of_work.get_attempt(attempt_id)
            if attempt is None:
                raise AttemptNotFound(attempt_id)
            task = self._unit_of_work.get_task(str(attempt.task_id))
            if task is None or task.task_type is not TaskType.REPAIR_CANDIDATE:
                raise OperationConflict("Only a repair candidate Attempt can request a repair proposal.")
            mission = self._unit_of_work.get_mission(str(task.mission_id))
            if mission is None:
                raise AttemptNotFound(f"mission for attempt {attempt_id}")
            if mission.status is not MissionStatus.RUNNING:
                raise OperationConflict("Repair proposal requires a running Mission.")
            attempt.assert_active_lease(owner=owner, epoch=epoch, expected_version=expected_version, now=now)
            spec = json.loads(mission.normalized_spec_json)
            if spec["mode"] != "repair":
                raise OperationConflict("Repair proposal requires mode='repair'.")
            baseline_task = next(
                (
                    item
                    for item in self._unit_of_work.get_tasks_for_mission(str(mission.mission_id))
                    if item.task_type is TaskType.BASELINE_REPRODUCTION
                ),
                None,
            )
            if baseline_task is None:
                raise OperationConflict("Repair proposal requires a baseline task.")
            baseline_attempts = self._unit_of_work.get_attempts_for_task(str(baseline_task.task_id))
            if not baseline_attempts:
                raise OperationConflict("Repair proposal requires a baseline Attempt.")
            baseline_log = next(
                (
                    artifact
                    for artifact in self._unit_of_work.get_artifacts_for_attempt(str(baseline_attempts[0].attempt_id))
                    if artifact.kind is ArtifactKind.EXECUTION_LOG
                ),
                None,
            )
            if baseline_log is None:
                raise OperationConflict("Repair proposal requires a verified baseline execution log.")
            request = DecisionRequest(
                mission_id=str(mission.mission_id),
                spec_sha256=mission.spec_sha256,
                baseline_log=self._artifact_store.read_verified(baseline_log.artifact).decode("utf-8", errors="replace"),
                allowed_paths=tuple(spec["change_budget"]["allowed_paths"]),
                max_files=spec["change_budget"]["max_files"],
                max_changed_lines=spec["change_budget"]["max_changed_lines"],
            )
            self._unit_of_work.commit()
        return request


def _is_unified_diff(value: str) -> bool:
    return value.startswith("diff --git ") and "\n--- " in value and "\n+++ " in value and "\n@@ " in value
