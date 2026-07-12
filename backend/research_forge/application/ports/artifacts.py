"""Content-addressed byte storage port."""

from __future__ import annotations

from typing import Protocol

from research_forge.domain.artifact import ArtifactRef


class ArtifactStore(Protocol):
    """Stores immutable bytes; business registration remains in the UoW."""

    def put(self, payload: bytes, *, media_type: str) -> ArtifactRef: ...

    def read_verified(self, reference: ArtifactRef) -> bytes: ...

    def verify(self, reference: ArtifactRef) -> bool: ...
