"""Registered paper bytes are part of the immutable Mission prerequisite boundary."""

from __future__ import annotations

from hashlib import sha256
from pathlib import Path
from subprocess import run

import pytest

from research_forge.adapters.outbound.git.prerequisites import (
    PinnedLocalPrerequisiteVerifier,
    ReproductionPrerequisiteFailure,
)


def _repository(root: Path) -> tuple[Path, str]:
    repository = root / "repository"
    repository.mkdir()
    (repository / "evaluate.py").write_text("print('ok')\n", encoding="utf-8")
    for arguments in (("init",), ("config", "user.email", "tests@example.invalid"), ("config", "user.name", "Tests"), ("add", "."), ("commit", "-m", "fixture")):
        run(["git", *arguments], cwd=repository, check=True, capture_output=True, text=True)
    commit = run(["git", "rev-parse", "HEAD"], cwd=repository, check=True, capture_output=True, text=True).stdout.strip()
    return repository, commit


def test_registered_paper_bytes_must_match_the_frozen_hash(tmp_path: Path) -> None:
    paper = tmp_path / "paper.pdf"
    paper.write_bytes(b"frozen paper v1")
    paper_sha256 = sha256(paper.read_bytes()).hexdigest()
    repository, commit_sha = _repository(tmp_path)
    verifier = PinnedLocalPrerequisiteVerifier(
        paper_artifacts={"paper-1": paper_sha256},
        paper_artifact_paths={"paper-1": paper},
        allowed_image_digests={"sha256:" + "a" * 64},
    )

    verifier.verify(
        paper_artifact_id="paper-1",
        paper_sha256=paper_sha256,
        repository_url_or_path=str(repository),
        commit_sha=commit_sha,
        image_digest="sha256:" + "a" * 64,
    )
    paper.write_bytes(b"tampered paper")

    with pytest.raises(ReproductionPrerequisiteFailure, match="bytes do not match"):
        verifier.verify(
            paper_artifact_id="paper-1",
            paper_sha256=paper_sha256,
            repository_url_or_path=str(repository),
            commit_sha=commit_sha,
            image_digest="sha256:" + "a" * 64,
        )
