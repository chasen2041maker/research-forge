"""
单元测试:PromptABTester 纯逻辑(不调真实 LLM)。
"""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture
def ab(tmp_path: Path):
    from co_scientist.appendix.evolve.prompt_ab import PromptABTester

    return PromptABTester(db_path=tmp_path / "ab.db")


def test_register_returns_pid(ab) -> None:
    pid = ab.register("m5_experiment", "hello")
    assert isinstance(pid, str) and len(pid) == 8


def test_best_for_empty_returns_none(ab) -> None:
    assert ab.best_for("m5_experiment") is None


def test_best_for_ignores_unscored_variants(ab) -> None:
    """只注册没打分 → best_for 仍是 None(WHERE runs>0)。"""
    ab.register("m5_experiment", "v1")
    ab.register("m5_experiment", "v2")
    assert ab.best_for("m5_experiment") is None


def test_best_for_picks_highest_avg(ab) -> None:
    p1 = ab.register("m5_experiment", "v1")
    p2 = ab.register("m5_experiment", "v2")
    ab.record_score(p1, 6.0)
    ab.record_score(p1, 8.0)  # v1 avg=7.0
    ab.record_score(p2, 9.0)  # v2 avg=9.0
    best = ab.best_for("m5_experiment")
    assert best is not None
    assert best.pid == p2
    assert best.avg_score == 9.0
    assert best.runs == 1


def test_record_score_accumulates(ab) -> None:
    pid = ab.register("task", "x")
    ab.record_score(pid, 3.0)
    ab.record_score(pid, 7.0)
    best = ab.best_for("task")
    assert best.runs == 2
    assert abs(best.avg_score - 5.0) < 1e-9


def test_different_name_isolated(ab) -> None:
    p1 = ab.register("taskA", "a")
    p2 = ab.register("taskB", "b")
    ab.record_score(p2, 10.0)
    assert ab.best_for("taskA") is None  # taskA 没评过
    assert ab.best_for("taskB").pid == p2


def test_evolve_prompt_llm_failure_keeps_current(ab, monkeypatch) -> None:
    from co_scientist.appendix.evolve import prompt_ab as ab_mod

    class ExplodingLLM:
        def chat(self, **kwargs):
            raise RuntimeError("llm boom")

    monkeypatch.setattr(ab_mod, "get_llm", lambda role: ExplodingLLM())
    out = ab.evolve_prompt("task", "original", ["fail1"])
    assert out == "original"
    # 失败时不应该往表里塞东西
    assert ab.best_for("task") is None


def test_evolve_prompt_registers_new_variant(ab, monkeypatch) -> None:
    from co_scientist.appendix.evolve import prompt_ab as ab_mod

    class FakeLLM:
        def chat(self, **kwargs):
            return {"content": "NEW PROMPT"}

    monkeypatch.setattr(ab_mod, "get_llm", lambda role: FakeLLM())
    out = ab.evolve_prompt("task", "original", ["fail1", "fail2"])
    assert out == "NEW PROMPT"
    # 新变体入库了,但 runs=0 仍然不会被 best_for 选中
    assert ab.best_for("task") is None
