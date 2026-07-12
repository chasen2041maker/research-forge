"""External truth checks required before an accepted spec creates a Mission."""

from __future__ import annotations

from typing import Protocol


class ReproductionPrerequisiteVerifier(Protocol):
    """Verify frozen paper, repository, and image references at the adapter boundary."""

    def verify(
        self,
        *,
        paper_artifact_id: str,
        paper_sha256: str,
        repository_url_or_path: str,
        commit_sha: str,
        image_digest: str,
    ) -> None: ...
