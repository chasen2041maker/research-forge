"""Security gates for secret canaries, path traversal, and bundle archive extraction."""

from __future__ import annotations

import io
import tarfile
from pathlib import Path
from subprocess import run

import pytest

from research_forge.adapters.outbound.sandbox import DockerSandboxBroker, LocalDevelopmentSandbox
from research_forge.application.dto import NetworkPolicy, SandboxRunRequest
from research_forge.application.use_cases.complete_reproduction_mission import CompleteReproductionMission
from research_forge.domain.errors import PathSafetyViolation


def _request(worktree: Path, *, working_directory: str = ".") -> SandboxRunRequest:
    return SandboxRunRequest(
        operation_id="operation-1",
        image_digest="sha256:" + "a" * 64,
        argv=("python", "evaluate.py"),
        worktree_path=str(worktree),
        working_directory=working_directory,
        timeout_seconds=30,
        max_log_bytes=1024,
        network_policy=NetworkPolicy.OFFLINE,
        expected_output_paths=("metrics.json",),
    )


def test_development_runner_does_not_leak_an_unrelated_host_secret(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    secret = "rf-canary-should-not-leak"
    monkeypatch.setenv("RF_CANARY", secret)
    workspace_root = tmp_path / "workspaces"
    worktree = workspace_root / "mission-1" / "worktrees" / "baseline"
    worktree.mkdir(parents=True)
    (worktree / "evaluate.py").write_text(
        "import json, os\n"
        "from pathlib import Path\n"
        "value = os.environ.get('RF_CANARY', '')\n"
        "print(value)\n"
        "Path('metrics.json').write_text(json.dumps({'secret_present': bool(value)}))\n",
        encoding="utf-8",
    )

    result = LocalDevelopmentSandbox(workspace_root).execute(_request(worktree))

    assert secret.encode() not in result.stdout
    assert b'"secret_present": false' in result.output_files["metrics.json"]


def test_sandbox_rejects_working_directory_path_escape(tmp_path: Path) -> None:
    workspace_root = tmp_path / "workspaces"
    worktree = workspace_root / "mission-1" / "worktrees" / "baseline"
    worktree.mkdir(parents=True)
    broker = DockerSandboxBroker(
        workspace_root=workspace_root,
        allowed_images={"sha256:" + "a" * 64: "python@sha256:" + "a" * 64},
    )

    with pytest.raises(PathSafetyViolation):
        broker.build_command(_request(worktree, working_directory="../../outside"))


def test_bundle_safe_extractor_rejects_archive_path_escape(tmp_path: Path) -> None:
    archive_path = tmp_path / "malicious.tar"
    with tarfile.open(archive_path, "w") as archive:
        member = tarfile.TarInfo("../../escaped.txt")
        payload = b"unsafe"
        member.size = len(payload)
        archive.addfile(member, io.BytesIO(payload))
    extractor = tmp_path / "safe_extract.py"
    extractor.write_text(CompleteReproductionMission._safe_extract_script(), encoding="utf-8")

    result = run(
        ["python", str(extractor), str(archive_path), str(tmp_path / "destination")],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode != 0
    assert not (tmp_path.parent / "escaped.txt").exists()
