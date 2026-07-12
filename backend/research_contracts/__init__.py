"""Versioned, product-neutral contracts shared by Research Studio and Forge."""

from research_contracts.research_proposal_v1 import (
    ResearchProposalV1,
    ResearchProposalValidationError,
)
from research_contracts.verified_result_v1 import (
    VerifiedResultV1,
    VerifiedResultValidationError,
)

__all__ = [
    "ResearchProposalV1",
    "ResearchProposalValidationError",
    "VerifiedResultV1",
    "VerifiedResultValidationError",
]
