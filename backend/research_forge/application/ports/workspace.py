"""Git worktree port for the fixed baseline repository."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True, slots=True)
class BaselineWorkspace:
    root_path: str
    worktree_path: str
    commit_sha: str


class WorkspaceManager(Protocol):
    """Creates or recovers an isolated baseline worktree at one pinned commit."""

    def ensure_baseline(
        self,
        *,
        mission_id: str,
        repository_url_or_path: str,
        expected_commit_sha: str,
    ) -> BaselineWorkspace: ...
