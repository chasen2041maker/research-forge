"""Expose a Studio-readable result only from Forge's completed evidence source of truth."""

from __future__ import annotations

from research_contracts import VerifiedResultV1

from research_forge.application.ports.unit_of_work import UnitOfWork
from research_forge.application.use_cases.get_mission_status import MissionNotFound
from research_forge.domain.evidence import ClaimStatus
from research_forge.domain.mission import MissionStatus


class VerifiedResultUnavailable(ValueError):
    """Raised when a Mission has not closed the evidence gate needed for a portable result."""


class GetVerifiedResult:
    """Build a read-only contract from completed Mission, Bundle, Metric, and Claim records."""

    def __init__(self, *, unit_of_work: UnitOfWork) -> None:
        self._unit_of_work = unit_of_work

    def execute(self, mission_id: str) -> VerifiedResultV1:
        with self._unit_of_work:
            mission = self._unit_of_work.get_mission(mission_id)
            if mission is None:
                raise MissionNotFound(mission_id)
            if mission.status is not MissionStatus.COMPLETED:
                raise VerifiedResultUnavailable("VerifiedResult requires a COMPLETED Mission.")
            if mission.proposal_id is None:
                raise VerifiedResultUnavailable("Mission was not created from a Studio Proposal handoff.")
            bundle = self._unit_of_work.get_bundle(mission_id)
            if bundle is None:
                raise VerifiedResultUnavailable("Completed Mission has no registered evidence Bundle.")
            metric = self._unit_of_work.get_metric_by_attempt_id(str(bundle.attempt_id))
            if metric is None:
                raise VerifiedResultUnavailable("Completed Bundle has no registered metric evidence.")
            claims = self._unit_of_work.get_claims_for_mission(mission_id)
            if not claims or any(claim.status is not ClaimStatus.VERIFIED for claim in claims):
                raise VerifiedResultUnavailable("VerifiedResult requires only VERIFIED evidence claims.")
            result = VerifiedResultV1.create(
                proposal_id=mission.proposal_id,
                mission_id=mission_id,
                spec_sha256=mission.spec_sha256,
                metric={
                    "artifact_sha256": metric.artifact.sha256,
                    "commit_sha": metric.commit_sha,
                    "comparator": metric.comparator,
                    "dataset_sha256": metric.dataset_sha256,
                    "environment_digest": metric.environment_digest,
                    "expected_value": metric.expected_value,
                    "json_pointer": metric.json_pointer,
                    "tolerance": metric.tolerance,
                    "unit": metric.unit,
                    "value": metric.value,
                },
                bundle_sha256=bundle.artifact.sha256,
                completed_at=bundle.created_at.isoformat(),
            )
            self._unit_of_work.commit()
        return result
