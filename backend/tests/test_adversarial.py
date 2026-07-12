"""
单元测试:对抗模块的纯逻辑(不调真实 LLM)。
"""

from __future__ import annotations

import pytest


def test_to_dpo_pair_chosen_fixed() -> None:
    from co_scientist.appendix.adversarial import AdversarialRound, to_dpo_pair

    r = AdversarialRound(
        blue_original="orig",
        red_attack="attack",
        blue_fixed="fix",
        judgment={"chosen": "blue_fixed", "blue_score": 8},
    )
    pair = to_dpo_pair(r)
    assert pair["chosen"] == "fix"
    assert pair["rejected"] == "orig"
    assert pair["score"] == 8
    assert "attack" in pair["prompt"]


def test_to_dpo_pair_chosen_original() -> None:
    """Judge 认为原方案更好 → chosen/rejected 交换。"""
    from co_scientist.appendix.adversarial import AdversarialRound, to_dpo_pair

    r = AdversarialRound(
        blue_original="orig",
        red_attack="attack",
        blue_fixed="fix",
        judgment={"chosen": "blue_original", "blue_score": 7},
    )
    pair = to_dpo_pair(r)
    assert pair["chosen"] == "orig"
    assert pair["rejected"] == "fix"


def test_to_dpo_pair_default_is_fixed() -> None:
    """Judge 没返回 chosen 时默认走 blue_fixed(兜底)。"""
    from co_scientist.appendix.adversarial import AdversarialRound, to_dpo_pair

    r = AdversarialRound("a", "b", "c", judgment={})
    pair = to_dpo_pair(r)
    assert pair["chosen"] == "c"


def test_red_found_issue() -> None:
    from co_scientist.appendix.adversarial.red_blue import _red_found_issue

    assert _red_found_issue("这里有三个严重漏洞") is True
    assert _red_found_issue("") is False
    assert _red_found_issue("   ") is False
    assert _red_found_issue("未发现新的问题") is False
    assert _red_found_issue("No more issues") is False
    assert _red_found_issue("暂无漏洞,方案已经很完善") is False


@pytest.fixture
def fake_rounds(monkeypatch):
    """给 run_round 打桩,用 counter 控制每轮返回什么。"""
    from co_scientist.appendix.adversarial import red_blue

    state = {"i": 0}

    def fake_run_round(proposal: str):
        state["i"] += 1
        from co_scientist.appendix.adversarial import AdversarialRound

        if state["i"] < 3:
            return AdversarialRound(
                blue_original=proposal,
                red_attack=f"issue round {state['i']}",
                blue_fixed=f"fixed v{state['i']}",
                judgment={"chosen": "blue_fixed", "blue_score": 7},
            )
        # 第 3 轮 Red 找不到漏洞
        return AdversarialRound(
            blue_original=proposal,
            red_attack="未发现新的漏洞",
            blue_fixed=f"fixed v{state['i']}",
            judgment={"chosen": "blue_fixed", "blue_score": 9},
        )

    monkeypatch.setattr(red_blue, "run_round", fake_run_round)
    return state


def test_run_multi_round_stops_on_no_issue(fake_rounds) -> None:
    from co_scientist.appendix.adversarial import run_multi_round

    mr = run_multi_round("seed", max_rounds=5)
    assert mr.stopped_reason == "no_more_issue"
    assert len(mr.rounds) == 3
    assert mr.final_proposal.startswith("fixed v")


def test_run_multi_round_hits_max(monkeypatch) -> None:
    from co_scientist.appendix.adversarial import AdversarialRound, run_multi_round
    from co_scientist.appendix.adversarial import red_blue

    # Red 永远找得到问题
    def always_attack(proposal):
        return AdversarialRound(
            blue_original=proposal,
            red_attack="still has problems",
            blue_fixed=proposal + "+",
            judgment={"chosen": "blue_fixed"},
        )

    monkeypatch.setattr(red_blue, "run_round", always_attack)
    mr = run_multi_round("x", max_rounds=2)
    assert mr.stopped_reason == "max_rounds"
    assert len(mr.rounds) == 2


def test_run_multi_round_red_failed(monkeypatch) -> None:
    from co_scientist.appendix.adversarial import run_multi_round
    from co_scientist.appendix.adversarial import red_blue

    def boom(proposal):
        raise RuntimeError("red crashed")

    monkeypatch.setattr(red_blue, "run_round", boom)
    mr = run_multi_round("x", max_rounds=3)
    assert mr.stopped_reason == "red_failed"
    assert mr.rounds == []
