"""
============================================================
 Phase B 测试:M0 候选课题发现 + M3 GapCard 升级 + M5 GapCard 注入
============================================================

🎓 教学目标
    Phase B 引入了三件新能力,本文件用 FakeLLM 桩验证关键路径与降级:
      - M0 discover_topics:LLM 返回多张 TopicCard → 解析、补 topic_id、按 score 排序
      - M0 LLM 失败 → 降级返回 [],不抛异常
      - M3 build_gap_cards:启发式 gap 节点 + 论文摘要 → list[GapCard]
      - M3 build_gap_cards 失败 → 降级返回 [],主流程不阻塞
      - M5 design_experiment:GapCard 中的 datasets/baselines/metrics 注入 prompt 后,
        生成的实验方案 prompt 里能看到这些先验
"""

from __future__ import annotations

from typing import Any

import pytest


# ============================================================
# FakeLLM(与 test_modules.py 中相同,但不依赖那边以避免循环 import)
# ============================================================
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
        "co_scientist.modules.m0_topic_discovery.discovery",
        "co_scientist.modules.m3_kg.kg_builder",
        "co_scientist.modules.m5_experiment.designer",
    ]:
        monkeypatch.setattr(f"{mod}.get_llm", lambda role="chat": fake, raising=False)


# ============================================================
# M0:候选课题发现器
# ============================================================

def test_m0_discover_topics_normal(monkeypatch: pytest.MonkeyPatch) -> None:
    """LLM 返回 3 张 TopicCard → 全部带上 topic_id,按 score 降序。"""
    fake = FakeLLM({
        "m0_discover": {
            "topics": [
                {"title": "RAG 在法律问答的鲁棒性",
                 "research_direction": "...", "candidate_question": "...",
                 "score": 7.5},
                {"title": "GraphRAG 多跳推理",
                 "research_direction": "...", "candidate_question": "...",
                 "score": 8.8},
                {"title": "LLM 工具调用幻觉",
                 "research_direction": "...", "candidate_question": "...",
                 "score": 6.0},
            ]
        }
    })
    _install_fake(monkeypatch, fake)

    from co_scientist.modules.m0_topic_discovery import discover_topics

    cards = discover_topics("我想做 RAG 相关研究", k=3)
    assert len(cards) == 3
    assert all(c["topic_id"].startswith("tc-") for c in cards)
    assert cards[0]["score"] == 8.8  # 排序生效
    assert cards[-1]["score"] == 6.0


def test_m0_discover_topics_llm_failure_returns_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    """LLM 调用挂了 → 返回 [] 让主流程降级到无 M0 模式。"""
    class BoomLLM(FakeLLM):
        def chat_json(self, **kwargs: Any) -> dict:
            raise RuntimeError("API down")

    fake = BoomLLM({})
    _install_fake(monkeypatch, fake)

    from co_scientist.modules.m0_topic_discovery import discover_topics

    cards = discover_topics("xxx")
    assert cards == []


def test_m0_node_skips_when_topic_cards_already_present(monkeypatch: pytest.MonkeyPatch) -> None:
    """state 已有 topic_cards(断点续跑)→ 节点应空操作不再调 LLM。"""
    fake = FakeLLM({})  # 不配响应,真调用就会 AssertionError
    _install_fake(monkeypatch, fake)

    from co_scientist.modules.m0_topic_discovery import topic_discovery_node

    state = {
        "raw_question": "xxx",
        "topic_cards": [{"topic_id": "tc-old", "title": "已有"}],
        "metadata": {},
    }
    patch = topic_discovery_node(state)  # type: ignore[arg-type]
    assert patch == {}
    assert fake.calls == []


def test_user_select_topic_uses_metadata_without_stdin(monkeypatch: pytest.MonkeyPatch) -> None:
    """M0 选择节点从 metadata.selected_topic_id 取前端选择,不读后端终端。"""
    monkeypatch.setattr("builtins.input", lambda *_a, **_kw: (_ for _ in ()).throw(
        AssertionError("M0 must not read stdin")
    ))

    from co_scientist.graph import user_select_topic_node

    state = {
        "raw_question": "原始兴趣",
        "topic_cards": [
            {
                "topic_id": "tc-a",
                "title": "A",
                "candidate_question": "Question A",
                "score": 9.0,
            },
            {
                "topic_id": "tc-b",
                "title": "B",
                "candidate_question": "Question B",
                "score": 5.0,
            },
        ],
        "metadata": {"selected_topic_id": "tc-b"},
    }
    patch = user_select_topic_node(state)  # type: ignore[arg-type]

    assert patch["current_topic_id"] == "tc-b"
    assert patch["raw_question"] == "Question B"


# ============================================================
# M3:GapCard 升级
# ============================================================

def test_m3_build_gap_cards_normal(monkeypatch: pytest.MonkeyPatch) -> None:
    """有 gap_nodes + papers → LLM 返回 GapCard list,每张带 gap_id 与 evidence_level。"""
    fake = FakeLLM({
        "m3_gap_card": {
            "gap_cards": [
                {"title": "RAG 在多跳问答上不稳",
                 "problem": "...",
                 "evidence_papers": ["p1", "p2"],
                 "datasets": ["HotpotQA"],
                 "baselines": ["DPR", "Contriever"],
                 "metrics": ["EM", "F1"],
                 "novelty_score": 7.0,
                 "feasibility_score": 8.0,
                 "evidence_level": "high"},
                {"title": "工具使用幻觉",
                 "problem": "...",
                 "novelty_score": 5.0,
                 "feasibility_score": 6.0,
                 "evidence_level": "INVALID_VALUE"},  # 应被归一化
            ]
        }
    })
    _install_fake(monkeypatch, fake)

    from co_scientist.modules.m3_kg.kg_builder import build_gap_cards
    from co_scientist.state import Paper

    papers = [
        Paper(id="p1", title="A", abstract="abs1"),
        Paper(id="p2", title="B", abstract="abs2"),
    ]
    cards = build_gap_cards("研究问题", ["RAG", "tool_use"], papers, top_n=2, max_cards=5)
    assert len(cards) == 2
    # 排序按 novelty * feasibility:7*8=56 > 5*6=30,所以第一个是 RAG
    assert cards[0]["title"].startswith("RAG")
    # evidence_level 归一化到合法值
    assert cards[1]["evidence_level"] == "medium"
    # gap_id 自动生成
    assert all(c["gap_id"].startswith("gc-") for c in cards)


def test_m3_build_gap_cards_no_papers_falls_back(monkeypatch: pytest.MonkeyPatch) -> None:
    """没有论文时 → 降级为半空 GapCard,不调 LLM。"""
    fake = FakeLLM({})  # 不配响应,真调 LLM 会爆
    _install_fake(monkeypatch, fake)

    from co_scientist.modules.m3_kg.kg_builder import build_gap_cards

    cards = build_gap_cards("xxx", ["RAG"], [], max_cards=3)
    assert len(cards) == 1
    assert cards[0]["title"] == "RAG"
    assert cards[0]["evidence_level"] == "low"
    assert fake.calls == []  # 真没调 LLM


def test_m3_build_gap_cards_llm_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    """LLM 失败 → 返回 [],主流程降级到只用 research_gaps。"""
    class BoomLLM(FakeLLM):
        def chat_json(self, **kwargs: Any) -> dict:
            raise RuntimeError("LLM 5xx")

    fake = BoomLLM({})
    _install_fake(monkeypatch, fake)

    from co_scientist.modules.m3_kg.kg_builder import build_gap_cards
    from co_scientist.state import Paper

    cards = build_gap_cards("xxx", ["RAG"], [Paper(id="p1", title="t", abstract="a")])
    assert cards == []


# ============================================================
# M5:GapCard 注入实验设计
# ============================================================

def test_m5_design_experiment_uses_gap_card_priors(monkeypatch: pytest.MonkeyPatch) -> None:
    """
    传入 GapCard → 生成 prompt 时 datasets/baselines/metrics 应出现在 user message 里。
    用 calls 历史检查 LLM 收到的 user 内容。
    """
    fake = FakeLLM({
        "m5_design": {
            "name": "exp1",
            "datasets": [{"name": "HotpotQA"}],
            "baselines": ["DPR", "Contriever"],
            "metrics": ["EM"],
            "expected_results": "...",
            "ablations": ["abl1"],
            "statistical_test": {"name": "paired_t"},
        }
    })
    _install_fake(monkeypatch, fake)

    # PromptABTester 的 best_for 可能查 SQLite,这里强制 mock 返回 None,避免触发 db
    monkeypatch.setattr(
        "co_scientist.modules.m5_experiment.designer.PromptABTester",
        lambda: type("T", (), {"best_for": lambda self, name: None})(),
    )

    from co_scientist.modules.m5_experiment.designer import design_experiment
    from co_scientist.state import GapCard

    gap = GapCard(
        gap_id="gc-1",
        title="RAG 多跳",
        missing_piece="缺乏跨文档证据聚合",
        datasets=["HotpotQA", "2WikiMQA"],
        baselines=["DPR"],
        metrics=["EM", "F1"],
    )
    exp, variant_meta = design_experiment(
        "RAG 在多跳问答上的鲁棒性",
        {"refined_question": "..."},
        gap_card=gap,
    )

    # 实验方案被采纳
    assert exp["name"] == "exp1"
    assert variant_meta == {}  # 没用 A/B 变体

    # 关键:LLM user 内容应包含 GapCard 先验
    last_user = fake.calls[-1]["messages"][-1]["content"]
    assert "HotpotQA" in last_user
    assert "DPR" in last_user
    assert "缺乏跨文档证据聚合" in last_user
    assert "GapCard 先验" in last_user
