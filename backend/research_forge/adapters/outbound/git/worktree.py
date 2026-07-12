"""Pinned Git worktree implementation using argv-only Git CLI invocations."""

from __future__ import annotations

from pathlib import Path
from subprocess import CalledProcessError, CompletedProcess, run

from research_forge.application.ports.workspace import BaselineWorkspace
from research_forge.domain.errors import PathSafetyViolation


class WorkspaceError(RuntimeError):
    """Raised when a repository cannot safely produce the requested baseline worktree."""


class GitWorktreeManager:
    """Create a bare mirror and one detached, clean baseline worktree per mission."""

    def __init__(self, workspace_root: Path, *, git_binary: str = "git") -> None:
        self._workspace_root = workspace_root.resolve()
        self._workspace_root.mkdir(parents=True, exist_ok=True)
        self._git_binary = git_binary

    def ensure_baseline(
        self,
        *,
        mission_id: str,
        repository_url_or_path: str,
        expected_commit_sha: str,
    ) -> BaselineWorkspace:
        mission_root = self._mission_root(mission_id)
        mirror = mission_root / "repo.git"
        worktree = mission_root / "worktrees" / "baseline"
        source = Path(repository_url_or_path).resolve()
        if not source.is_dir():
            raise WorkspaceError("VS-001 accepts an existing local repository fixture only.")
        if not mirror.exists():
            mission_root.mkdir(parents=True, exist_ok=True)
            self._run("clone", "--bare", "--no-local", str(source), str(mirror))
        if mirror.is_symlink() or not mirror.is_dir():
            raise PathSafetyViolation("Bare repository path is missing, unsafe, or not a directory.")

        commit_sha = self._run("--git-dir", str(mirror), "rev-parse", f"{expected_commit_sha}^{{commit}}").stdout.strip()
        if commit_sha.lower() != expected_commit_sha.lower():
            raise WorkspaceError("Pinned repository commit does not resolve to the expected full SHA.")

        if worktree.exists():
            if worktree.is_symlink() or not worktree.is_dir():
                raise PathSafetyViolation("Baseline worktree is missing, unsafe, or not a directory.")
            head = self._run("-C", str(worktree), "rev-parse", "HEAD").stdout.strip()
            status = self._run("-C", str(worktree), "status", "--porcelain").stdout
            if head != commit_sha or status:
                raise WorkspaceError("Existing baseline worktree is not the requested clean pinned commit.")
        else:
            worktree.parent.mkdir(parents=True, exist_ok=True)
            self._run("--git-dir", str(mirror), "worktree", "add", "--detach", str(worktree), commit_sha)
        return BaselineWorkspace(
            root_path=str(mission_root),
            worktree_path=str(worktree),
            commit_sha=commit_sha,
        )

    def _mission_root(self, mission_id: str) -> Path:
        mission_path = Path(mission_id)
        if mission_path.name != mission_id or mission_id in {"", ".", ".."}:
            raise PathSafetyViolation("Mission ID cannot contain a filesystem path.")
        root = (self._workspace_root / mission_id).resolve()
        try:
            root.relative_to(self._workspace_root)
        except ValueError as exc:
            raise PathSafetyViolation("Workspace path escapes the configured root.") from exc
        return root

    def _run(self, *arguments: str) -> CompletedProcess[str]:
        command = [self._git_binary, *arguments]
        try:
            return run(command, check=True, capture_output=True, text=True, shell=False)
        except CalledProcessError as exc:
            detail = exc.stderr.strip() or exc.stdout.strip() or "Git command failed."
            raise WorkspaceError(detail) from exc
