"""The verified-result contract emitted only after Forge closes its evidence gate."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass


class VerifiedResultValidationError(ValueError):
    """Raised when a verified result lacks the evidence needed for its claim."""


@dataclass(frozen=True, slots=True)
class VerifiedResultV1:
    """A portable, evidence-linked report of a Forge-completed Mission."""

    proposal_id: str
    mission_id: str
    spec_sha256: str
    metric: Mapping[str, object]
    bundle_sha256: str
    completed_at: str

    def to_mapping(self) -> dict[str, object]:
        return {
            "schema_version": 1,
            "proposal_id": self.proposal_id,
            "mission_id": self.mission_id,
            "spec_sha256": self.spec_sha256,
            "metric": json.loads(json.dumps(self.metric, ensure_ascii=False, allow_nan=False)),
            "bundle_sha256": self.bundle_sha256,
            "completed_at": self.completed_at,
            "status": "VERIFIED",
        }

    @classmethod
    def from_mapping(cls, raw: Mapping[str, object]) -> VerifiedResultV1:
        """Validate a transport payload before a read-only Studio consumer renders it."""
        if raw.get("schema_version") != 1 or raw.get("status") != "VERIFIED":
            raise VerifiedResultValidationError("VerifiedResult v1 requires schema_version=1 and status=VERIFIED")
        metric = raw.get("metric")
        if not isinstance(metric, Mapping):
            raise VerifiedResultValidationError("metric evidence must be an object")
        fields = ("proposal_id", "mission_id", "spec_sha256", "bundle_sha256", "completed_at")
        values: dict[str, str] = {}
        for field in fields:
            value = raw.get(field)
            if not isinstance(value, str):
                raise VerifiedResultValidationError(f"{field} must be a string")
            values[field] = value
        return cls.create(metric=metric, **values)

    @classmethod
    def create(
        cls,
        *,
        proposal_id: str,
        mission_id: str,
        spec_sha256: str,
        metric: Mapping[str, object],
        bundle_sha256: str,
        completed_at: str,
    ) -> VerifiedResultV1:
        scalar_fields = {
            "proposal_id": proposal_id,
            "mission_id": mission_id,
            "spec_sha256": spec_sha256,
            "bundle_sha256": bundle_sha256,
            "completed_at": completed_at,
        }
        blank = [name for name, value in scalar_fields.items() if not value.strip()]
        if blank:
            raise VerifiedResultValidationError("blank fields: " + ", ".join(blank))
        if not metric:
            raise VerifiedResultValidationError("metric evidence must not be empty")
        try:
            json.dumps(metric, ensure_ascii=False, allow_nan=False)
        except (TypeError, ValueError) as exc:
            raise VerifiedResultValidationError(f"metric is not JSON canonicalizable: {exc}") from exc
        return cls(
            proposal_id=proposal_id,
            mission_id=mission_id,
            spec_sha256=spec_sha256,
            metric=dict(metric),
            bundle_sha256=bundle_sha256,
            completed_at=completed_at,
        )
