"""Architecture CI guards derived from the accepted layering ADRs."""

from __future__ import annotations

import ast
from collections import defaultdict
from pathlib import Path


PACKAGE_ROOT = Path(__file__).resolve().parents[2] / "research_forge"
BACKEND_ROOT = PACKAGE_ROOT.parent
STUDIO_ROOT = BACKEND_ROOT / "co_scientist"
CONTRACT_ROOT = BACKEND_ROOT / "research_contracts"
GATEWAY_ROOT = BACKEND_ROOT / "research_gateway"
FORBIDDEN_FRAMEWORKS = {"celery", "docker", "fastapi", "langgraph", "pydantic", "redis", "sqlalchemy"}
PLATFORM_IMPORT_OWNERS = {
    "celery": ("adapters/outbound/queue/",),
    "docker": ("adapters/outbound/sandbox/",),
    "fastapi": ("adapters/inbound/api/", "bootstrap/"),
    "langgraph": ("adapters/decision/",),
    "pydantic": ("adapters/inbound/api/",),
    "redis": ("adapters/outbound/queue/", "bootstrap/"),
    "sqlalchemy": ("adapters/outbound/persistence/", "bootstrap/"),
}


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


def _is_owned_by(relative: str, owners: tuple[str, ...]) -> bool:
    return any(relative.startswith(owner) for owner in owners)


def _module_name(path: Path) -> str:
    relative = path.relative_to(PACKAGE_ROOT).with_suffix("")
    parts = list(relative.parts)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(("research_forge", *parts))


def _contains_bare_dict_any(annotation: ast.expr | None) -> bool:
    if not isinstance(annotation, ast.Subscript) or not isinstance(annotation.value, ast.Name):
        return False
    if annotation.value.id not in {"dict", "Dict"} or not isinstance(annotation.slice, ast.Tuple):
        return False
    elements = annotation.slice.elts
    return (
        len(elements) == 2
        and isinstance(elements[0], ast.Name)
        and elements[0].id == "str"
        and isinstance(elements[1], ast.Name)
        and elements[1].id == "Any"
    )


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
            if relative.startswith("adapters/decision/") and (
                imported.startswith("research_forge.adapters.inbound")
                or imported.startswith("research_forge.adapters.outbound")
                or imported.startswith("research_forge.application.ports")
            ):
                violations.append(f"{relative}: Decision adapter imports side-effect dependency {imported}")
            if relative.startswith("adapters/outbound/") and imported.startswith("research_forge.application.") and not (
                imported.startswith("research_forge.application.dto")
                or imported.startswith("research_forge.application.ports")
            ):
                violations.append(f"{relative}: Outbound adapter imports Application implementation {imported}")
            if not relative.startswith("bootstrap/") and imported.startswith("research_forge.bootstrap"):
                violations.append(f"{relative}: imports Bootstrap outside composition root")
            if imported == "subprocess" and not (
                relative.startswith("adapters/outbound/git/") or relative.startswith("adapters/outbound/sandbox/")
            ):
                violations.append(f"{relative}: subprocess is outside Git or Sandbox adapter")
            owner_paths = PLATFORM_IMPORT_OWNERS.get(imported.split(".")[0])
            if owner_paths is not None and not _is_owned_by(relative, owner_paths):
                violations.append(f"{relative}: platform dependency {imported} is outside its adapter boundary")
    assert not violations, "\n".join(violations)


def test_studio_forge_contract_boundary_is_one_way_and_product_neutral() -> None:
    """Keep the shared Contract and handoff from becoming a hidden package-level merge."""
    violations: list[str] = []
    for path in STUDIO_ROOT.rglob("*.py"):
        relative = path.relative_to(BACKEND_ROOT).as_posix()
        for imported in _imports(path):
            if imported == "research_forge" or imported.startswith("research_forge."):
                violations.append(f"{relative}: Studio imports Forge implementation {imported}")

    for path in CONTRACT_ROOT.rglob("*.py"):
        relative = path.relative_to(BACKEND_ROOT).as_posix()
        for imported in _imports(path):
            if imported.startswith("co_scientist") or imported.startswith("research_forge"):
                violations.append(f"{relative}: neutral contract imports product package {imported}")

    for path in GATEWAY_ROOT.rglob("*.py"):
        relative = path.relative_to(BACKEND_ROOT).as_posix()
        for imported in _imports(path):
            forbidden_studio = (
                imported == "co_scientist"
                or imported.startswith("co_scientist.graph")
                or imported.startswith("co_scientist.state")
                or imported.startswith("co_scientist.modules")
            )
            forbidden_forge = imported == "research_forge" or imported.startswith("research_forge.")
            if forbidden_studio or forbidden_forge:
                violations.append(f"{relative}: bridge imports a product implementation {imported}")
    assert not violations, "\n".join(violations)


def test_research_state_stays_frozen_while_handoff_uses_the_public_snapshot() -> None:
    """New product handoff must not add data fields to the legacy graph's ResearchState."""
    state_path = STUDIO_ROOT / "state" / "research_state.py"
    tree = ast.parse(state_path.read_text(encoding="utf-8"), filename=str(state_path))
    state = next(
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == "ResearchState"
    )
    actual = {
        statement.target.id
        for statement in state.body
        if isinstance(statement, ast.AnnAssign) and isinstance(statement.target, ast.Name)
    }
    expected = {
        "raw_question",
        "topic_cards",
        "current_topic_id",
        "pico",
        "papers",
        "rewritten_queries",
        "evidence_access_status",
        "triples",
        "research_gaps",
        "gap_cards",
        "current_gap_id",
        "critiques",
        "meta_decision",
        "decision_card",
        "recalled_memories",
        "experiment_plan",
        "code_artifact",
        "execution_mode",
        "paper_draft",
        "fork_id",
        "parent_fork_id",
        "error_log",
        "metadata",
    }
    assert actual == expected


def test_public_signatures_do_not_expose_bare_dict_any() -> None:
    violations: list[str] = []
    for path in PACKAGE_ROOT.rglob("*.py"):
        relative = _module_path(path)
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            annotations = [argument.annotation for argument in (*node.args.posonlyargs, *node.args.args)]
            annotations.extend(argument.annotation for argument in node.args.kwonlyargs)
            annotations.append(node.args.vararg.annotation if node.args.vararg is not None else None)
            annotations.append(node.args.kwarg.annotation if node.args.kwarg is not None else None)
            annotations.append(node.returns)
            if any(_contains_bare_dict_any(annotation) for annotation in annotations):
                violations.append(f"{relative}:{node.lineno}: public signature exposes dict[str, Any]")
    assert not violations, "\n".join(violations)


def test_internal_module_graph_is_acyclic() -> None:
    modules = {_module_name(path): path for path in PACKAGE_ROOT.rglob("*.py")}
    dependencies: dict[str, set[str]] = defaultdict(set)
    for module, path in modules.items():
        for imported in _imports(path):
            if imported in modules and imported != module:
                dependencies[module].add(imported)

    visiting: list[str] = []
    visited: set[str] = set()
    cycles: list[str] = []

    def visit(module: str) -> None:
        if module in visiting:
            start = visiting.index(module)
            cycles.append(" -> ".join((*visiting[start:], module)))
            return
        if module in visited:
            return
        visiting.append(module)
        for dependency in sorted(dependencies[module]):
            visit(dependency)
        visiting.pop()
        visited.add(module)

    for module in sorted(modules):
        visit(module)
    assert not cycles, "\n".join(cycles)
