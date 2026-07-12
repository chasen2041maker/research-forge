"""Pinned-paper, Git-commit, and image allowlist verifier for local VS-001 inputs."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping, Set
from pathlib import Path
from subprocess import CalledProcessError, run


class ReproductionPrerequisiteFailure(ValueError):
    """Raised when an execution reference is absent, drifting, or outside policy."""


class PinnedLocalPrerequisiteVerifier:
    """Verify registered paper bytes, a local repository's exact commit, and an image allowlist."""

    def __init__(
        self,
        *,
        paper_artifacts: Mapping[str, str],
        paper_artifact_paths: Mapping[str, Path] | None = None,
        allowed_image_digests: Set[str],
        git_binary: str = "git",
    ) -> None:
        self._paper_artifacts = {key: value.lower() for key, value in paper_artifacts.items()}
        self._paper_artifact_paths = (
            {key: Path(value).resolve() for key, value in paper_artifact_paths.items()}
            if paper_artifact_paths is not None
            else None
        )
        self._allowed_image_digests = {value.lower() for value in allowed_image_digests}
        self._git_binary = git_binary

    def verify(
        self,
        *,
        paper_artifact_id: str,
        paper_sha256: str,
        repository_url_or_path: str,
        commit_sha: str,
        image_digest: str,
    ) -> None:
        if self._paper_artifacts.get(paper_artifact_id) != paper_sha256.lower():
            raise ReproductionPrerequisiteFailure("Paper Artifact is not registered with the supplied SHA-256.")
        self._verify_registered_paper_bytes(paper_artifact_id, paper_sha256)
        if image_digest.lower() not in self._allowed_image_digests:
            raise ReproductionPrerequisiteFailure("Execution image digest is not in the allowed image policy.")
        repository = Path(repository_url_or_path).resolve()
        if not repository.is_dir():
            raise ReproductionPrerequisiteFailure("VS-001 requires an existing local fixture repository.")
        try:
            actual_commit = run(
                [self._git_binary, "-C", str(repository), "rev-parse", f"{commit_sha}^{{commit}}"],
                check=True,
                capture_output=True,
                text=True,
                shell=False,
            ).stdout.strip()
        except CalledProcessError as exc:
            raise ReproductionPrerequisiteFailure("Pinned repository commit does not exist.") from exc
        if actual_commit.lower() != commit_sha.lower():
            raise ReproductionPrerequisiteFailure("Repository HEAD resolution differs from the frozen full SHA.")

    def _verify_registered_paper_bytes(self, artifact_id: str, expected_sha256: str) -> None:
        if self._paper_artifact_paths is None:
            return
        path = self._paper_artifact_paths.get(artifact_id)
        if path is None or path.is_symlink() or not path.is_file():
            raise ReproductionPrerequisiteFailure("Registered Paper Artifact bytes are missing or unsafe.")
        digest = hashlib.sha256()
        with path.open("rb") as source:
            for chunk in iter(lambda: source.read(1024 * 1024), b""):
                digest.update(chunk)
        if digest.hexdigest() != expected_sha256.lower():
            raise ReproductionPrerequisiteFailure("Registered Paper Artifact bytes do not match the frozen SHA-256.")
