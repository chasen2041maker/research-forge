"""The unverified proposal contract emitted by Research Studio."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from jsonschema import Draft202012Validator


class ResearchProposalValidationError(ValueError):
    """Raised when a ResearchProposal v1 payload cannot cross the product boundary."""


@dataclass(frozen=True, slots=True)
class ResearchProposalV1:
    """A versioned Studio result; it is explicitly not evidence of a verified claim."""

    payload: Mapping[str, object]

    @property
    def proposal_id(self) -> str:
        return _string(self.payload, "proposal_id")

    @property
    def status(self) -> str:
        return _string(self.payload, "status")

    @property
    def missing_fields(self) -> tuple[str, ...]:
        fields = self.payload["missing_fields"]
        if not isinstance(fields, list):
            raise ResearchProposalValidationError("missing_fields must be an array")
        return tuple(item for item in fields if isinstance(item, str))

    def to_mapping(self) -> dict[str, object]:
        """Return a detached JSON-compatible copy suitable for transport."""
        return json.loads(json.dumps(self.payload, ensure_ascii=False, allow_nan=False))

    @classmethod
    def from_mapping(cls, raw: Mapping[str, object]) -> ResearchProposalV1:
        if not isinstance(raw, Mapping):
            raise ResearchProposalValidationError("ResearchProposal v1 must be a JSON object")

        errors = sorted(_validator().iter_errors(raw), key=lambda error: list(error.absolute_path))
        if errors:
            messages = "; ".join(_format_error(error) for error in errors)
            raise ResearchProposalValidationError(messages)

        try:
            normalized = json.loads(
                json.dumps(raw, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
            )
        except (TypeError, ValueError) as exc:
            raise ResearchProposalValidationError(
                f"ResearchProposal v1 is not JSON canonicalizable: {exc}"
            ) from exc
        if not isinstance(normalized, dict):
            raise ResearchProposalValidationError("ResearchProposal v1 must normalize to an object")
        return cls(payload=normalized)


def _validator() -> Draft202012Validator:
    schema = json.loads(
        Path(__file__).with_name("research_proposal_v1.schema.json").read_text(encoding="utf-8")
    )
    return Draft202012Validator(schema)


def _format_error(error: object) -> str:
    path = getattr(error, "absolute_path")
    location = "$" if not path else "$." + ".".join(str(part) for part in path)
    return f"{location}: {getattr(error, 'message')}"


def _string(payload: Mapping[str, object], key: str) -> str:
    value = payload[key]
    if not isinstance(value, str):
        raise ResearchProposalValidationError(f"{key} must be a string")
    return value
