"""Read-only Studio projection for a Forge-issued VerifiedResult contract."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from research_contracts import VerifiedResultV1


@dataclass(frozen=True, slots=True)
class StudioVerifiedReport:
    """Facts that Studio may display without turning verification evidence into speculative prose."""

    proposal_id: str
    mission_id: str
    spec_sha256: str
    metric: Mapping[str, object]
    bundle_sha256: str
    completed_at: str

    def to_mapping(self) -> dict[str, object]:
        return {
            "status": "VERIFIED",
            "proposal_id": self.proposal_id,
            "mission_id": self.mission_id,
            "spec_sha256": self.spec_sha256,
            "metric": dict(self.metric),
            "bundle_sha256": self.bundle_sha256,
            "completed_at": self.completed_at,
        }


def write_verified_result(result: VerifiedResultV1) -> StudioVerifiedReport:
    """Project only the Forge-verified contract fields; no Studio state or LLM is consulted."""
    verified = VerifiedResultV1.from_mapping(result.to_mapping())
    return StudioVerifiedReport(
        proposal_id=verified.proposal_id,
        mission_id=verified.mission_id,
        spec_sha256=verified.spec_sha256,
        metric=verified.metric,
        bundle_sha256=verified.bundle_sha256,
        completed_at=verified.completed_at,
    )
