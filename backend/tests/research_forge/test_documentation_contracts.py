"""Keep shipped Research Forge documentation and the frozen input contract synchronized."""

from __future__ import annotations

import json
import re
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
SPEC_DOCUMENT = REPOSITORY_ROOT / "docs" / "规范" / "科研复现任务规范_v1.md"
SPEC_SCHEMA = REPOSITORY_ROOT / "docs" / "规范" / "科研复现任务规范_v1.schema.json"
MARKDOWN_LINK = re.compile(r"!?\[[^\]]*\]\(([^)]+)\)")


def test_reproduction_spec_documentation_matches_the_v1_schema() -> None:
    document = SPEC_DOCUMENT.read_text(encoding="utf-8")
    schema = json.loads(SPEC_SCHEMA.read_text(encoding="utf-8"))

    assert document.startswith("# ReproductionSpec v1")
    assert "科研复现任务规范_v1.schema.json" in document
    assert schema["title"] == "Research Forge ReproductionSpec v1"
    assert schema["properties"]["schema_version"]["const"] == 1
    assert "schema_version" in schema["required"]


def test_shipped_markdown_local_links_resolve() -> None:
    documents = [
        REPOSITORY_ROOT / "README.md",
        REPOSITORY_ROOT / "README.zh-CN.md",
        *(REPOSITORY_ROOT / "docs").rglob("*.md"),
    ]
    broken: list[str] = []
    for document in documents:
        for target in _local_targets(document):
            if not (document.parent / target).resolve().exists():
                broken.append(f"{document.relative_to(REPOSITORY_ROOT)} -> {target}")
    assert not broken, "\n".join(broken)


def _local_targets(document: Path) -> tuple[str, ...]:
    targets: list[str] = []
    for match in MARKDOWN_LINK.finditer(document.read_text(encoding="utf-8")):
        target = match.group(1).strip().strip("<>").split("#", maxsplit=1)[0]
        if not target or "://" in target or target.startswith(("mailto:", "data:")):
            continue
        targets.append(target)
    return tuple(targets)
