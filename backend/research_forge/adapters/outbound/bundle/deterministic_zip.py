"""Byte-stable ZIP renderer for a no-LLM Research Bundle."""

from __future__ import annotations

from io import BytesIO
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

from research_forge.application.dto.bundle import BundleBuildInput


class DeterministicZipBundleBuilder:
    """Render required bundle entries in lexicographic order with a fixed ZIP timestamp."""

    def build(self, material: BundleBuildInput) -> bytes:
        entries = {
            "artifacts/execution.log": material.log_payload,
            "artifacts/metrics.json": material.metric_payload,
            "bundle-manifest.json": material.manifest_json.encode("utf-8"),
            "claims.jsonl": material.claims_jsonl.encode("utf-8"),
            "dataset-manifest.json": material.dataset_manifest_json.encode("utf-8"),
            "environment.lock": material.environment_lock_json.encode("utf-8"),
            "evidence.jsonl": material.evidence_jsonl.encode("utf-8"),
            "mission-spec.json": material.normalized_spec_json.encode("utf-8"),
            "report.md": material.report_markdown.encode("utf-8"),
            "reproduce.sh": material.reproduce_script.encode("utf-8"),
            "safe_extract.py": material.safe_extract_script.encode("utf-8"),
            "source.tar": material.source_archive,
        }
        output = BytesIO()
        with ZipFile(output, mode="w", compression=ZIP_DEFLATED, compresslevel=9, strict_timestamps=True) as archive:
            for name in sorted(entries):
                info = ZipInfo(filename=name, date_time=(1980, 1, 1, 0, 0, 0))
                info.compress_type = ZIP_DEFLATED
                info.external_attr = 0o100644 << 16
                archive.writestr(info, entries[name])
        return output.getvalue()
