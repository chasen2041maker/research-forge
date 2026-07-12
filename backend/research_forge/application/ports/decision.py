"""Decision-only port for bounded repair proposals; implementations receive no side-effect ports."""

from __future__ import annotations

from typing import Protocol

from research_forge.application.dto.repair import ActionProposal, DecisionRequest


class DecisionEngine(Protocol):
    """Return an untrusted action proposal; Application owns every policy and side effect."""

    def propose(self, request: DecisionRequest) -> ActionProposal: ...
