"""Run the three deterministic Research Forge product demonstrations and write one JSON report."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from time import monotonic


ROOT = Path(__file__).resolve().parents[2]
DEMONSTRATIONS = (
    (
        "studio-to-forge-handoff",
        "Studio UNVERIFIED proposal plus human completion reaches the Forge handoff API boundary.",
        "backend/tests/research_integration/test_studio_forge_handoff.py::"
        "test_forge_handoff_endpoint_calls_the_existing_mission_creation_boundary",
    ),
    (
        "forge-to-studio-verified-result",
        "Only completed Bundle, Metric, and VERIFIED claims can become a Studio read-only result.",
        "backend/tests/research_integration/test_verified_result_loop.py::"
        "test_forge_emits_verified_result_and_studio_projects_only_its_facts",
    ),
    (
        "bounded-repair-approval-loop",
        "A verified failure produces one persisted PATCH artifact, approval, candidate run, and Bundle.",
        "backend/tests/research_forge/test_repair_flow.py::"
        "test_repair_runs_exactly_one_budgeted_candidate_after_a_failed_baseline",
    ),
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=ROOT / "artifacts" / "demo-reports")
    arguments = parser.parse_args()
    results: list[dict[str, object]] = []
    for demo_id, description, target in DEMONSTRATIONS:
        started = monotonic()
        completed = subprocess.run(
            [sys.executable, "-m", "pytest", target, "-q"],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
        )
        results.append(
            {
                "id": demo_id,
                "description": description,
                "target": target,
                "passed": completed.returncode == 0,
                "duration_seconds": round(monotonic() - started, 3),
                "stdout_tail": completed.stdout[-2000:],
                "stderr_tail": completed.stderr[-2000:],
            }
        )
    report = {
        "schema_version": 1,
        "suite": "research-forge-product-demos",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "passed": sum(result["passed"] is True for result in results),
        "failed": sum(result["passed"] is not True for result in results),
        "results": results,
    }
    arguments.output_dir.mkdir(parents=True, exist_ok=True)
    output = arguments.output_dir / f"{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}-demos.json"
    output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(output)
    return 0 if report["failed"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
