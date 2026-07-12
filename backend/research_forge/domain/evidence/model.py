"""Claim, evidence, and metric entities for the no-LLM evidence gate."""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from research_forge.domain.artifact import ArtifactRef
from research_forge.domain.mission import AttemptId, MissionId


class ClaimStatus(StrEnum):
    CANDIDATE = "CANDIDATE"
    VERIFIED = "VERIFIED"
    CONFLICTED = "CONFLICTED"
    UNSUPPORTED = "UNSUPPORTED"


class ClaimType(StrEnum):
    EXPERIMENT_RESULT = "EXPERIMENT_RESULT"


class EvidenceType(StrEnum):
    METRIC_ARTIFACT = "METRIC_ARTIFACT"
    EXECUTION_LOG = "EXECUTION_LOG"
    ENVIRONMENT_MANIFEST = "ENVIRONMENT_MANIFEST"
    DATASET_MANIFEST = "DATASET_MANIFEST"


@dataclass(frozen=True, slots=True)
class MetricRecord:
    metric_id: str
    attempt_id: AttemptId
    artifact: ArtifactRef
    json_pointer: str
    value: float
    comparator: str
    expected_value: float
    tolerance: float
    unit: str
    commit_sha: str
    command: tuple[str, ...]
    environment_digest: str
    dataset_sha256: str

    def __post_init__(self) -> None:
        if not math.isfinite(self.value):
            raise ValueError("Metric record value must be finite.")
        if len(self.commit_sha) != 40 or len(self.dataset_sha256) != 64:
            raise ValueError("Metric record must link a full commit and dataset digest.")
        if not self.command or not self.environment_digest:
            raise ValueError("Metric record must link its command and environment.")


@dataclass(frozen=True, slots=True)
class Claim:
    claim_id: str
    mission_id: MissionId
    attempt_id: AttemptId
    claim_type: ClaimType
    status: ClaimStatus
    statement: str
    created_at: datetime


@dataclass(frozen=True, slots=True)
class EvidenceLink:
    claim_id: str
    evidence_type: EvidenceType
    artifact: ArtifactRef


@dataclass(frozen=True, slots=True)
class VerifiedClaimView:
    """A deliberately small writer-facing view that excludes Mission and workspace state."""

    claim_id: str
    statement: str
    metric_value: float
    unit: str
    metric_artifact_sha256: str

    @classmethod
    def from_claim(cls, claim: Claim, metric: MetricRecord, evidence: tuple[EvidenceLink, ...]) -> "VerifiedClaimView":
        if claim.status is not ClaimStatus.VERIFIED:
            raise ValueError("Only VERIFIED claims can construct a VerifiedClaimView.")
        has_metric = any(link.evidence_type is EvidenceType.METRIC_ARTIFACT for link in evidence)
        if not has_metric:
            raise ValueError("Verified numeric claims require Metric Artifact evidence.")
        return cls(
            claim_id=claim.claim_id,
            statement=claim.statement,
            metric_value=metric.value,
            unit=metric.unit,
            metric_artifact_sha256=metric.artifact.sha256,
        )
