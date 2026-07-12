"""
============================================================
 Phase E 测试:API 端点(整理版前端对接)
============================================================

🎓 测试覆盖
    - GET /api/forks/tree:返回 tree + forks 列表
    - GET /api/forks/{fork_id}:不存在 404,存在返回 meta + (snapshot|None)
    - GET /api/branches/compare:多 fork 对比
    - POST /api/branches/merge:规则版选 winner mark mainline
    - _build_snapshot 把 ResearchState 压成完整 snapshot(整理版 Phase A-D 全字段)

🧪 不跑真实 LangGraph,直接操作 _runs / fork_manager 注入桩状态。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient


# ============================================================
# Helpers
# ============================================================


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """创建一个干净的 TestClient,fork_manager 用 tmp_path 隔离。"""
    # 在 import api 之前换掉 fork db 路径,避免污染 dev forks.db
    from co_scientist.modules.m8_replay import ForkManager
    fm = ForkManager(db_path=tmp_path / "forks.db")

    # 把 api 模块里的 fork_manager 单例换成隔离实例
    from co_scientist.api import main as api_main
    monkeypatch.setattr(api_main, "fork_manager", fm)
    # 清空内存 _runs(同一进程跑测试可能残留)
    monkeypatch.setattr(api_main, "_runs", {})
    return TestClient(api_main.app), fm, api_main


# ============================================================
# _build_snapshot 字段完整性
# ============================================================

def test_build_snapshot_includes_phase_abc_fields() -> None:
    from co_scientist.api.main import _build_snapshot

    state = {
        "raw_question": "q",
        "pico": {"refined_question": "rq"},
        "papers": [{"id": "p1"}, {"id": "p2"}],
        "critiques": [{"reviewer": "novelty", "rating": 7}],
        "meta_decision": {"final_rating": 7.0, "decision": "pass"},
        "topic_cards": [{"topic_id": "tc-1", "title": "T"}],
        "current_topic_id": "tc-1",
        "evidence_access_status": [
            {"paper_id": "p1", "access_status": "fulltext", "evidence_level": "high"},
        ],
        "gap_cards": [{"gap_id": "g1", "title": "G"}],
        "current_gap_id": "g1",
        "decision_card": {
            "decision": "pass",
            "final_rating": 7.5,
            "recommended_action": "continue",
            "target_node": "m6",
        },
        "experiment_plan": {"name": "exp", "baselines": ["B"]},
        "metadata": {"research_gate": {"gate_decision": "continue_to_m6"}},
        "paper_draft": {"title": "PT", "latex_path": "/x.tex"},
        "error_log": [],
    }
    snap = _build_snapshot(state)
    assert snap["topic_cards"][0]["topic_id"] == "tc-1"
    assert snap["current_topic_id"] == "tc-1"
    assert len(snap["evidence_access_status"]) == 1
    assert snap["gap_cards"][0]["gap_id"] == "g1"
    assert snap["current_gap_id"] == "g1"
    assert snap["decision_card"]["target_node"] == "m6"
    assert snap["research_gate"]["gate_decision"] == "continue_to_m6"
    assert snap["paper_title"] == "PT"
    assert snap["paper_latex_path"] == "/x.tex"
    # legacy 字段也保留
    assert snap["papers_count"] == 2
    assert snap["meta_decision"]["final_rating"] == 7.0
    assert "artifacts" in snap


def test_initial_progress_marks_m0_skipped() -> None:
    from co_scientist.api.main import _initial_progress

    progress = _initial_progress(skip_m0=True)
    rows = {n["id"]: n for n in progress["nodes"]}
    assert rows["m0"]["status"] == "skipped"
    assert rows["user_select_topic"]["status"] == "skipped"
    assert rows["m1"]["status"] == "pending"


def test_topics_discover_endpoint(client, monkeypatch: pytest.MonkeyPatch) -> None:
    test_client, _, api_main = client

    def _fake_discover(*args: Any, **kwargs: Any) -> list[dict]:
        return [{"topic_id": "tc-1", "title": "T", "candidate_question": "Q", "score": 8.0}]

    monkeypatch.setattr(api_main, "discover_topics", _fake_discover)
    resp = test_client.post("/api/topics/discover", json={"question": "RAG", "k": 3})
    assert resp.status_code == 200
    data = resp.json()
    assert data["count"] == 1
    assert data["topic_cards"][0]["topic_id"] == "tc-1"


def test_m1_clarify_endpoint_returns_follow_up(client, monkeypatch: pytest.MonkeyPatch) -> None:
    test_client, _, api_main = client

    monkeypatch.setattr(
        api_main,
        "check_specificity",
        lambda question: (False, "你准备使用哪个数据集和指标?"),
    )

    resp = test_client.post("/api/m1/clarify", json={"question": "我想做 RAG"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["ready"] is False
    assert data["turn"] == 1
    assert data["follow_up"] == "你准备使用哪个数据集和指标?"


def test_m1_clarify_endpoint_ready_after_answer(client, monkeypatch: pytest.MonkeyPatch) -> None:
    test_client, _, api_main = client
    seen: dict[str, str] = {}

    def _fake_check(question: str) -> tuple[bool, str]:
        seen["question"] = question
        return True, ""

    monkeypatch.setattr(api_main, "check_specificity", _fake_check)

    resp = test_client.post(
        "/api/m1/clarify",
        json={
            "question": "我想做 RAG",
            "clarifications": [{"q": "数据集?", "a": "HotpotQA,F1"}],
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["ready"] is True
    assert data["follow_up"] == ""
    assert "HotpotQA" in seen["question"]


# ============================================================
# /api/forks/tree
# ============================================================

def test_forks_tree_with_branched_topics(client) -> None:
    test_client, fm, _ = client
    metas = fm.branch_from_topic_cards([
        {"topic_id": "tc-a", "title": "A"},
        {"topic_id": "tc-b", "title": "B"},
    ])
    fm.update_status(metas[0].fork_id, "done", final_rating=8.0)
    fm.update_status(metas[1].fork_id, "done", final_rating=6.0)

    resp = test_client.get("/api/forks/tree")
    assert resp.status_code == 200
    data = resp.json()
    # 两条都挂在 root
    assert "root" in data["tree"]
    assert set(data["tree"]["root"]) == {metas[0].fork_id, metas[1].fork_id}
    # forks 列表里 topic_id 字段已暴露
    fids = {f["fork_id"]: f for f in data["forks"]}
    assert fids[metas[0].fork_id]["topic_id"] == "tc-a"


# ============================================================
# /api/forks/{fork_id}
# ============================================================

def test_fork_detail_404(client) -> None:
    test_client, _, _ = client
    resp = test_client.get("/api/forks/nonexistent_id")
    assert resp.status_code == 404


def test_fork_detail_with_snapshot(client) -> None:
    test_client, fm, api_main = client
    meta = fm.create_fork("", "m0", "test")
    fm.update_status(meta.fork_id, "done", 7.0)

    api_main._runs[meta.fork_id] = {
        "status": "done",
        "state": {
            "pico": {"refined_question": "rq"},
            "papers": [{"id": "p1"}],
            "decision_card": {"final_rating": 7.0, "decision": "pass"},
        },
    }

    resp = test_client.get(f"/api/forks/{meta.fork_id}")
    assert resp.status_code == 200
    data = resp.json()
    assert data["meta"]["fork_id"] == meta.fork_id
    assert data["status"] == "done"
    assert data["snapshot"]["pico"]["refined_question"] == "rq"
    assert data["snapshot"]["decision_card"]["final_rating"] == 7.0


def test_fork_detail_no_state_yet(client) -> None:
    """fork 已创建但没跑 → snapshot 为 None,不抛。"""
    test_client, fm, _ = client
    meta = fm.create_fork("", "m0", "no run")
    resp = test_client.get(f"/api/forks/{meta.fork_id}")
    assert resp.status_code == 200
    data = resp.json()
    assert data["snapshot"] is None


# ============================================================
# /api/branches/compare
# ============================================================

def test_branches_compare(client) -> None:
    test_client, fm, api_main = client
    metas = fm.branch_from_topic_cards([
        {"topic_id": "tc-a", "title": "A"},
        {"topic_id": "tc-b", "title": "B"},
    ])
    for m, r in zip(metas, [6.0, 8.5]):
        fm.update_status(m.fork_id, "done", r)
        api_main._runs[m.fork_id] = {
            "status": "done",
            "state": {
                "pico": {"refined_question": f"q-{m.fork_id[:4]}"},
                "papers": [{"id": "p1"}, {"id": "p2"}],
                "meta_decision": {"final_rating": r, "decision": "pass"},
                "decision_card": {"final_rating": r, "decision": "pass"},
            },
        }

    resp = test_client.get(
        f"/api/branches/compare?fork_ids={metas[0].fork_id},{metas[1].fork_id}"
    )
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["branches"]) == 2
    # 摘要里 final_rating 已汇总
    assert {b["final_rating"] for b in data["branches"]} == {6.0, 8.5}
    assert all("final_rating" in b["summary"] for b in data["branches"])


def test_branches_compare_skips_unknown_ids(client) -> None:
    """传入的 fork_id 中混入不存在的:静默跳过,不报错。"""
    test_client, fm, _ = client
    real = fm.create_fork("", "m0", "ok")
    resp = test_client.get(f"/api/branches/compare?fork_ids={real.fork_id},bogus")
    assert resp.status_code == 200
    assert len(resp.json()["branches"]) == 1


# ============================================================
# /api/branches/merge
# ============================================================

def test_branches_merge_rule_version_picks_highest_rating(client) -> None:
    test_client, fm, api_main = client
    metas = fm.branch_from_topic_cards([
        {"topic_id": "tc-a", "title": "A"},
        {"topic_id": "tc-b", "title": "B"},
        {"topic_id": "tc-c", "title": "C"},
    ])
    fm.update_status(metas[0].fork_id, "done", 5.0)
    fm.update_status(metas[1].fork_id, "done", 9.0)
    fm.update_status(metas[2].fork_id, "abandoned", 0.0)

    resp = test_client.post(
        "/api/branches/merge",
        json={
            "fork_ids": [m.fork_id for m in metas],
            "use_llm_compare": False,
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["winner"]["fork_id"] == metas[1].fork_id
    # mainline 已落库
    assert fm.get_meta(metas[1].fork_id).status == "mainline"


def test_branches_merge_returns_none_when_all_abandoned(client) -> None:
    test_client, fm, _ = client
    metas = fm.branch_from_topic_cards([
        {"topic_id": "x", "title": "X"},
    ])
    fm.update_status(metas[0].fork_id, "abandoned", 0.0)

    resp = test_client.post(
        "/api/branches/merge",
        json={"fork_ids": [metas[0].fork_id], "use_llm_compare": False},
    )
    assert resp.status_code == 200
    assert resp.json()["winner"] is None


def test_branches_merge_404_on_unknown_ids(client) -> None:
    test_client, _, _ = client
    resp = test_client.post(
        "/api/branches/merge",
        json={"fork_ids": ["nope1", "nope2"], "use_llm_compare": False},
    )
    assert resp.status_code == 404


# ============================================================
# /api/branches/run(BackgroundTasks 不真跑,只检查接口形状)
# ============================================================

def test_branches_run_creates_forks(client, monkeypatch: pytest.MonkeyPatch) -> None:
    """启动多分支:立即返回 fork_ids,fork 已写入 forks.db。"""
    test_client, fm, api_main = client

    # 桩掉 run_topic_branches:让 BackgroundTask 一进去就直接返回,
    # 避免真跑 LangGraph(它需要 langgraph 包)
    def _stub_runner(*args: Any, **kwargs: Any) -> tuple:
        return None, []

    monkeypatch.setattr(
        "co_scientist.modules.m8_replay.run_topic_branches",
        _stub_runner,
    )

    resp = test_client.post(
        "/api/branches/run",
        json={
            "raw_question": "我想做 RAG 方向",
            "topic_cards": [
                {"topic_id": "tc-a", "title": "A", "candidate_question": "qa"},
                {"topic_id": "tc-b", "title": "B", "candidate_question": "qb"},
            ],
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["fork_ids"]) == 2
    # 元数据已经落库
    for fid in data["fork_ids"]:
        meta = fm.get_meta(fid)
        assert meta is not None
        assert meta.branch_node == "m0_discover"


def test_branches_run_400_on_empty_topic_cards(client) -> None:
    test_client, _, _ = client
    resp = test_client.post(
        "/api/branches/run",
        json={"raw_question": "x", "topic_cards": []},
    )
    assert resp.status_code == 400
