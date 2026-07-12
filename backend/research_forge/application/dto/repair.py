"""Typed proposal and candidate-commit contracts for the single bounded repair slice."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class DecisionRequest:
    mission_id: str
    spec_sha256: str
    baseline_log: str
    allowed_paths: tuple[str, ...]
    max_files: int
    max_changed_lines: int


@dataclass(frozen=True, slots=True)
class ActionProposal:
    """An untrusted decision artifact; Application policy must validate it before execution."""

    action_type: str
    unified_diff: str
    rationale_summary: str
    expected_artifacts: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class CandidateCommitRequest:
    worktree_path: str
    unified_diff: str
    allowed_paths: tuple[str, ...]
    max_files: int
    max_changed_lines: int
    operation_id: str
    input_hash: str
    expected_parent_sha: str


@dataclass(frozen=True, slots=True)
class CandidateCommit:
    commit_sha: str
    changed_paths: tuple[str, ...]
    changed_lines: int
