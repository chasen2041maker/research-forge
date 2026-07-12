"""Ensure the release suite remains a complete, frozen, executable 16-case manifest."""

from __future__ import annotations

import json
from pathlib import Path


MANIFEST = Path(__file__).resolve().parents[2] / "evals" / "research_forge_v01_manifest.json"


def test_frozen_eval_manifest_has_sixteen_unique_executable_cases() -> None:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    cases = manifest["cases"]
    assert manifest["schema_version"] == 1
    assert len(cases) == 16
    assert len({case["id"] for case in cases}) == 16
    assert {case["category"] for case in cases} == {"runtime", "security-approval", "evidence", "git-artifact"}
    assert all(case["repeat"] >= 1 and "::test_" in case["target"] for case in cases)
    assert all((Path(__file__).resolve().parents[3] / case["target"].split("::", 1)[0]).is_file() for case in cases)
