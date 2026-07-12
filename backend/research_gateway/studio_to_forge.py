"""Compile an explicitly completed Studio proposal into Forge's existing frozen-spec input."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from research_contracts import ResearchProposalV1
from research_gateway.models import ProposalCompletionV1


class ProposalHandoffError(ValueError):
    """Raised before a proposal can reach Forge's normal Mission creation boundary."""


@dataclass(frozen=True, slots=True)
class BuiltReproductionSpec:
    """A handoff result that preserves proposal identity outside the frozen VS-001 schema."""

    proposal_id: str
    spec: Mapping[str, object]


def compile_proposal(
    *,
    proposal: ResearchProposalV1,
    completion: ProposalCompletionV1,
) -> BuiltReproductionSpec:
    """Require human completion, then pass only that confirmed data to Forge's normal validator."""
    if proposal.status != "UNVERIFIED":
        raise ProposalHandoffError("only UNVERIFIED ResearchProposal v1 payloads may enter Forge")

    unresolved = [
        field
        for field in proposal.missing_fields
        if not _has_confirmed_value(field, completion.value_at(field))
    ]
    if unresolved:
        raise ProposalHandoffError("human completion is still missing: " + ", ".join(unresolved))

    payload = completion.payload
    return BuiltReproductionSpec(
        proposal_id=proposal.proposal_id,
        spec={
            "schema_version": 1,
            "mode": payload["mode"],
            "paper": payload["paper"],
            "repository": payload["repository"],
            "execution": payload["execution"],
            "metric": payload["metric"],
            "change_budget": payload["change_budget"],
            "budget": payload["budget"],
        },
    )


def _has_confirmed_value(field: str, value: object | None) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, tuple, Mapping)):
        return field in {"execution.setup_argv", "change_budget.allowed_paths"} or bool(value)
    return True
