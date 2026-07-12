"""Git worktree port for the fixed baseline repository."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from research_forge.application.dto.repair import CandidateCommit, CandidateCommitRequest

@dataclass(frozen=True, slots=True)
class BaselineWorkspace:
    root_path: str
    worktree_path: str
    commit_sha: str


@dataclass(frozen=True, slots=True)
class CandidateWorkspace:
    root_path: str
    worktree_path: str
    parent_commit_sha: str


class WorkspaceManager(Protocol):
    """Creates or recovers an isolated baseline worktree at one pinned commit."""

    def ensure_baseline(
        self,
        *,
        mission_id: str,
        repository_url_or_path: str,
        expected_commit_sha: str,
    ) -> BaselineWorkspace: ...

    def archive_baseline(self, worktree_path: str) -> bytes: ...

    def ensure_candidate(
        self,
        *,
        mission_id: str,
        expected_parent_commit_sha: str,
    ) -> CandidateWorkspace: ...

    def commit_candidate(self, request: CandidateCommitRequest) -> CandidateCommit: ...
