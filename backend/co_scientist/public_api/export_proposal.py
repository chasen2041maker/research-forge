"""Translate a completed Studio snapshot into the neutral ResearchProposal v1 contract."""

from __future__ import annotations

from collections.abc import Mapping

from co_scientist.public_api.models import ExplorationSnapshot
from research_contracts import ResearchProposalV1


_REQUIRED_FORGE_FIELDS = (
    "paper.artifact_id",
    "paper.sha256",
    "paper.extraction_profile",
    "repository.url_or_path",
    "repository.commit_sha",
    "execution.image_digest",
    "execution.setup_mode",
    "execution.setup_argv",
    "execution.run_argv",
    "execution.working_directory",
    "execution.timeout_seconds",
    "metric.artifact_path",
    "metric.json_pointer",
    "metric.comparator",
    "metric.expected_value",
    "metric.tolerance",
    "metric.unit",
    "change_budget.allowed_paths",
    "change_budget.max_files",
    "change_budget.max_changed_lines",
    "change_budget.max_candidate_commits",
    "change_budget.max_candidate_runs",
    "budget.max_wall_time_seconds",
    "budget.max_cost_usd",
    "budget.max_artifact_bytes",
    "budget.max_log_bytes",
)


def export_proposal(snapshot: ExplorationSnapshot) -> ResearchProposalV1:
    """Export a useful direction while refusing to infer any verification prerequisite."""
    state = snapshot.state
    pico = _mapping(state.get("pico"))
    experiment = _mapping(state.get("experiment_plan"))
    question = _text(pico.get("refined_question")) or _text(state.get("raw_question"))
    if not question:
        raise ValueError("Studio snapshot does not contain a research question")

    metrics = _strings(experiment.get("metrics"))
    expected_results = _text(experiment.get("expected_results"))
    gap = _first_text(state.get("research_gaps"))
    hypothesis = expected_results or gap or "The Studio direction requires human hypothesis review."
    paper_refs = _paper_refs(state.get("papers"))
    evidence_refs = tuple(item["url"] for item in paper_refs if item["url"])

    code_validation = _mapping(_mapping(state.get("code_artifact")).get("validation"))
    payload: dict[str, object] = {
        "schema_version": 1,
        "proposal_id": f"proposal-{snapshot.run_id}",
        "studio_run_id": snapshot.run_id,
        "research_question": question,
        "hypothesis": hypothesis,
        "paper_refs": list(paper_refs),
        "repository_candidate": {"url": "", "commit_sha": ""},
        "objective": {
            "description": expected_results or question,
            "metric_name": metrics[0] if metrics else "",
        },
        "suggested_execution": {
            "run_argv": list(_strings(code_validation.get("run_argv"))),
            "metric_artifact_path": _text(code_validation.get("metric_artifact_path")),
            "metric_json_pointer": _text(code_validation.get("metric_json_pointer")),
        },
        "allowed_change_paths": [],
        "evidence_refs": list(evidence_refs),
        "missing_fields": list(_REQUIRED_FORGE_FIELDS),
        "status": "UNVERIFIED",
    }
    return ResearchProposalV1.from_mapping(payload)


def _mapping(value: object) -> Mapping[str, object]:
    return value if isinstance(value, Mapping) else {}


def _text(value: object) -> str:
    return value.strip() if isinstance(value, str) else ""


def _strings(value: object) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    return tuple(item.strip() for item in value if isinstance(item, str) and item.strip())


def _first_text(value: object) -> str:
    values = _strings(value)
    return values[0] if values else ""


def _paper_refs(value: object) -> tuple[dict[str, str], ...]:
    if not isinstance(value, list):
        return ()
    refs: list[dict[str, str]] = []
    for index, paper in enumerate(value):
        if not isinstance(paper, Mapping):
            continue
        paper_id = _text(paper.get("id")) or _text(paper.get("arxiv_id")) or f"studio-paper-{index + 1}"
        title = _text(paper.get("title")) or paper_id
        url = _text(paper.get("url"))
        refs.append({"paper_id": paper_id, "title": title, "url": url})
    return tuple(refs)
