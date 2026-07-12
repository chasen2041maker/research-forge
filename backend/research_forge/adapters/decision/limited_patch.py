"""A capability-limited LLM adapter that can emit only one unified patch proposal."""

from __future__ import annotations

import json
from typing import Protocol

from research_forge.application.dto.repair import ActionProposal, DecisionRequest


class PatchTextGenerator(Protocol):
    """Minimal model boundary; it receives only one prebuilt, side-effect-free text prompt."""

    def complete_patch(self, prompt: str) -> str: ...


class LimitedPatchDecisionEngine:
    """Constrain an injected model to verified evidence, path policy, and budgets only."""

    def __init__(self, *, generator: PatchTextGenerator) -> None:
        self._generator = generator

    def propose(self, request: DecisionRequest) -> ActionProposal:
        unified_diff = self._generator.complete_patch(self._prompt(request))
        if not _is_unified_diff(unified_diff):
            raise ValueError("Repair model must return exactly one UTF-8 unified diff.")
        return ActionProposal(
            action_type="APPLY_PATCH",
            unified_diff=unified_diff,
            rationale_summary="Model-generated bounded repair proposal; execution remains policy-gated.",
            expected_artifacts=(),
        )

    @staticmethod
    def _prompt(request: DecisionRequest) -> str:
        """Do not pass Mission state, credentials, tools, or mutable workspace data to the model."""
        context = {
            "allowed_paths": list(request.allowed_paths),
            "budget": {
                "max_changed_lines": request.max_changed_lines,
                "max_files": request.max_files,
            },
            "verified_baseline_log": request.baseline_log,
        }
        return (
            "Return only one Git unified diff for APPLY_PATCH. Do not explain it. "
            "You may edit only allowed_paths and must respect budget.\n"
            + json.dumps(context, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        )


def _is_unified_diff(value: str) -> bool:
    return isinstance(value, str) and value.startswith("diff --git ") and "\n--- " in value and "\n+++ " in value and "\n@@ " in value
