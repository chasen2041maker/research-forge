"""Run the frozen v0.1 evaluation manifest and write an append-only JSON report."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from time import monotonic


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_MANIFEST = ROOT / "backend" / "evals" / "research_forge_v01_manifest.json"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--output-dir", type=Path, default=ROOT / "artifacts" / "eval-reports")
    parser.add_argument("--repeat", type=int, default=1, help="Multiply each manifest case repeat count.")
    arguments = parser.parse_args()
    if arguments.repeat <= 0:
        raise ValueError("--repeat must be positive")
    manifest_bytes = arguments.manifest.read_bytes()
    manifest = json.loads(manifest_bytes)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    results: list[dict[str, object]] = []
    for case in manifest["cases"]:
        for run_index in range(int(case["repeat"]) * arguments.repeat):
            started = monotonic()
            completed = subprocess.run(
                [sys.executable, "-m", "pytest", str(case["target"]), "-q"],
                cwd=ROOT,
                text=True,
                capture_output=True,
                check=False,
            )
            results.append(
                {
                    "case_id": case["id"],
                    "category": case["category"],
                    "target": case["target"],
                    "run_index": run_index + 1,
                    "passed": completed.returncode == 0,
                    "duration_seconds": round(monotonic() - started, 3),
                    "stdout_tail": completed.stdout[-2000:],
                    "stderr_tail": completed.stderr[-2000:],
                }
            )
    report = {
        "schema_version": 1,
        "suite_id": manifest["suite_id"],
        "manifest_sha256": sha256(manifest_bytes).hexdigest(),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "cases_total": len(results),
        "passed": sum(result["passed"] for result in results),
        "failed": sum(not result["passed"] for result in results),
        "results": results,
    }
    arguments.output_dir.mkdir(parents=True, exist_ok=True)
    output = arguments.output_dir / f"{timestamp}-{report['manifest_sha256'][:12]}.json"
    output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(output)
    return 0 if report["failed"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
