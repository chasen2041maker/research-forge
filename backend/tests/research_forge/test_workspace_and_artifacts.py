"""Git worktree and CAS recovery tests for the first vertical slice."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from subprocess import run

import pytest

from research_forge.adapters.outbound.artifacts import LocalContentAddressedStore
from research_forge.adapters.outbound.git import GitWorktreeManager
from research_forge.adapters.outbound.persistence import InMemoryUnitOfWork
from research_forge.application.use_cases import (
    ClaimBaselineAttempt,
    EnsureBaselineWorkspace,
    PersistArtifact,
)
from research_forge.domain.artifact import ArtifactKind
from research_forge.domain.errors import ArtifactIntegrityViolation
from research_forge.domain.execution import OperationStatus
from research_forge.domain.mission import Attempt, AttemptId, Mission, MissionId, Task, TaskId, TaskType


class _Clock:
    def __init__(self) -> None:
        self.current = datetime(2026, 7, 12, tzinfo=timezone.utc)

    def now(self) -> datetime:
        return self.current


class _Ids:
    def __init__(self) -> None:
        self.counter = 0

    def new(self, kind: str) -> str:
        self.counter += 1
        return f"{kind}-{self.counter}"


def _git(*arguments: str, cwd: Path | None = None) -> str:
    completed = run(["git", *arguments], cwd=cwd, check=True, capture_output=True, text=True)
    return completed.stdout.strip()


def _create_source_repository(root: Path) -> tuple[Path, str]:
    source = root / "source"
    source.mkdir()
    (source / "evaluate.py").write_text(
        "import json\nfrom pathlib import Path\nPath('metrics.json').write_text(json.dumps({'accuracy': 0.8}))\n",
        encoding="utf-8",
    )
    _git("init", cwd=source)
    _git("config", "user.email", "tests@example.invalid", cwd=source)
    _git("config", "user.name", "Research Forge Tests", cwd=source)
    _git("add", "evaluate.py", cwd=source)
    _git("commit", "-m", "fixture", cwd=source)
    return source, _git("rev-parse", "HEAD", cwd=source)


def _seed(uow: InMemoryUnitOfWork, clock: _Clock, repository_path: Path, commit_sha: str) -> None:
    spec = {"repository": {"url_or_path": str(repository_path), "commit_sha": commit_sha}}
    mission = Mission.create(
        mission_id=MissionId("mission-1"),
        spec_sha256="a" * 64,
        normalized_spec_json=json.dumps(spec, sort_keys=True),
        created_at=clock.now(),
    )
    mission.mark_ready()
    task = Task(TaskId("task-1"), mission.mission_id, TaskType.BASELINE_REPRODUCTION, clock.now())
    attempt = Attempt(AttemptId("attempt-1"), task.task_id, 1, 0, clock.now())
    with uow:
        uow.add_mission(mission)
        uow.add_task(task)
        uow.add_attempt(attempt)
        uow.commit()


def test_worktree_manager_creates_a_clean_detached_pinned_baseline(tmp_path: Path) -> None:
    source, commit_sha = _create_source_repository(tmp_path)
    manager = GitWorktreeManager(tmp_path / "workspaces")

    workspace = manager.ensure_baseline(
        mission_id="mission-1",
        repository_url_or_path=str(source),
        expected_commit_sha=commit_sha,
    )
    recovered = manager.ensure_baseline(
        mission_id="mission-1",
        repository_url_or_path=str(source),
        expected_commit_sha=commit_sha,
    )

    assert workspace.commit_sha == commit_sha
    assert recovered.worktree_path == workspace.worktree_path
    assert Path(workspace.worktree_path, "evaluate.py").is_file()


def test_local_cas_deduplicates_and_detects_tampering(tmp_path: Path) -> None:
    cas = LocalContentAddressedStore(tmp_path / "artifacts")

    first = cas.put(b'{"accuracy":0.8}', media_type="application/json")
    second = cas.put(b'{"accuracy":0.8}', media_type="application/json")

    assert first == second
    assert cas.verify(first) is True
    (tmp_path / "artifacts" / "cas" / first.sha256).write_bytes(b"tampered")
    assert cas.verify(first) is False
    with pytest.raises(ArtifactIntegrityViolation):
        cas.read_verified(first)


def test_local_cas_garbage_collects_only_unregistered_orphans(tmp_path: Path) -> None:
    cas = LocalContentAddressedStore(tmp_path / "artifacts")
    registered = cas.put(b"registered", media_type="text/plain")
    orphan = cas.put(b"orphan", media_type="text/plain")

    removed = cas.collect_orphans(
        referenced_sha256={registered.sha256},
        minimum_age=timedelta(seconds=0),
    )

    assert removed == (orphan.sha256,)
    assert cas.verify(registered) is True


def test_cas_rename_before_db_finalize_recovers_without_duplicate_registration(tmp_path: Path) -> None:
    clock = _Clock()
    ids = _Ids()
    uow = InMemoryUnitOfWork()
    source, commit_sha = _create_source_repository(tmp_path)
    _seed(uow, clock, source, commit_sha)
    lease = ClaimBaselineAttempt(unit_of_work=uow, clock=clock, lease_duration=timedelta(seconds=30)).execute(
        attempt_id="attempt-1", owner="worker-a"
    )
    use_case = PersistArtifact(
        unit_of_work=uow,
        artifact_store=LocalContentAddressedStore(tmp_path / "artifacts"),
        clock=clock,
        id_generator=ids,
    )
    payload = b"baseline execution log"

    with pytest.raises(RuntimeError, match="simulated crash"):
        use_case.execute(
            attempt_id="attempt-1",
            owner=lease.owner,
            epoch=lease.epoch,
            expected_version=lease.version,
            idempotency_key="attempt-1:execution-log",
            kind=ArtifactKind.EXECUTION_LOG,
            payload=payload,
            media_type="text/plain",
            target_path="execution.log",
            after_blob_written=lambda: (_ for _ in ()).throw(RuntimeError("simulated crash")),
        )

    prepared = uow.get_operation_by_idempotency_key("attempt-1:execution-log")
    assert prepared is not None and prepared.status is OperationStatus.PREPARED
    recovered = use_case.execute(
        attempt_id="attempt-1",
        owner=lease.owner,
        epoch=lease.epoch,
        expected_version=lease.version,
        idempotency_key="attempt-1:execution-log",
        kind=ArtifactKind.EXECUTION_LOG,
        payload=payload,
        media_type="text/plain",
        target_path="execution.log",
    )

    assert recovered.sha256 == hashlib.sha256(payload).hexdigest()
    assert len(uow.artifacts) == 1
    assert uow.get_operation_by_idempotency_key("attempt-1:execution-log").status is OperationStatus.SUCCEEDED


def test_workspace_creation_is_registered_through_the_operation_ledger(tmp_path: Path) -> None:
    clock = _Clock()
    ids = _Ids()
    source, commit_sha = _create_source_repository(tmp_path)
    uow = InMemoryUnitOfWork()
    _seed(uow, clock, source, commit_sha)
    lease = ClaimBaselineAttempt(unit_of_work=uow, clock=clock, lease_duration=timedelta(seconds=30)).execute(
        attempt_id="attempt-1", owner="worker-a"
    )
    use_case = EnsureBaselineWorkspace(
        unit_of_work=uow,
        workspace_manager=GitWorktreeManager(tmp_path / "workspaces"),
        clock=clock,
        id_generator=ids,
    )

    first = use_case.execute(
        attempt_id="attempt-1",
        owner=lease.owner,
        epoch=lease.epoch,
        expected_version=lease.version,
        idempotency_key="attempt-1:baseline-worktree",
    )
    second = use_case.execute(
        attempt_id="attempt-1",
        owner=lease.owner,
        epoch=lease.epoch,
        expected_version=lease.version,
        idempotency_key="attempt-1:baseline-worktree",
    )

    assert first == second
    operation = uow.get_operation_by_idempotency_key("attempt-1:baseline-worktree")
    assert operation is not None and operation.status is OperationStatus.SUCCEEDED
    assert operation.external_result_ref == first.worktree_path
