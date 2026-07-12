"""Pure input to the deterministic no-LLM Research Bundle renderer."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class BundleBuildInput:
    manifest_json: str
    normalized_spec_json: str
    environment_lock_json: str
    dataset_manifest_json: str
    claims_jsonl: str
    evidence_jsonl: str
    report_markdown: str
    reproduce_script: str
    safe_extract_script: str
    metric_payload: bytes
    log_payload: bytes
    source_archive: bytes
