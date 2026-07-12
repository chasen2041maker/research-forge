"""Build a Studio-readable report only from Forge-completed evidence."""

from __future__ import annotations

from collections.abc import Mapping

from research_contracts import ResearchProposalV1, VerifiedResultV1


def build_verified_report(
    *,
    proposal: ResearchProposalV1,
    mission_id: str,
    spec_sha256: str,
    metric: Mapping[str, object],
    bundle_sha256: str,
    completed_at: str,
) -> VerifiedResultV1:
    """Return a result contract only after a caller supplies the closed Mission evidence."""
    return VerifiedResultV1.create(
        proposal_id=proposal.proposal_id,
        mission_id=mission_id,
        spec_sha256=spec_sha256,
        metric=metric,
        bundle_sha256=bundle_sha256,
        completed_at=completed_at,
    )
