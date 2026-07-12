"""
============================================================
 Phase C 测试:M2.5 文献访问状态 + M4 DecisionCard + M5.5 ResearchGate
============================================================

🎓 测试覆盖
    - M2.5 启发式分级:fulltext/abstract_only/restricted/failed,各档位映射正确
    - M2.5 has_code/has_dataset 嗅探与等级升级
    - M4 build_decision_card 正常路径与 LLM 失败兜底
    - M5.5 启发式 4 个分支(缺字段/低证据/服从 DecisionCard/默认放行)
    - M5.5 LLM 输出非法动作时回退到启发式
"""

from __future__ import annotations

from typing import Any

import pytest


class FakeLLM:
    def __init__(self, responses: dict[str, Any]) -> None:
        self._responses = responses
        self.calls: list[dict[str, Any]] = []

    def chat_json(self, **kwargs: Any) -> dict:
        purpose = kwargs.get("purpose", "")
        self.calls.append({"method": "chat_json", **kwargs})
        if purpose not in self._responses:
            raise AssertionError(
                f"FakeLLM 未配置 purpose={purpose!r},已知: {list(self._responses)}"
            )
        return dict(self._responses[purpose])

    def chat(self, **kwargs: Any) -> dict:
        purpose = kwargs.get("purpose", "")
        self.calls.append({"method": "chat", **kwargs})
        resp = self._responses.get(purpose, "")
        if isinstance(resp, str):
            return {"content": resp, "input_tokens": 0, "output_tokens": 0}
        return resp


def _install_fake(monkeypatch: pytest.MonkeyPatch, fake: FakeLLM) -> None:
    monkeypatch.setattr("co_scientist.llm.get_llm", lambda role="chat": fake)
    for mod in [
        "co_scientist.modules.m4_critique.roundtable",
        "co_scientist.modules.m5_5_research_gate.gate",
    ]:
        monkeypatch.setattr(f"{mod}.get_llm", lambda role="chat": fake, raising=False)


# ============================================================
# M2.5
# ============================================================

def test_m2_5_arxiv_with_code_is_high() -> None:
    from co_scientist.modules.m2_5_access_status import parse_access_status
    from co_scientist.state import Paper
    p = Paper(
        id="p1", title="t",
        abstract="Code at https://github.com/foo/bar",
        source="arxiv", arxiv_id="2401.0001",
        url="https://arxiv.org/abs/2401.0001",
    )
    [s] = parse_access_status([p])
    assert s["access_status"] == "fulltext"
    assert s["evidence_level"] == "high"
    assert s["has_code"] is True


def test_m2_5_doi_no_abstract_is_restricted_low() -> None:
    from co_scientist.modules.m2_5_access_status import parse_access_status
    from co_scientist.state import Paper
    p = Paper(id="p2", title="t", abstract="", doi="10.1234/x", url="https://elsevier.com/x")
    [s] = parse_access_status([p])
    assert s["access_status"] == "restricted"
    assert s["evidence_level"] == "low"


def test_m2_5_abstract_only_default_medium() -> None:
    from co_scientist.modules.m2_5_access_status import parse_access_status
    from co_scientist.state import Paper
    p = Paper(id="p3", title="t", abstract="some abstract", doi="10.1/x")
    [s] = parse_access_status([p])
    assert s["access_status"] == "abstract_only"
    assert s["evidence_level"] == "medium"


def test_m2_5_node_skips_no_papers(monkeypatch: pytest.MonkeyPatch) -> None:
    """空 papers 时节点直接跳过,不抛异常。"""
    from co_scientist.modules.m2_5_access_status import access_status_node
    patch = access_status_node({"papers": []})  # type: ignore[arg-type]
    assert patch == {}


# ============================================================
# M4 DecisionCard
# ============================================================

def test_m4_build_decision_card_normal(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakeLLM({
        "m4_decision_card": {
            "passed": True,
            "decision": "pass",
            "final_rating": 7.5,
            "recommended_action": "continue",
            "target_node": "m6",
            "branch_count": 1,
            "branch_variants": [],
            "blocking_issues": [],
            "required_fixes": [],
            "reason": "OK",
        }
    })
    _install_fake(monkeypatch, fake)

    from co_scientist.modules.m4_critique.roundtable import build_decision_card

    dc = build_decision_card(
        meta_decision={"decision": "pass", "final_rating": 7.5},
        cards=[{"reviewer": "novelty", "rating": 8, "weaknesses": ["w1"]}],
        gap=None,
        access_statuses=[],
    )
    assert dc["passed"] is True
    assert dc["decision"] == "pass"
    assert dc["target_node"] == "m6"
    assert dc["final_rating"] == 7.5


def test_m4_build_decision_card_llm_fail_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    """LLM 挂掉 → 兜底返回 minor_revision DecisionCard,不抛。"""
    class BoomLLM(FakeLLM):
        def chat_json(self, **kwargs: Any) -> dict:
            raise RuntimeError("Claude 5xx")

    fake = BoomLLM({})
    _install_fake(monkeypatch, fake)

    from co_scientist.modules.m4_critique.roundtable import build_decision_card

    dc = build_decision_card(
        meta_decision={"decision": "pass", "final_rating": 6.0},
        cards=[],
        gap=None,
        access_statuses=[],
    )
    assert dc["decision"] == "minor_revision"
    assert dc["recommended_action"] == "revise_experiment"
    assert dc["target_node"] == "m5"
    assert dc["final_rating"] == 6.0  # 用兜底 fallback rating


# ============================================================
# M5.5 ResearchGate
# ============================================================

def test_m5_5_gate_missing_baseline_revise() -> None:
    from co_scientist.modules.m5_5_research_gate import decide_gate
    from co_scientist.state import Experiment

    exp = Experiment(name="x", datasets=[{"name": "D"}], baselines=[], metrics=["EM"])
    g = decide_gate(exp, None, [])
    assert g["gate_decision"] == "revise_experiment"
    assert any("baseline" in i for i in g["blocking_issues"])


def test_m5_5_gate_low_evidence_fetch() -> None:
    from co_scientist.modules.m5_5_research_gate import decide_gate
    from co_scientist.state import Experiment, EvidenceAccessStatus

    exp = Experiment(name="x", datasets=[{"name": "D"}], baselines=["B"], metrics=["EM"])
    statuses = [
        EvidenceAccessStatus(
            paper_id=f"p{i}", evidence_level="low", access_status="restricted",
            has_code=False, has_dataset=False, has_benchmark=False, notes=[]
        )
        for i in range(3)
    ]
    statuses.append(EvidenceAccessStatus(
        paper_id="p4", evidence_level="medium", access_status="abstract_only",
        has_code=False, has_dataset=False, has_benchmark=False, notes=[]
    ))
    g = decide_gate(exp, None, statuses)
    assert g["gate_decision"] == "fetch_more_evidence"


def test_m5_5_gate_obeys_decision_card() -> None:
    from co_scientist.modules.m5_5_research_gate import decide_gate
    from co_scientist.state import DecisionCard, Experiment

    exp = Experiment(name="x", datasets=[{"name": "D"}], baselines=["B"], metrics=["EM"])
    dc = DecisionCard(
        recommended_action="choose_new_topic",
        target_node="m0",
        final_rating=2.0,
        blocking_issues=["创新性不足"],
        required_fixes=[],
    )
    g = decide_gate(exp, dc, [])
    assert g["gate_decision"] == "choose_new_topic"


def test_m5_5_gate_default_continue() -> None:
    from co_scientist.modules.m5_5_research_gate import decide_gate
    from co_scientist.state import Experiment

    exp = Experiment(name="x", datasets=[{"name": "D"}], baselines=["B1", "B2"], metrics=["EM", "F1"])
    g = decide_gate(exp, None, [])
    assert g["gate_decision"] == "continue_to_m6"


def test_m5_5_gate_llm_invalid_action_falls_back(monkeypatch: pytest.MonkeyPatch) -> None:
    """LLM 输出非法 gate_decision → 沿用启发式。"""
    fake = FakeLLM({
        "m5_5_gate": {
            "gate_decision": "WTF_INVALID",
            "rationale": "should fallback",
            "blocking_issues": [],
            "required_fixes": [],
        }
    })
    _install_fake(monkeypatch, fake)

    from co_scientist.modules.m5_5_research_gate import decide_gate
    from co_scientist.state import Experiment

    exp = Experiment(name="x", datasets=[{"name": "D"}], baselines=["B"], metrics=["EM"])
    g = decide_gate(exp, None, [], use_llm=True)
    # 沿用启发式 → continue_to_m6
    assert g["gate_decision"] == "continue_to_m6"
