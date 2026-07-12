"""
============================================================
 Phase D 测试:M8 Git-like 多分支管理
============================================================

🎓 测试覆盖
    - ForkManager:branch_from_topic_cards / get_winner / mark_mainline / branch_from_gate_decision
    - run_topic_branches:K 张 TopicCard 并行(MVP 串行)→ K 条 fork,winner 选 final_rating 最高
    - run_topic_branches:其中一条 fork 跑挂 → status=abandoned 不影响其他;winner 不会选挂掉的
    - score_branches_with_llm:正常 LLM 评分 + LLM 失败降级 + LLM 给非法 fork_id 降级
    - merge_winner:use_llm_compare=False 走规则版,True 走 LLM 版,失败再回落

🧪 不依赖真实 LangGraph runtime,所有跑图调用都用桩函数注入。
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any

import pytest


# ============================================================
# Helpers:临时 ForkManager + 桩 run_pipeline
# ============================================================


@pytest.fixture
def tmp_fork_manager(tmp_path: Path):
    from co_scientist.modules.m8_replay import ForkManager
    return ForkManager(db_path=tmp_path / "forks.db")


def _state_with_rating(rating: float, **extra: Any) -> dict:
    """造一个带 final_rating 的 final_state 桩。"""
    return {
        "raw_question": extra.get("raw_question", "q"),
        "pico": {"refined_question": extra.get("refined_question", "rq")},
        "papers": [{"id": f"p{i}"} for i in range(extra.get("n_papers", 3))],
        "gap_cards": [{"gap_id": f"g{i}"} for i in range(extra.get("n_gap", 1))],
        "critiques": [{"reviewer": "novelty", "rating": int(rating)}],
        "meta_decision": {"decision": "pass", "final_rating": rating},
        "decision_card": {
            "final_rating": rating,
            "decision": "pass" if rating >= 7 else "minor_revision",
            "recommended_action": "continue",
        },
        "experiment_plan": {
            "name": extra.get("exp_name", "exp"),
            "baselines": ["B1", "B2"],
            "metrics": ["EM", "F1"],
        },
        "paper_draft": {"title": extra.get("paper_title", "draft title")},
        "metadata": {"research_gate": {"gate_decision": "continue_to_m6"}},
    }


# ============================================================
# ForkManager 扩展
# ============================================================


def test_fork_manager_branch_from_topic_cards(tmp_fork_manager) -> None:
    cards = [
        {"topic_id": "tc-a", "title": "RAG 多跳"},
        {"topic_id": "tc-b", "title": "GraphRAG"},
    ]
    metas = tmp_fork_manager.branch_from_topic_cards(cards)
    assert len(metas) == 2
    assert metas[0].topic_id == "tc-a"
    assert "RAG 多跳" in metas[0].description
    assert metas[0].branch_node == "m0_discover"


def test_fork_manager_get_winner_skips_abandoned(tmp_fork_manager) -> None:
    cards = [{"topic_id": f"tc-{i}", "title": f"t{i}"} for i in range(3)]
    metas = tmp_fork_manager.branch_from_topic_cards(cards)
    tmp_fork_manager.update_status(metas[0].fork_id, "done", final_rating=6.0)
    tmp_fork_manager.update_status(metas[1].fork_id, "abandoned", final_rating=9.5)  # 高分但弃
    tmp_fork_manager.update_status(metas[2].fork_id, "done", final_rating=7.0)

    winner = tmp_fork_manager.get_winner([m.fork_id for m in metas])
    assert winner is not None
    assert winner.fork_id == metas[2].fork_id  # 不是高分的 abandoned
    assert winner.final_rating == 7.0


def test_fork_manager_branch_from_gate_decision(tmp_fork_manager) -> None:
    parent = tmp_fork_manager.create_fork("", "m0_discover", "root")
    new = tmp_fork_manager.branch_from_gate_decision(
        parent.fork_id, "fetch_more_evidence",
    )
    assert new is not None
    assert new.parent_fork_id == parent.fork_id
    assert new.branch_node == "m2_retrieve"


def test_fork_manager_branch_from_gate_decision_stop_returns_none(tmp_fork_manager) -> None:
    parent = tmp_fork_manager.create_fork("", "m0", "root")
    assert tmp_fork_manager.branch_from_gate_decision(parent.fork_id, "stop") is None
    assert tmp_fork_manager.branch_from_gate_decision(parent.fork_id, "continue_to_m6") is None


def test_fork_manager_mark_mainline_unique_per_parent(tmp_fork_manager) -> None:
    parent = tmp_fork_manager.create_fork("", "root", "p")
    a = tmp_fork_manager.create_fork(parent.fork_id, "m1", "a")
    b = tmp_fork_manager.create_fork(parent.fork_id, "m1", "b")
    tmp_fork_manager.update_status(a.fork_id, "done", 7.0)
    tmp_fork_manager.update_status(b.fork_id, "done", 8.0)

    tmp_fork_manager.mark_mainline(a.fork_id)
    assert tmp_fork_manager.get_meta(a.fork_id).status == "mainline"

    tmp_fork_manager.mark_mainline(b.fork_id)
    # 同父下唯一 mainline:a 被降回 done
    assert tmp_fork_manager.get_meta(a.fork_id).status == "done"
    assert tmp_fork_manager.get_meta(b.fork_id).status == "mainline"


# ============================================================
# run_topic_branches
# ============================================================


def test_run_topic_branches_winner_picks_highest_rating(tmp_fork_manager) -> None:
    from co_scientist.modules.m8_replay import run_topic_branches

    cards = [
        {"topic_id": "tc-a", "title": "Topic A", "candidate_question": "qa"},
        {"topic_id": "tc-b", "title": "Topic B", "candidate_question": "qb"},
        {"topic_id": "tc-c", "title": "Topic C", "candidate_question": "qc"},
    ]

    # 桩:每张 topic 返回不同 rating
    rating_map = {"qa": 5.0, "qb": 8.5, "qc": 6.0}

    def fake_pipeline(raw_question: str, **kwargs: Any) -> dict:
        return _state_with_rating(rating_map[raw_question])

    winner, all_branches = run_topic_branches(
        "root question", cards,
        fork_manager=tmp_fork_manager,
        run_pipeline=fake_pipeline,
    )
    assert winner is not None
    assert winner.fork_meta.topic_id == "tc-b"  # qb=8.5 最高
    assert len(all_branches) == 3
    # 摘要里 final_rating 与 stub 一致
    assert all_branches[1].summary["final_rating"] == 8.5
    # 全部 done
    assert all(b.fork_meta.status == "done" for b in all_branches)


def test_run_topic_branches_one_failure_doesnt_kill_others(tmp_fork_manager) -> None:
    from co_scientist.modules.m8_replay import run_topic_branches

    cards = [
        {"topic_id": "tc-a", "title": "A", "candidate_question": "qa"},
        {"topic_id": "tc-b", "title": "B", "candidate_question": "qb"},
    ]

    def fake_pipeline(raw_question: str, **kwargs: Any) -> dict:
        if raw_question == "qa":
            raise RuntimeError("budget exceeded")
        return _state_with_rating(7.0)

    winner, all_branches = run_topic_branches(
        "root", cards,
        fork_manager=tmp_fork_manager,
        run_pipeline=fake_pipeline,
    )
    # qa 挂了,只有 qb 是赢家
    assert winner is not None
    assert winner.fork_meta.topic_id == "tc-b"
    abandoned = [b for b in all_branches if b.fork_meta.status == "abandoned"]
    assert len(abandoned) == 1
    assert "budget exceeded" in abandoned[0].error


# ============================================================
# score_branches_with_llm
# ============================================================


class _FakeLLM:
    def __init__(self, response: dict[str, Any] | Exception) -> None:
        self.response = response
        self.calls: list[dict] = []

    def chat_json(self, **kwargs: Any) -> dict:
        self.calls.append(kwargs)
        if isinstance(self.response, Exception):
            raise self.response
        return dict(self.response)

    def chat(self, **kwargs: Any) -> dict:
        return {"content": "", "input_tokens": 0, "output_tokens": 0}


def _install_compare_fake(monkeypatch: pytest.MonkeyPatch, llm: _FakeLLM) -> None:
    monkeypatch.setattr(
        "co_scientist.modules.m8_replay.multi_branch.get_llm",
        lambda role="critical": llm,
    )


def _make_branch_results(ratings: list[float]) -> list:
    """造一组成功的 BranchResult。"""
    from co_scientist.modules.m8_replay import BranchResult, ForkMeta
    out = []
    for i, r in enumerate(ratings):
        meta = ForkMeta(
            fork_id=f"f{i}", parent_fork_id="", branch_node="m0",
            description=f"fork {i}", created_at=float(i),
            final_rating=r, status="done", topic_id=f"tc-{i}",
        )
        out.append(BranchResult(
            fork_meta=meta,
            final_state=_state_with_rating(r),
            summary={"final_rating": r, "decision": "pass"},
            error="",
        ))
    return out


def test_score_branches_with_llm_normal(monkeypatch: pytest.MonkeyPatch) -> None:
    from co_scientist.modules.m8_replay import score_branches_with_llm

    branches = _make_branch_results([6.0, 8.0, 7.0])
    fake = _FakeLLM({
        "winner_fork_id": "f1",
        "winner_score": 8.5,
        "ranking": [
            {"fork_id": "f1", "score": 8.5, "reason": "best"},
            {"fork_id": "f2", "score": 7.5, "reason": "ok"},
            {"fork_id": "f0", "score": 6.0, "reason": "weak"},
        ],
        "comparison_summary": "f1 wins",
    })
    _install_compare_fake(monkeypatch, fake)

    result = score_branches_with_llm(branches)
    assert result["winner_fork_id"] == "f1"
    assert len(result["ranking"]) == 3
    # 调 critical(默认)
    assert fake.calls[0]["purpose"] == "m8_compare"


def test_score_branches_with_llm_invalid_winner_falls_back_to_top_ranking(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """LLM 给的 winner_fork_id 不在候选 → 取 ranking 第一项。"""
    from co_scientist.modules.m8_replay import score_branches_with_llm

    branches = _make_branch_results([6.0, 8.0])
    fake = _FakeLLM({
        "winner_fork_id": "BOGUS",
        "ranking": [{"fork_id": "f1", "score": 8.0}, {"fork_id": "f0", "score": 6.0}],
    })
    _install_compare_fake(monkeypatch, fake)

    result = score_branches_with_llm(branches)
    assert result["winner_fork_id"] == "f1"


def test_score_branches_with_llm_failure_returns_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    from co_scientist.modules.m8_replay import score_branches_with_llm

    branches = _make_branch_results([7.0, 8.0])
    fake = _FakeLLM(RuntimeError("relay 503"))
    _install_compare_fake(monkeypatch, fake)

    result = score_branches_with_llm(branches)
    assert result == {}  # 让 merge_winner 降级到规则版


def test_score_branches_with_llm_too_few_succ_returns_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    """不到 2 条成功分支 → 不调 LLM 直接返回 {}。"""
    from co_scientist.modules.m8_replay import score_branches_with_llm

    branches = _make_branch_results([7.0])
    fake = _FakeLLM(RuntimeError("should not be called"))
    _install_compare_fake(monkeypatch, fake)

    assert score_branches_with_llm(branches) == {}
    assert fake.calls == []


# ============================================================
# merge_winner:LLM vs rule-based
# ============================================================


def test_merge_winner_rule_version_picks_highest_rating(tmp_fork_manager) -> None:
    from co_scientist.modules.m8_replay import (
        BranchResult,
        ForkMeta,
        merge_winner,
    )

    # 在 fm 中登记两条 fork
    a = tmp_fork_manager.create_fork("", "m0", "fork a")
    b = tmp_fork_manager.create_fork("", "m0", "fork b")
    tmp_fork_manager.update_status(a.fork_id, "done", final_rating=6.0)
    tmp_fork_manager.update_status(b.fork_id, "done", final_rating=9.0)

    branches = [
        BranchResult(fork_meta=tmp_fork_manager.get_meta(a.fork_id),
                     final_state=_state_with_rating(6.0),
                     summary={"final_rating": 6.0}),
        BranchResult(fork_meta=tmp_fork_manager.get_meta(b.fork_id),
                     final_state=_state_with_rating(9.0),
                     summary={"final_rating": 9.0}),
    ]
    winner = merge_winner(branches, fork_manager=tmp_fork_manager)
    assert winner is not None
    assert winner.fork_meta.fork_id == b.fork_id
    assert tmp_fork_manager.get_meta(b.fork_id).status == "mainline"


def test_merge_winner_llm_compare_can_override_rating(
    tmp_fork_manager, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """use_llm_compare=True:LLM 可以推翻 final_rating 选择(它考虑更多维度)。"""
    from co_scientist.modules.m8_replay import (
        BranchResult,
        merge_winner,
    )

    a = tmp_fork_manager.create_fork("", "m0", "fork a")
    b = tmp_fork_manager.create_fork("", "m0", "fork b")
    tmp_fork_manager.update_status(a.fork_id, "done", final_rating=8.5)  # rating 高
    tmp_fork_manager.update_status(b.fork_id, "done", final_rating=7.0)

    branches = [
        BranchResult(fork_meta=tmp_fork_manager.get_meta(a.fork_id),
                     final_state=_state_with_rating(8.5),
                     summary={"final_rating": 8.5, "decision": "pass"}),
        BranchResult(fork_meta=tmp_fork_manager.get_meta(b.fork_id),
                     final_state=_state_with_rating(7.0),
                     summary={"final_rating": 7.0, "decision": "pass"}),
    ]
    # LLM 故意选 b(rating 较低,但综合更优)
    fake = _FakeLLM({
        "winner_fork_id": b.fork_id,
        "ranking": [{"fork_id": b.fork_id, "score": 9.0}, {"fork_id": a.fork_id, "score": 7.0}],
    })
    _install_compare_fake(monkeypatch, fake)

    winner = merge_winner(branches, fork_manager=tmp_fork_manager, use_llm_compare=True)
    assert winner is not None
    assert winner.fork_meta.fork_id == b.fork_id  # LLM 决定的 winner
    assert tmp_fork_manager.get_meta(b.fork_id).status == "mainline"


def test_merge_winner_llm_fail_falls_back_to_rule(
    tmp_fork_manager, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """LLM 评分失败 → 沿用规则版(final_rating 最高)。"""
    from co_scientist.modules.m8_replay import (
        BranchResult,
        merge_winner,
    )

    a = tmp_fork_manager.create_fork("", "m0", "a")
    b = tmp_fork_manager.create_fork("", "m0", "b")
    tmp_fork_manager.update_status(a.fork_id, "done", final_rating=8.0)
    tmp_fork_manager.update_status(b.fork_id, "done", final_rating=6.0)

    branches = [
        BranchResult(fork_meta=tmp_fork_manager.get_meta(a.fork_id),
                     final_state=_state_with_rating(8.0),
                     summary={"final_rating": 8.0}),
        BranchResult(fork_meta=tmp_fork_manager.get_meta(b.fork_id),
                     final_state=_state_with_rating(6.0),
                     summary={"final_rating": 6.0}),
    ]
    fake = _FakeLLM(RuntimeError("network"))
    _install_compare_fake(monkeypatch, fake)

    winner = merge_winner(branches, fork_manager=tmp_fork_manager, use_llm_compare=True)
    assert winner is not None
    assert winner.fork_meta.fork_id == a.fork_id  # 回落规则版,选 8.0


def test_merge_winner_no_successful_branches_returns_none(tmp_fork_manager) -> None:
    from co_scientist.modules.m8_replay import (
        BranchResult,
        ForkMeta,
        merge_winner,
    )

    branches = [
        BranchResult(
            fork_meta=ForkMeta(fork_id="x", parent_fork_id="", branch_node="m0",
                               description="x", created_at=0.0, status="abandoned"),
            final_state=None, summary={}, error="boom",
        )
    ]
    assert merge_winner(branches, fork_manager=tmp_fork_manager) is None
