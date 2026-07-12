"""
Smoke tests:不真调 LLM,只验证 import 和基本数据流。
运行:pytest backend/tests/
"""

from __future__ import annotations


def test_imports() -> None:
    """所有模块能正常 import。"""
    from co_scientist import config, llm, state, utils  # noqa: F401
    from co_scientist.graph import build_graph  # noqa: F401
    from co_scientist.modules import (
        m1_refiner,
        m2_retriever,
        m3_kg,
        m4_critique,
        m5_experiment,
        m6_code,
        m7_writer,
        m8_replay,
    )
    from co_scientist.appendix import adversarial, evolve  # noqa: F401


def test_initial_state() -> None:
    from co_scientist.state import make_initial_state

    s = make_initial_state("测试问题")
    assert s["raw_question"] == "测试问题"
    assert s["papers"] == []
    assert s["execution_mode"] == "generate_only"


def test_cost_calc() -> None:
    from co_scientist.utils.cost_tracker import CostTracker

    # 10k input + 2k output,deepseek-chat
    cost = CostTracker.calc_cost("deepseek-chat", 10_000, 2_000)
    # 10000 * 0.27 / 1M + 2000 * 1.10 / 1M = 0.0027 + 0.0022 = 0.0049
    assert 0.004 < cost < 0.006


def test_rrf_fusion() -> None:
    from co_scientist.modules.m2_retriever.fusion import reciprocal_rank_fusion
    from co_scientist.state import Paper

    list1: list[Paper] = [
        Paper(id="A", title="Paper A", doi="10.1/a", source="s1"),
        Paper(id="B", title="Paper B", doi="10.1/b", source="s1"),
    ]
    list2: list[Paper] = [
        Paper(id="A", title="Paper A", doi="10.1/a", source="s2"),
        Paper(id="C", title="Paper C", doi="10.1/c", source="s2"),
    ]
    merged = reciprocal_rank_fusion([list1, list2])
    # A 在两个源都出现,应排第一
    assert merged[0]["doi"] == "10.1/a"
    assert len(merged) == 3  # A, B, C


def test_graph_builds() -> None:
    """图能构建(需要 LangGraph 已装)。"""
    from co_scientist.graph import build_graph

    graph = build_graph(interrupt_before_code=False)
    assert graph is not None


def test_inverted_abstract() -> None:
    from co_scientist.modules.m2_retriever.sources.openalex_src import _decode_inverted_abstract

    inv = {"the": [0, 3], "model": [1], "cat": [2]}
    text = _decode_inverted_abstract(inv)
    assert text == "the model cat the"


def test_reviewer_personas() -> None:
    from co_scientist.modules.m4_critique import ALL_REVIEWERS, META_REVIEWER

    names = [r.name for r in ALL_REVIEWERS]
    assert "novelty" in names
    assert "methodology" in names
    assert "devil" in names
    assert META_REVIEWER.model_role == "critical"  # Claude Opus
