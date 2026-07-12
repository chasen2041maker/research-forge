"""Decision adapter tests: a model sees only verified evidence and cannot call side-effect ports."""

from __future__ import annotations

import json

from research_forge.adapters.decision import LimitedPatchDecisionEngine
from research_forge.application.dto import ActionProposal, DecisionRequest


class _Generator:
    def __init__(self, patch: str) -> None:
        self.patch = patch
        self.prompts: list[str] = []

    def complete_patch(self, prompt: str) -> str:
        self.prompts.append(prompt)
        return self.patch


def test_limited_patch_engine_sends_only_verified_log_path_policy_and_budgets_to_model() -> None:
    patch = "diff --git a/value.py b/value.py\n--- a/value.py\n+++ b/value.py\n@@ -1 +1 @@\n-VALUE = 0\n+VALUE = 1\n"
    generator = _Generator(patch)
    request = DecisionRequest(
        mission_id="mission-sensitive-identifier",
        spec_sha256="a" * 64,
        baseline_log="verified assertion failure",
        allowed_paths=("value.py",),
        max_files=1,
        max_changed_lines=2,
    )

    proposal = LimitedPatchDecisionEngine(generator=generator).propose(request)

    assert proposal == ActionProposal(
        action_type="APPLY_PATCH",
        unified_diff=patch,
        rationale_summary="Model-generated bounded repair proposal; execution remains policy-gated.",
        expected_artifacts=(),
    )
    prompt = generator.prompts[0]
    assert "mission-sensitive-identifier" not in prompt
    assert "a" * 64 not in prompt
    context = json.loads(prompt.split("\n", maxsplit=1)[1])
    assert context == {
        "allowed_paths": ["value.py"],
        "budget": {"max_changed_lines": 2, "max_files": 1},
        "verified_baseline_log": "verified assertion failure",
    }
