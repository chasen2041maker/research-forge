"""Contract-level handoff tests; Studio and Forge keep their normal test suites separate."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from co_scientist.public_api import ExplorationSnapshot, export_proposal
from research_contracts import ResearchProposalV1
from research_forge.adapters.inbound.api import create_app
from research_forge.application.dto import JsonSchemaReproductionSpecValidator
from research_gateway import ProposalCompletionV1, ProposalHandoffError, compile_proposal


def _proposal() -> ResearchProposalV1:
    return export_proposal(
        ExplorationSnapshot.create(
            run_id="studio-run-001",
            state={
                "raw_question": "Can retrieval reduce hallucinations in clinical summarization?",
                "pico": {"refined_question": "Does retrieval reduce clinical-summary hallucination?"},
                "papers": [
                    {
                        "id": "arxiv:2401.00001",
                        "title": "A retrieval baseline",
                        "url": "https://arxiv.org/abs/2401.00001",
                    }
                ],
                "experiment_plan": {
                    "metrics": ["hallucination_rate"],
                    "expected_results": "Retrieval will reduce hallucination rate.",
                },
                "code_artifact": {
                    "validation": {
                        "run_argv": ["python", "evaluate.py"],
                        "metric_artifact_path": "metrics.json",
                        "metric_json_pointer": "/hallucination_rate",
                    }
                },
            },
        )
    )


def _completion() -> ProposalCompletionV1:
    return ProposalCompletionV1.from_mapping(
        {
            "mode": "reproduce",
            "paper": {
                "artifact_id": "paper-toy-001",
                "sha256": "a" * 64,
                "extraction_profile": "plain-text-v1",
            },
            "repository": {
                "url_or_path": "tests/fixtures/toy_reproduction_repo",
                "commit_sha": "b" * 40,
            },
            "execution": {
                "image_digest": "sha256:" + "c" * 64,
                "setup_mode": "prebuilt",
                "setup_argv": [],
                "run_argv": ["python", "evaluate.py", "--output", "metrics.json"],
                "working_directory": ".",
                "timeout_seconds": 120,
                "network_policy": "offline",
                "allowed_domains": [],
            },
            "metric": {
                "artifact_path": "metrics.json",
                "format": "json",
                "json_pointer": "/hallucination_rate",
                "comparator": "equals",
                "expected_value": 0.8,
                "tolerance": 0.001,
                "unit": "ratio",
            },
            "change_budget": {
                "allowed_paths": [],
                "max_files": 0,
                "max_changed_lines": 0,
                "max_candidate_commits": 0,
                "max_candidate_runs": 0,
            },
            "budget": {
                "max_wall_time_seconds": 300,
                "max_cost_usd": 0,
                "max_artifact_bytes": 10_485_760,
                "max_log_bytes": 1_048_576,
            },
        }
    )


def _validator() -> JsonSchemaReproductionSpecValidator:
    schema_path = Path(__file__).resolve().parents[3] / "docs" / "contracts" / "reproduction-spec-v1.schema.json"
    return JsonSchemaReproductionSpecValidator(json.loads(schema_path.read_text(encoding="utf-8")))


def test_studio_export_is_explicitly_unverified_and_compiles_only_confirmed_data() -> None:
    proposal = _proposal()
    assert proposal.status == "UNVERIFIED"
    assert proposal.to_mapping()["suggested_execution"]["run_argv"] == ["python", "evaluate.py"]

    built = compile_proposal(proposal=proposal, completion=_completion())
    accepted = _validator().validate(built.spec)

    assert built.proposal_id == "proposal-studio-run-001"
    assert accepted.payload["execution"]["run_argv"] == ["python", "evaluate.py", "--output", "metrics.json"]


def test_handoff_refuses_a_proposal_when_human_completion_omits_a_required_value() -> None:
    proposal = _proposal()
    base = _completion().payload
    execution = base["execution"]
    assert isinstance(execution, dict)
    completion = ProposalCompletionV1.from_mapping(
        {
            **base,
            "execution": {**execution, "run_argv": []},
        }
    )

    with pytest.raises(ProposalHandoffError, match="execution.run_argv"):
        compile_proposal(proposal=proposal, completion=completion)


class _RecordingController:
    def __init__(self) -> None:
        self.spec: dict[str, object] | None = None

    def create(self, spec: object) -> dict[str, str]:
        self.spec = dict(spec) if isinstance(spec, dict) else None
        return {"mission_id": "mission-001", "status": "READY"}


def test_forge_handoff_endpoint_calls_the_existing_mission_creation_boundary() -> None:
    controller = _RecordingController()
    app = create_app(controller=controller, local_token="test-token", cors_origins=())
    proposal = _proposal()

    response = TestClient(app).post(
        "/v1/proposals/handoff",
        headers={"Authorization": "Bearer test-token"},
        json={"proposal": proposal.to_mapping(), "completion": _completion().payload},
    )

    assert response.status_code == 200
    assert response.json()["proposal_id"] == proposal.proposal_id
    assert response.json()["mission"]["mission_id"] == "mission-001"
    assert controller.spec is not None
    assert controller.spec["schema_version"] == 1
