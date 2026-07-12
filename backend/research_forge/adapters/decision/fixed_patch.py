"""Deterministic test adapter for the bounded repair DecisionEngine port."""

from __future__ import annotations

from research_forge.application.dto.repair import ActionProposal, DecisionRequest


class FixedPatchDecisionEngine:
    """Return one configured proposal, useful for frozen repair fixtures and contract tests."""

    def __init__(self, proposal: ActionProposal) -> None:
        self._proposal = proposal
        self.requests: list[DecisionRequest] = []

    def propose(self, request: DecisionRequest) -> ActionProposal:
        self.requests.append(request)
        return self._proposal
