"""Immutable content-addressed artifact references and their business registrations."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from research_forge.domain.mission import AttemptId


class ArtifactKind(StrEnum):
    EXECUTION_LOG = "EXECUTION_LOG"
    METRIC = "METRIC"
    BUNDLE = "BUNDLE"
    ENVIRONMENT = "ENVIRONMENT"
    DATASET_MANIFEST = "DATASET_MANIFEST"


@dataclass(frozen=True, slots=True)
class ArtifactRef:
    sha256: str
    size_bytes: int
    media_type: str

    @property
    def uri(self) -> str:
        return f"cas:sha256:{self.sha256}"


@dataclass(frozen=True, slots=True)
class ArtifactRegistration:
    artifact: ArtifactRef
    kind: ArtifactKind
    attempt_id: AttemptId
    operation_id: str
    created_at: datetime
