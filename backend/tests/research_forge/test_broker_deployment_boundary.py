"""Regression checks for the separately supervised Docker broker deployment boundary."""

from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
DEPLOYMENT = ROOT / "deploy" / "research-forge" / "systemd"


def test_only_the_broker_service_receives_docker_group_membership() -> None:
    broker = (DEPLOYMENT / "research-forge-sandbox-broker.service").read_text(encoding="utf-8")
    worker = (DEPLOYMENT / "research-forge-worker.service").read_text(encoding="utf-8")
    api = (DEPLOYMENT / "research-forge-api.service").read_text(encoding="utf-8")
    publisher = (DEPLOYMENT / "research-forge-publisher.service").read_text(encoding="utf-8")

    assert "SupplementaryGroups=docker" in broker
    assert "research_forge.bootstrap.sandbox_broker" in broker
    assert "SupplementaryGroups=docker" not in worker
    assert "SupplementaryGroups=docker" not in api
    assert "SupplementaryGroups=docker" not in publisher
    assert "research-forge-sandbox-broker.service" in worker
