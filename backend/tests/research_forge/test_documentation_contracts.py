"""Keep shipped Research Forge documentation and the frozen input contract synchronized."""

from __future__ import annotations

import json
import re
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
DOCS_ROOT = REPOSITORY_ROOT / "docs"
SPEC_DOCUMENT = DOCS_ROOT / "contracts" / "reproduction-spec-v1.md"
SPEC_SCHEMA = DOCS_ROOT / "contracts" / "reproduction-spec-v1.schema.json"
PROPOSAL_SCHEMA = DOCS_ROOT / "contracts" / "research-proposal-v1.schema.json"
IMPLEMENTATION_SCHEMA = REPOSITORY_ROOT / "backend" / "research_contracts" / "research_proposal_v1.schema.json"
ADR_INDEX = DOCS_ROOT / "adr" / "README.md"
CAPABILITY_PROFILE = DOCS_ROOT / "contracts" / "runtime-capability-profile-v0.1.md"
IMPLEMENTATION_STATUS = DOCS_ROOT / "architecture" / "implementation-status.yaml"
MARKDOWN_LINK = re.compile(r"!?\[[^\]]*\]\(([^)]+)\)")


def test_reproduction_spec_documentation_matches_the_v1_schema() -> None:
    document = SPEC_DOCUMENT.read_text(encoding="utf-8")
    schema = json.loads(SPEC_SCHEMA.read_text(encoding="utf-8"))

    assert document.startswith("---\n")
    assert "# ReproductionSpec v1" in document
    assert "reproduction-spec-v1.schema.json" in document
    assert schema["title"] == "Research Forge ReproductionSpec v1"
    assert schema["properties"]["schema_version"]["const"] == 1
    assert "schema_version" in schema["required"]


def test_shipped_markdown_local_links_resolve() -> None:
    documents = [
        REPOSITORY_ROOT / "README.md",
        REPOSITORY_ROOT / "README.zh-CN.md",
        *DOCS_ROOT.rglob("*.md"),
    ]
    broken: list[str] = []
    for document in documents:
        for target in _local_targets(document):
            if not (document.parent / target).resolve().exists():
                broken.append(f"{document.relative_to(REPOSITORY_ROOT)} -> {target}")
    assert not broken, "\n".join(broken)


def test_active_documents_have_status_front_matter_and_history_is_labeled() -> None:
    documents = tuple(DOCS_ROOT.rglob("*.md"))
    missing: list[str] = []
    for document in documents:
        text = document.read_text(encoding="utf-8")
        relative = document.relative_to(DOCS_ROOT)
        if not text.startswith("---\n") or "\nstatus: " not in text.split("---", maxsplit=2)[1]:
            missing.append(relative.as_posix())
        if "history" in relative.parts and "status: historical" not in text:
            missing.append(f"{relative.as_posix()} is not marked historical")
    assert not missing, "\n".join(missing)


def test_documented_product_contracts_and_adr_index_are_synchronized() -> None:
    proposal_schema = json.loads(PROPOSAL_SCHEMA.read_text(encoding="utf-8"))
    implementation_schema = json.loads(IMPLEMENTATION_SCHEMA.read_text(encoding="utf-8"))
    assert proposal_schema == implementation_schema
    assert proposal_schema["properties"]["status"]["const"] == "UNVERIFIED"

    adr_index = ADR_INDEX.read_text(encoding="utf-8")
    for number in range(1, 8):
        assert f"000{number}" in adr_index


def test_capability_profile_and_status_matrix_do_not_overclaim_runtime() -> None:
    profile = CAPABILITY_PROFILE.read_text(encoding="utf-8")
    status = IMPLEMENTATION_STATUS.read_text(encoding="utf-8")
    for phrase in ("lockfile", "allowlisted", "in-process", "max_cost_usd"):
        assert phrase in profile or phrase in status
    assert "operation_ledger_and_stale_redelivery:" in status
    assert "status: partial" in status


def _local_targets(document: Path) -> tuple[str, ...]:
    targets: list[str] = []
    for match in MARKDOWN_LINK.finditer(document.read_text(encoding="utf-8")):
        target = match.group(1).strip().strip("<>").split("#", maxsplit=1)[0]
        if not target or "://" in target or target.startswith(("mailto:", "data:")):
            continue
        targets.append(target)
    return tuple(targets)
