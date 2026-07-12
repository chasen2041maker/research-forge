"""Typed contracts between baseline orchestration and the sandbox boundary."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Mapping


class NetworkPolicy(StrEnum):
    OFFLINE = "offline"


@dataclass(frozen=True, slots=True)
class SandboxRunRequest:
    operation_id: str
    image_digest: str
    argv: tuple[str, ...]
    worktree_path: str
    working_directory: str
    timeout_seconds: int
    max_log_bytes: int
    network_policy: NetworkPolicy
    expected_output_paths: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class SandboxResult:
    operation_id: str
    execution_id: str
    exit_code: int
    stdout: bytes
    stderr: bytes
    output_files: Mapping[str, bytes]
    environment_digest: str
    dataset_sha256: str
    logs_truncated: bool = False
