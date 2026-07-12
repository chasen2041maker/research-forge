"""Architecture CI guards derived from the accepted layering ADRs."""

from __future__ import annotations

import ast
from pathlib import Path


PACKAGE_ROOT = Path(__file__).resolve().parents[2] / "research_forge"
FORBIDDEN_FRAMEWORKS = {"celery", "docker", "fastapi", "langgraph", "pydantic", "redis", "sqlalchemy"}


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            names.add(node.module)
    return names


def _module_path(path: Path) -> str:
    return path.relative_to(PACKAGE_ROOT).as_posix()


def _is_framework_import(name: str) -> bool:
    return name.split(".")[0] in FORBIDDEN_FRAMEWORKS


def test_architecture_import_contracts() -> None:
    violations: list[str] = []
    for path in PACKAGE_ROOT.rglob("*.py"):
        relative = _module_path(path)
        imports = _imports(path)
        for imported in imports:
            if imported == "co_scientist" or imported.startswith("co_scientist."):
                violations.append(f"{relative}: imports legacy package {imported}")
            if relative.startswith("domain/") and (
                imported.startswith("research_forge.application")
                or imported.startswith("research_forge.adapters")
                or imported.startswith("research_forge.bootstrap")
                or _is_framework_import(imported)
            ):
                violations.append(f"{relative}: Domain imports forbidden dependency {imported}")
            if relative.startswith("application/") and (
                imported.startswith("research_forge.adapters")
                or imported.startswith("research_forge.bootstrap")
                or _is_framework_import(imported)
            ):
                violations.append(f"{relative}: Application imports forbidden dependency {imported}")
            if relative.startswith("adapters/inbound/") and imported.startswith("research_forge.adapters.outbound"):
                violations.append(f"{relative}: Inbound adapter imports outbound adapter {imported}")
            if not relative.startswith("bootstrap/") and imported.startswith("research_forge.bootstrap"):
                violations.append(f"{relative}: imports Bootstrap outside composition root")
            if imported == "subprocess" and not (
                relative.startswith("adapters/outbound/git/") or relative.startswith("adapters/outbound/sandbox/")
            ):
                violations.append(f"{relative}: subprocess is outside Git or Sandbox adapter")
    assert not violations, "\n".join(violations)
