"""Public evidence-gate entities and deterministic metric validation."""

from research_forge.domain.evidence.metric import (
    MetricComparator,
    MetricExpectation,
    MetricValidation,
    extract_and_validate_metric,
)
from research_forge.domain.evidence.model import (
    Claim,
    ClaimStatus,
    ClaimType,
    EvidenceLink,
    EvidenceType,
    MetricRecord,
    VerifiedClaimView,
)

__all__ = [
    "Claim",
    "ClaimStatus",
    "ClaimType",
    "EvidenceLink",
    "EvidenceType",
    "MetricExpectation",
    "MetricComparator",
    "MetricRecord",
    "MetricValidation",
    "VerifiedClaimView",
    "extract_and_validate_metric",
]
