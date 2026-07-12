"""
============================================================
 m4 Orchestrator 单元测试(tests/test_orchestrator.py)
============================================================

🎓 测什么
    1. _sanitize_selection 清洗逻辑(纯函数,好测)
    2. select_reviewers 集成测试(Orchestrator LLM 打桩)
    3. run_roundtable_async 真的按 Orchestrator 的选择并行跑(roundtable 级集成)

📌 这些测试默认全跑,不花钱(LLM 全部 monkeypatch)。
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest


# ============================================================
# Part 1:_sanitize_selection 纯逻辑
# ============================================================


def test_sanitize_filters_illegal_names() -> None:
    """LLM 幻觉出新角色(如 ethics)应被丢弃。"""
    from co_scientist.modules.m4_critique.orchestrator import _sanitize_selection

    out = _sanitize_selection(["novelty", "ethics", "methodology"])
    assert "ethics" not in out
    assert "novelty" in out
    assert "methodology" in out


def test_sanitize_normalizes_case() -> None:
    """大小写/空白不一致应归一。"""
    from co_scientist.modules.m4_critique.orchestrator import _sanitize_selection

    out = _sanitize_selection([" Novelty ", "METHODOLOGY"])
    assert "novelty" in out
    assert "methodology" in out


def test_sanitize_dedupes() -> None:
    """重复名字应去重。"""
    from co_scientist.modules.m4_critique.orchestrator import _sanitize_selection

    out = _sanitize_selection(["novelty", "novelty", "devil"])
    assert out.count("novelty") == 1
    assert "devil" in out


def test_sanitize_forces_devil() -> None:
    """devil 永远必选,LLM 漏了也要补上。"""
    from co_scientist.modules.m4_critique.orchestrator import _sanitize_selection

    out = _sanitize_selection(["novelty", "methodology", "statistics"])
    assert "devil" in out


def test_sanitize_pads_to_min() -> None:
    """少于 MIN_REVIEWERS=3 要从全量里补齐。"""
    from co_scientist.modules.m4_critique.orchestrator import (
        _MIN_REVIEWERS,
        _sanitize_selection,
    )

    out = _sanitize_selection(["novelty"])
    assert len(out) >= _MIN_REVIEWERS
    assert "devil" in out


def test_sanitize_caps_to_max() -> None:
    """多于 MAX_REVIEWERS=5 要截断。"""
    from co_scientist.modules.m4_critique.orchestrator import (
        _MAX_REVIEWERS,
        _sanitize_selection,
    )

    # 全部合法 Reviewer 都传进去(5 个)+ 一个 devil,共 5 个(devil 已在其中)
    out = _sanitize_selection(
        ["novelty", "methodology", "statistics", "reproducibility", "devil"]
    )
    assert len(out) <= _MAX_REVIEWERS


# ============================================================
# Part 2:select_reviewers 集成(Orchestrator LLM 打桩)
# ============================================================


class _FakeOrchLLM:
    """可控的假 Orchestrator,按场景返回不同的 reviewers 列表。"""

    def __init__(self, payload: dict | Exception) -> None:
        self._payload = payload

    def chat_json(self, **kwargs: Any) -> dict[str, Any]:
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload


def _patch_orch_llm(monkeypatch: pytest.MonkeyPatch, payload) -> None:
    from co_scientist.modules.m4_critique import orchestrator as orch_mod

    monkeypatch.setattr(orch_mod, "get_llm", lambda role="chat": _FakeOrchLLM(payload))


def test_select_reviewers_honors_llm_choice(monkeypatch: pytest.MonkeyPatch) -> None:
    from co_scientist.modules.m4_critique.orchestrator import select_reviewers

    _patch_orch_llm(
        monkeypatch,
        {
            "reviewers": ["novelty", "methodology", "statistics"],
            "reason": "实证研究",
        },
    )
    res = select_reviewers("q", "m")
    assert "devil" in res["reviewers"]  # 强制注入
    assert "novelty" in res["reviewers"]
    assert res["fallback"] is False
    assert res["reason"] == "实证研究"


def test_select_reviewers_fallback_on_llm_error(monkeypatch: pytest.MonkeyPatch) -> None:
    from co_scientist.modules.m4_critique.orchestrator import select_reviewers

    _patch_orch_llm(monkeypatch, RuntimeError("llm boom"))
    res = select_reviewers("q", "m")
    assert res["fallback"] is True
    # 降级应返回全量 5 个 Reviewer
    assert len(res["reviewers"]) == 5


def test_select_reviewers_fallback_on_bad_schema(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """LLM 返回了 JSON 但 reviewers 不是列表 → 降级全量。"""
    from co_scientist.modules.m4_critique.orchestrator import select_reviewers

    _patch_orch_llm(monkeypatch, {"reviewers": "novelty, methodology"})
    res = select_reviewers("q", "m")
    assert res["fallback"] is True


def test_resolve_personas_filters_unknown() -> None:
    """resolve_personas 遇到未知名字应忽略,不崩。"""
    from co_scientist.modules.m4_critique.orchestrator import resolve_personas

    personas = resolve_personas(["novelty", "ethics", "devil"])
    names = [p.name for p in personas]
    assert "novelty" in names
    assert "devil" in names
    assert "ethics" not in names


# ============================================================
# Part 3:roundtable 按 Orchestrator 选择跑(end-to-end,LLM 全打桩)
# ============================================================


def _patch_reviewer_llm(monkeypatch: pytest.MonkeyPatch) -> None:
    """把 review_proposal 打桩,返回固定评审卡,不真调 LLM。"""
    from co_scientist.modules.m4_critique import reviewers as reviewers_mod
    from co_scientist.modules.m4_critique import roundtable as roundtable_mod
    from co_scientist.state import CritiqueCard

    def fake_review(persona, *a, **kw):
        return CritiqueCard(
            reviewer=persona.name,
            rating=7,
            confidence=4,
            soundness=4,
            contribution=4,
            presentation=4,
            strengths=["mock"],
            weaknesses=["mock"],
            questions=[],
            limitations=[],
            rationale="mock",
        )

    monkeypatch.setattr(reviewers_mod, "review_proposal", fake_review)
    monkeypatch.setattr(roundtable_mod, "review_proposal", fake_review)


def _patch_meta(monkeypatch: pytest.MonkeyPatch) -> None:
    """把 meta_decide 打桩,返回固定决定。"""
    from co_scientist.modules.m4_critique import roundtable as roundtable_mod

    def fake_meta(cards):
        return {"decision": "accept", "final_rating": 7.0, "reasons": ["mock"]}

    monkeypatch.setattr(roundtable_mod, "meta_decide", fake_meta)


def test_roundtable_uses_orchestrator_choice(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    roundtable 调 Orchestrator 后,应该只跑 Orchestrator 选的那几位。
    这是 "Orchestrator 的选择真的生效" 的核心断言。
    """
    from co_scientist.modules.m4_critique import roundtable as roundtable_mod

    # Orchestrator 只选 devil + novelty(2 个,会被补齐到 3)
    _patch_orch_llm(
        monkeypatch,
        {"reviewers": ["devil", "novelty"], "reason": "test"},
    )
    _patch_reviewer_llm(monkeypatch)
    _patch_meta(monkeypatch)

    cards, meta = asyncio.run(
        roundtable_mod.run_roundtable_async(
            refined_question="test question",
            method_summary="method summary",
        )
    )

    # 至少包含 devil + novelty
    reviewer_names = {c["reviewer"] for c in cards}
    assert "devil" in reviewer_names
    assert "novelty" in reviewer_names
    # Orchestrator 信息透传到了 meta
    assert "orchestrator" in meta
    assert meta["orchestrator"]["fallback"] is False


def test_roundtable_respects_settings_switch(monkeypatch: pytest.MonkeyPatch) -> None:
    """settings.M4_USE_ORCHESTRATOR=False 时应跑全员,不调 Orchestrator。"""
    from co_scientist.config import settings as s
    from co_scientist.modules.m4_critique import roundtable as roundtable_mod

    # 关掉开关
    monkeypatch.setattr(s, "M4_USE_ORCHESTRATOR", False)

    # 故意让 Orchestrator 返回 raise —— 开关关了就不会被调到
    _patch_orch_llm(monkeypatch, RuntimeError("should not be called"))
    _patch_reviewer_llm(monkeypatch)
    _patch_meta(monkeypatch)

    cards, meta = asyncio.run(
        roundtable_mod.run_roundtable_async(
            refined_question="q",
            method_summary="m",
        )
    )

    # 全量 5 个都要跑(ALL_REVIEWERS)
    assert len(cards) == 5
    assert meta["orchestrator"]["fallback"] is False  # 不是 orch 失败,是被开关跳过
    assert "跳过" in meta["orchestrator"]["reason"]
