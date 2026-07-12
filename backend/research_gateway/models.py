"""User-confirmed input supplied between an unverified proposal and a frozen Forge spec."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass


class ProposalCompletionValidationError(ValueError):
    """Raised when the human completion form is structurally incomplete."""


@dataclass(frozen=True, slots=True)
class ProposalCompletionV1:
    """Full user-confirmed content for a ReproductionSpec; no values are taken from Studio."""

    payload: Mapping[str, object]

    @classmethod
    def from_mapping(cls, raw: Mapping[str, object]) -> ProposalCompletionV1:
        if not isinstance(raw, Mapping):
            raise ProposalCompletionValidationError("completion must be a JSON object")
        required = {"mode", "paper", "repository", "execution", "metric", "change_budget", "budget"}
        missing = sorted(required.difference(raw))
        if missing:
            raise ProposalCompletionValidationError("completion is missing: " + ", ".join(missing))
        try:
            normalized = json.loads(json.dumps(raw, ensure_ascii=False, allow_nan=False))
        except (TypeError, ValueError) as exc:
            raise ProposalCompletionValidationError(
                f"completion is not JSON canonicalizable: {exc}"
            ) from exc
        if not isinstance(normalized, dict):
            raise ProposalCompletionValidationError("completion must normalize to an object")
        return cls(payload=normalized)

    def value_at(self, path: str) -> object | None:
        value: object = self.payload
        for part in path.split("."):
            if not isinstance(value, Mapping) or part not in value:
                return None
            value = value[part]
        return value
