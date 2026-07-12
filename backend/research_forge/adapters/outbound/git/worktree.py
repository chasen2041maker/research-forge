"""Pinned Git worktree implementation using argv-only Git CLI invocations."""

from __future__ import annotations

from pathlib import Path, PurePosixPath
from subprocess import CalledProcessError, CompletedProcess, run

from research_forge.application.dto.repair import CandidateCommit, CandidateCommitRequest
from research_forge.application.ports.workspace import BaselineWorkspace, CandidateWorkspace
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

    def archive_baseline(self, worktree_path: str) -> bytes:
        worktree = Path(worktree_path).resolve()
        try:
            worktree.relative_to(self._workspace_root)
        except ValueError as exc:
            raise PathSafetyViolation("Baseline archive path escapes the workspace root.") from exc
        if worktree.is_symlink() or not worktree.is_dir():
            raise PathSafetyViolation("Baseline archive worktree is missing or unsafe.")
        try:
            return run(
                [self._git_binary, "-C", str(worktree), "archive", "--format=tar", "HEAD"],
                check=True,
                capture_output=True,
                shell=False,
            ).stdout
        except CalledProcessError as exc:
            raise WorkspaceError("Unable to archive the pinned baseline worktree.") from exc

    def ensure_candidate(
        self,
        *,
        mission_id: str,
        expected_parent_commit_sha: str,
    ) -> CandidateWorkspace:
        mission_root = self._mission_root(mission_id)
        mirror = mission_root / "repo.git"
        worktree = mission_root / "worktrees" / "candidate"
        if mirror.is_symlink() or not mirror.is_dir():
            raise PathSafetyViolation("Candidate worktree requires a registered bare repository mirror.")
        parent_sha = self._run(
            "--git-dir", str(mirror), "rev-parse", f"{expected_parent_commit_sha}^{{commit}}"
        ).stdout.strip()
        if parent_sha.lower() != expected_parent_commit_sha.lower():
            raise WorkspaceError("Candidate parent does not resolve to the requested full commit SHA.")
        if worktree.exists():
            if worktree.is_symlink() or not worktree.is_dir():
                raise PathSafetyViolation("Candidate worktree is missing, unsafe, or not a directory.")
            head = self._run("-C", str(worktree), "rev-parse", "HEAD").stdout.strip()
            status = self._run("-C", str(worktree), "status", "--porcelain").stdout
            if head != parent_sha or status:
                raise WorkspaceError("Existing candidate worktree is not a clean requested parent commit.")
        else:
            worktree.parent.mkdir(parents=True, exist_ok=True)
            self._run("--git-dir", str(mirror), "worktree", "add", "--detach", str(worktree), parent_sha)
        return CandidateWorkspace(
            root_path=str(mission_root),
            worktree_path=str(worktree),
            parent_commit_sha=parent_sha,
        )

    def recover_candidate(
        self,
        *,
        mission_id: str,
        operation_id: str,
        expected_parent_commit_sha: str,
    ) -> CandidateWorkspace:
        mission_root = self._mission_root(mission_id)
        mirror = mission_root / "repo.git"
        worktree = mission_root / "worktrees" / "candidate"
        if not worktree.exists():
            return self.ensure_candidate(
                mission_id=mission_id,
                expected_parent_commit_sha=expected_parent_commit_sha,
            )
        if mirror.is_symlink() or not mirror.is_dir() or worktree.is_symlink() or not worktree.is_dir():
            raise PathSafetyViolation("Candidate recovery path is missing or unsafe.")
        if self._run("-C", str(worktree), "status", "--porcelain").stdout:
            raise WorkspaceError("Candidate recovery requires a clean registered worktree.")
        head = self._run("-C", str(worktree), "rev-parse", "HEAD").stdout.strip()
        if head != expected_parent_commit_sha:
            parent = self._run("-C", str(worktree), "rev-parse", f"{head}^{{commit}}^").stdout.strip()
            if parent != expected_parent_commit_sha or not self._commit_has_operation_trailer(worktree, head, operation_id):
                raise WorkspaceError("Candidate worktree cannot be safely recovered for this operation.")
        return CandidateWorkspace(
            root_path=str(mission_root),
            worktree_path=str(worktree),
            parent_commit_sha=expected_parent_commit_sha,
        )

    def commit_candidate(self, request: CandidateCommitRequest) -> CandidateCommit:
        worktree, mirror = self._candidate_paths(request.worktree_path)
        existing = self._operation_commit(mirror, request.operation_id)
        if existing is not None:
            return self._candidate_commit_view(worktree, existing)
        current_head = self._run("-C", str(worktree), "rev-parse", "HEAD").stdout.strip()
        if current_head != request.expected_parent_sha:
            if self._commit_has_operation_trailer(worktree, current_head, request.operation_id):
                self._publish_operation_ref(mirror, request.operation_id, current_head)
                return self._candidate_commit_view(worktree, current_head)
            raise WorkspaceError("Candidate worktree no longer points to the expected parent commit.")
        if self._run("-C", str(worktree), "status", "--porcelain").stdout:
            raise WorkspaceError("Candidate worktree must be clean before a proposal patch is applied.")
        patch_applied = False
        commit_created = False
        try:
            self._run_with_input(
                request.unified_diff,
                "-C",
                str(worktree),
                "apply",
                "--check",
                "--whitespace=error",
            )
            self._run_with_input(
                request.unified_diff,
                "-C",
                str(worktree),
                "apply",
                "--whitespace=error",
            )
            patch_applied = True
            changed_paths, changed_lines = self._validate_candidate_diff(worktree, request)
            self._run("-C", str(worktree), "add", "--", *changed_paths)
            message = (
                "Research Forge bounded repair candidate\n\n"
                f"Research-Forge-Operation: {request.operation_id}\n"
                f"Research-Forge-Input-Hash: {request.input_hash}"
            )
            self._run(
                "-C",
                str(worktree),
                "-c",
                "user.name=Research Forge",
                "-c",
                "user.email=research-forge@localhost",
                "commit",
                "--no-gpg-sign",
                "--no-verify",
                "-m",
                message,
            )
            commit_created = True
            commit_sha = self._run("-C", str(worktree), "rev-parse", "HEAD").stdout.strip()
            self._publish_operation_ref(mirror, request.operation_id, commit_sha)
            return CandidateCommit(
                commit_sha=commit_sha,
                changed_paths=changed_paths,
                changed_lines=changed_lines,
            )
        except Exception:
            if patch_applied and not commit_created:
                self._run("-C", str(worktree), "reset", "--hard", request.expected_parent_sha)
                self._run("-C", str(worktree), "clean", "-fd")
            raise

    def _candidate_paths(self, raw_worktree_path: str) -> tuple[Path, Path]:
        worktree = Path(raw_worktree_path).resolve()
        try:
            relative = worktree.relative_to(self._workspace_root)
        except ValueError as exc:
            raise PathSafetyViolation("Candidate worktree path escapes the configured root.") from exc
        if len(relative.parts) != 3 or relative.parts[1:] != ("worktrees", "candidate"):
            raise PathSafetyViolation("Candidate commits may only target the registered candidate worktree.")
        if worktree.is_symlink() or not worktree.is_dir():
            raise PathSafetyViolation("Candidate worktree is missing or unsafe.")
        mirror = self._workspace_root / relative.parts[0] / "repo.git"
        if mirror.is_symlink() or not mirror.is_dir():
            raise PathSafetyViolation("Candidate repository mirror is missing or unsafe.")
        return worktree, mirror

    def _validate_candidate_diff(
        self, worktree: Path, request: CandidateCommitRequest
    ) -> tuple[tuple[str, ...], int]:
        names = tuple(
            line for line in self._run("-C", str(worktree), "diff", "--name-only", "--no-renames").stdout.splitlines() if line
        )
        if not names:
            raise WorkspaceError("Candidate proposal must produce a non-empty diff.")
        if len(names) > request.max_files:
            raise WorkspaceError("Candidate proposal exceeds the allowed changed-file budget.")
        for name in names:
            self._validate_changed_path(name, request.allowed_paths)
        changed_lines = 0
        for line in self._run("-C", str(worktree), "diff", "--numstat", "--no-renames").stdout.splitlines():
            parts = line.split("\t", maxsplit=2)
            if len(parts) != 3 or not parts[0].isdigit() or not parts[1].isdigit():
                raise WorkspaceError("Binary or malformed candidate diffs are not permitted.")
            changed_lines += int(parts[0]) + int(parts[1])
        if changed_lines > request.max_changed_lines:
            raise WorkspaceError("Candidate proposal exceeds the allowed changed-line budget.")
        return names, changed_lines

    @staticmethod
    def _validate_changed_path(path: str, allowed_paths: tuple[str, ...]) -> None:
        candidate = PurePosixPath(path)
        if candidate.is_absolute() or ".." in candidate.parts or "\t" in path:
            raise PathSafetyViolation("Candidate diff contains an unsafe repository path.")
        allowed = any(path == entry or path.startswith(f"{entry.rstrip('/')}/") for entry in allowed_paths)
        if not allowed:
            raise WorkspaceError(f"Candidate patch changes disallowed path: {path}")

    def _operation_commit(self, mirror: Path, operation_id: str) -> str | None:
        ref = self._operation_ref(operation_id)
        result = run(
            [self._git_binary, "--git-dir", str(mirror), "rev-parse", "--verify", "--quiet", ref],
            check=False,
            capture_output=True,
            text=True,
            shell=False,
        )
        return result.stdout.strip() if result.returncode == 0 else None

    def _publish_operation_ref(self, mirror: Path, operation_id: str, commit_sha: str) -> None:
        ref = self._operation_ref(operation_id)
        current = self._operation_commit(mirror, operation_id)
        if current is not None:
            if current != commit_sha:
                raise WorkspaceError("Operation ref already points to a conflicting candidate commit.")
            return
        self._run(
            "--git-dir",
            str(mirror),
            "update-ref",
            ref,
            commit_sha,
            "0" * 40,
        )

    @staticmethod
    def _operation_ref(operation_id: str) -> str:
        if not operation_id or any(character not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_." for character in operation_id):
            raise PathSafetyViolation("Operation ID is unsafe for a Git ref namespace.")
        return f"refs/research-forge/operations/{operation_id}"

    def _candidate_commit_view(self, worktree: Path, commit_sha: str) -> CandidateCommit:
        names = tuple(
            line
            for line in self._run("-C", str(worktree), "show", "--format=", "--name-only", "--no-renames", commit_sha).stdout.splitlines()
            if line
        )
        changed_lines = 0
        for line in self._run("-C", str(worktree), "show", "--format=", "--numstat", "--no-renames", commit_sha).stdout.splitlines():
            parts = line.split("\t", maxsplit=2)
            if len(parts) == 3 and parts[0].isdigit() and parts[1].isdigit():
                changed_lines += int(parts[0]) + int(parts[1])
        return CandidateCommit(commit_sha=commit_sha, changed_paths=names, changed_lines=changed_lines)

    def _commit_has_operation_trailer(self, worktree: Path, commit_sha: str, operation_id: str) -> bool:
        message = self._run("-C", str(worktree), "show", "-s", "--format=%B", commit_sha).stdout
        return f"Research-Forge-Operation: {operation_id}" in message

    def _run(self, *arguments: str) -> CompletedProcess[str]:
        command = [self._git_binary, *arguments]
        try:
            return run(command, check=True, capture_output=True, text=True, shell=False)
        except CalledProcessError as exc:
            detail = exc.stderr.strip() or exc.stdout.strip() or "Git command failed."
            raise WorkspaceError(detail) from exc

    def _run_with_input(self, payload: str, *arguments: str) -> CompletedProcess[str]:
        command = [self._git_binary, *arguments]
        try:
            return run(command, input=payload, check=True, capture_output=True, text=True, shell=False)
        except CalledProcessError as exc:
            detail = exc.stderr.strip() or exc.stdout.strip() or "Git patch operation failed."
            raise WorkspaceError(detail) from exc
