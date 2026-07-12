"""
============================================================
 模块单元测试(m1-m8)—— 不调真实 LLM
============================================================

🎓 教学目标
    上一版只有 test_smoke.py 验了"能 import、图能 build",核心 8 个模块
    一行业务逻辑都没测。这里补上每个模块至少 1-2 个用例,做到:
      - 用 FakeLLM 替掉 get_llm,不花 token、不依赖网络
      - 覆盖"正常路径" + 一个"LLM 返回脏数据"的边界
      - 直接测模块的纯函数,不走 LangGraph 节点(节点由 test_smoke 的
        graph.build 侧面保障)

📌 设计决策
    1. 统一在 monkeypatch 里替换 `co_scientist.llm.get_llm`,不侵入业务代码
    2. FakeLLM 用"per-purpose 返回值表"实现,同一个测试里不同 purpose
       (如 m7_style_guide vs m7_write_method)可以回不同假响应
    3. 不测 m2 外部数据源(arXiv/OpenAlex 联网),只测 query_rewriter
       和 fusion —— 前者已有 test_smoke 覆盖 fusion,这里补改写

------------------------------------------------------------
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

import pytest


# ============================================================
# FakeLLM:一个能按 purpose 返回不同结果的假客户端
# ============================================================
class FakeLLM:
    """
    最小可用的 LLM 桩。

    用法:
        fake = FakeLLM({
            "m1_check_specificity": {"specific": True, "next_question": ""},
            "m1_build_pico": {"refined_question": "...", "population": "..."},
        })
        monkeypatch.setattr("co_scientist.llm.get_llm", lambda role="chat": fake)

    设计要点:
      - chat_json:按 kwargs["purpose"] 查返回值,没查到抛错(测试暴露遗漏 tag)
      - chat:返回一个 dict 当 LLMResponse,带 content 字段,和真实客户端一致
      - calls:记录所有调用参数,断言时可以检查 purpose 是否被打上
    """

    def __init__(self, responses: dict[str, Any]) -> None:
        self._responses = responses
        self.calls: list[dict[str, Any]] = []

    def _lookup(self, purpose: str) -> Any:
        if purpose not in self._responses:
            raise AssertionError(
                f"FakeLLM 未配置 purpose={purpose!r} 的响应,"
                f"已知: {list(self._responses.keys())}"
            )
        return self._responses[purpose]

    def chat_json(self, **kwargs: Any) -> dict:
        purpose = kwargs.get("purpose", "")
        self.calls.append({"method": "chat_json", **kwargs})
        return dict(self._lookup(purpose))

    def chat(self, **kwargs: Any) -> dict:
        purpose = kwargs.get("purpose", "")
        self.calls.append({"method": "chat", **kwargs})
        resp = self._lookup(purpose)
        # 真实 LLMResponse 是 TypedDict,这里直接回 dict 等价
        if isinstance(resp, str):
            return {"content": resp, "input_tokens": 0, "output_tokens": 0}
        return resp


def _install_fake(monkeypatch: pytest.MonkeyPatch, fake: FakeLLM) -> None:
    """把 fake 装到所有模块的 get_llm 入口。"""
    # 业务模块 from co_scientist.llm import get_llm,这里打到包根上即可
    monkeypatch.setattr("co_scientist.llm.get_llm", lambda role="chat": fake)
    # 个别模块是 from co_scientist.llm import get_llm 后直接 get_llm(),
    # 这种写法会把名字绑到模块本地,需要逐个打补丁:
    for mod in [
        "co_scientist.modules.m1_refiner.refiner",
        "co_scientist.modules.m2_retriever.query_rewriter",
        "co_scientist.modules.m3_kg.kg_builder",
        "co_scientist.modules.m4_critique.reviewers",
        "co_scientist.modules.m4_critique.roundtable",
        "co_scientist.modules.m5_experiment.designer",
        "co_scientist.modules.m6_code.code_gen",
        "co_scientist.modules.m7_writer.writer",
    ]:
        monkeypatch.setattr(f"{mod}.get_llm", lambda role="chat": fake, raising=False)


# ============================================================
# M1:问题精炼
# ============================================================


def test_m1_check_specificity_true(monkeypatch: pytest.MonkeyPatch) -> None:
    """
    正常路径:LLM 判定问题够具体 → 返回 (True, "")。
    """
    fake = FakeLLM({"m1_check_specificity": {"specific": True, "next_question": ""}})
    _install_fake(monkeypatch, fake)

    from co_scientist.modules.m1_refiner.refiner import check_specificity

    ok, follow = check_specificity("RAG 如何减少幻觉,在 NQ 数据集上评估 EM/F1")
    assert ok is True
    assert follow == ""
    # purpose 必须被打上,否则 cost_tracker 聚合无法归类
    assert fake.calls[-1]["purpose"] == "m1_check_specificity"


def test_m1_build_pico_fills_refined_question(monkeypatch: pytest.MonkeyPatch) -> None:
    """边界:LLM 漏返 refined_question → 应回落到 raw_question。"""
    fake = FakeLLM({
        "m1_build_pico": {
            "population": "LLM",
            "intervention": "RAG",
            "comparison": "无 RAG",
            "outcome": "EM/F1",
            # 故意不给 refined_question
        }
    })
    _install_fake(monkeypatch, fake)

    from co_scientist.modules.m1_refiner.refiner import build_pico

    pico = build_pico("原始问题", [])
    assert pico["refined_question"] == "原始问题"  # 兜底生效
    assert pico["intervention"] == "RAG"


def test_m1_refine_node_never_reads_stdin_without_clarification(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """问题不够具体且前端未给补充时,节点记录待澄清问题但不读后端终端。"""
    fake = FakeLLM({
        "m1_check_specificity": {
            "specific": False,
            "next_question": "你关注哪个数据集和指标?",
        },
        "m1_build_pico": {
            "population": "LLM",
            "intervention": "RAG",
            "comparison": "baseline",
            "outcome": "EM/F1",
            "refined_question": "RAG factuality evaluation",
        },
    })
    _install_fake(monkeypatch, fake)
    monkeypatch.setattr("builtins.input", lambda *_a, **_kw: (_ for _ in ()).throw(
        AssertionError("M1 must not read stdin")
    ))

    from co_scientist.modules.m1_refiner.refiner import refine_question_node

    patch = refine_question_node({
        "raw_question": "我想做 RAG",
        "pico": {},
        "metadata": {},
    })  # type: ignore[arg-type]

    assert patch["pico"]["refined_question"] == "RAG factuality evaluation"
    assert patch["pico"]["clarifications"] == []
    assert patch["metadata"]["m1_pending_clarification"] == "你关注哪个数据集和指标?"


def test_m1_refine_node_uses_frontend_clarifications(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """前端/API 给出的 M1 补充说明会进入 PICO 构建。"""
    fake = FakeLLM({
        "m1_check_specificity": {
            "specific": False,
            "next_question": "请补充实验约束",
        },
        "m1_build_pico": {
            "population": "LLM",
            "intervention": "RAG",
            "comparison": "no retrieval",
            "outcome": "F1",
            "refined_question": "Evaluate RAG on HotpotQA with F1",
        },
    })
    _install_fake(monkeypatch, fake)
    monkeypatch.setattr("builtins.input", lambda *_a, **_kw: (_ for _ in ()).throw(
        AssertionError("M1 must not read stdin")
    ))

    from co_scientist.modules.m1_refiner.refiner import refine_question_node

    patch = refine_question_node({
        "raw_question": "我想做 RAG",
        "pico": {},
        "metadata": {
            "m1_clarifications": [
                {"q": "前端补充说明", "a": "用 HotpotQA,指标 F1,baseline 无检索"}
            ]
        },
    })  # type: ignore[arg-type]

    assert patch["pico"]["clarifications"] == [
        {"q": "前端补充说明", "a": "用 HotpotQA,指标 F1,baseline 无检索"}
    ]
    build_call = fake.calls[-1]["messages"][-1]["content"]
    assert "HotpotQA" in build_call


# ============================================================
# M2:Query 改写
# ============================================================


def test_m2_rewrite_queries_normal(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakeLLM({
        "m2_query_rewrite": {
            "queries": [
                "retrieval augmented generation hallucination",
                "RAG factuality evaluation",
                "LLM knowledge grounding",
            ]
        }
    })
    _install_fake(monkeypatch, fake)

    from co_scientist.modules.m2_retriever.query_rewriter import rewrite_queries

    qs = rewrite_queries("RAG 减少幻觉", n=5)
    assert len(qs) == 3
    assert all(isinstance(q, str) and q for q in qs)


def test_m2_rewrite_queries_bad_format_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    """
    边界:LLM 返回非 list(如 str)→ 回落到原问题,不应抛异常。
    这是线上最常踩的坑:LLM 偶发输出结构漂移,业务层必须兜底。
    """
    fake = FakeLLM({"m2_query_rewrite": {"queries": "not-a-list"}})
    _install_fake(monkeypatch, fake)

    from co_scientist.modules.m2_retriever.query_rewriter import rewrite_queries

    qs = rewrite_queries("RAG 减少幻觉")
    assert qs == ["RAG 减少幻觉"]


# ============================================================
# M2:Embedding 重排(降级兜底)
# ============================================================


def test_m2_rerank_falls_back_on_embed_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    """
    embedding API 挂掉时,rerank 必须回退到原 RRF 顺序,不抛异常。
    这是生产稳定性的关键:任何增强步骤失败都不应该拖垮主流程。
    """
    from co_scientist.llm.deepseek import DeepSeekClient
    from co_scientist.modules.m2_retriever import embedding_rerank
    from co_scientist.state import Paper

    # 继承 DeepSeekClient 但跳过 __init__(不走真的 API key 加载),
    # 只 override embed 抛异常
    class BoomDeepSeek(DeepSeekClient):
        def __init__(self) -> None:  # 故意不调 super().__init__()
            pass

        def embed(self, *a: Any, **kw: Any) -> list[list[float]]:  # type: ignore[override]
            raise RuntimeError("embedding down")

    monkeypatch.setattr(embedding_rerank, "get_llm", lambda role="chat": BoomDeepSeek())

    papers = [
        Paper(id="a", title="A", doi="1", source="s", score=0.9),
        Paper(id="b", title="B", doi="2", source="s", score=0.5),
    ]
    out = embedding_rerank.rerank_by_embedding("q", papers)
    # 顺序不变(原 list 返回)
    assert [p["id"] for p in out] == ["a", "b"]


def test_m2_cosine_basics() -> None:
    """纯数值函数:平行向量→1,正交向量→0,零向量→0。"""
    from co_scientist.modules.m2_retriever.embedding_rerank import _cosine

    assert _cosine([1.0, 2.0], [1.0, 2.0]) == pytest.approx(1.0)
    assert _cosine([1.0, 0.0], [0.0, 1.0]) == pytest.approx(0.0)
    assert _cosine([0.0, 0.0], [1.0, 1.0]) == 0.0


# ============================================================
# M3:三元组抽取
# ============================================================


def test_m3_triple_extract_filters_invalid_relation(monkeypatch: pytest.MonkeyPatch) -> None:
    """
    LLM 返回 3 条三元组,其中 1 条关系类型不在白名单 → 应被丢弃。
    验证 VALID_RELATIONS 过滤生效。
    """
    fake = FakeLLM({
        "m3_triple_extract": {
            "triples": [
                {"head": "BERT", "relation": "improves", "tail": "GLUE"},
                {"head": "RoBERTa", "relation": "uses", "tail": "BERT"},
                {"head": "X", "relation": "tastes_like", "tail": "Y"},  # 非法关系
            ]
        }
    })
    _install_fake(monkeypatch, fake)

    import asyncio

    from co_scientist.modules.m3_kg.kg_builder import _extract_triples_from_paper
    from co_scientist.state import Paper

    paper = Paper(
        id="p1",
        title="BERT improves GLUE",
        abstract="BERT is a pretrained model...",
        doi="",
        source="test",
    )
    triples = asyncio.run(_extract_triples_from_paper(paper))
    rels = {t["relation"] for t in triples}
    assert "improves" in rels and "uses" in rels
    assert "tastes_like" not in rels


# ============================================================
# M4:评审圆桌
# ============================================================


def test_m4_review_proposal_returns_card(monkeypatch: pytest.MonkeyPatch) -> None:
    """单个 Reviewer 走通,产出 CritiqueCard,评分字段 int 化。"""
    from co_scientist.modules.m4_critique.reviewers import NOVELTY_REVIEWER

    fake = FakeLLM({
        f"m4_review_{NOVELTY_REVIEWER.name}": {
            "rating": "8",  # LLM 有时回字符串,review_proposal 会 int() 掉
            "strengths": ["新颖角度"],
            "weaknesses": ["缺少对比"],
            "questions": [],
            "suggestions": [],
        }
    })
    _install_fake(monkeypatch, fake)

    from co_scientist.modules.m4_critique.reviewers import review_proposal

    card = review_proposal(NOVELTY_REVIEWER, "研究问题", "方法摘要")
    assert card["reviewer"] == "novelty"
    assert isinstance(card["rating"], int)
    assert card["rating"] == 8


def test_m4_review_proposal_swallows_llm_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """
    Reviewer LLM 调用失败 → 返回 rating=0 的空卡(而不是抛出)。
    保证并行 gather 里一个失败不拖垮整场评审。
    """
    from co_scientist.modules.m4_critique.reviewers import NOVELTY_REVIEWER

    class BoomLLM:
        def chat_json(self, **kwargs: Any) -> dict:
            raise RuntimeError("llm down")

    monkeypatch.setattr(
        "co_scientist.modules.m4_critique.reviewers.get_llm",
        lambda role="chat": BoomLLM(),
    )

    from co_scientist.modules.m4_critique.reviewers import review_proposal

    card = review_proposal(NOVELTY_REVIEWER, "q", "m")
    assert card["rating"] == 0  # 占位,下游 variance 会过滤


# ============================================================
# M5:实验设计
# ============================================================


def test_m5_design_experiment_builds_plan(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakeLLM({
        "m5_design": {
            "name": "RAG-vs-Baseline",
            "datasets": ["NQ"],
            "baselines": ["LLM-only", "RAG-BM25"],
            "metrics": ["EM", "F1"],
            "expected_results": "EM↑3pt",
            "ablations": ["去掉 reranker"],
            "statistical_test": {"name": "paired t-test"},
        }
    })
    _install_fake(monkeypatch, fake)

    from co_scientist.modules.m5_experiment.designer import design_experiment, self_check

    exp, variant_meta = design_experiment("RAG 幻觉", {"refined_question": "RAG 幻觉"})
    assert exp["name"] == "RAG-vs-Baseline"
    # 未注册任何 prompt 变体 → variant_meta 为空 dict
    assert variant_meta == {}
    # self_check 在"五项齐全"时应返回空列表
    assert self_check(exp) == []


def test_m5_self_check_reports_missing() -> None:
    """纯函数,独立测。缺基线 + 缺显著性检验 → 两项都被标出来。"""
    from co_scientist.modules.m5_experiment.designer import self_check
    from co_scientist.state import Experiment

    exp = Experiment(
        name="x",
        datasets=["D"],
        baselines=[],  # 缺
        metrics=["acc"],
        expected_results="",
        ablations=["A"],
        statistical_test={},  # 缺
    )
    missing = self_check(exp)
    assert "至少 2 个基线" in missing
    assert "有显著性检验" in missing


# ============================================================
# M6:代码生成
# ============================================================


def test_m6_generate_code_extracts_requirements(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakeLLM({
        "m6_generate_code": {
            "files": {
                "main.py": "print('hi')\n",
                "requirements.txt": "torch\n# comment\ntransformers==4.40\n",
                "README.md": "# demo",
            }
        }
    })
    _install_fake(monkeypatch, fake)

    from co_scientist.modules.m6_code.code_gen import generate_code
    from co_scientist.state import Experiment

    exp = Experiment(
        name="demo", datasets=[], baselines=[], metrics=[],
        expected_results="", ablations=[], statistical_test={},
    )
    artifact, skills = generate_code(exp)
    assert "main.py" in artifact["files"]
    # 注释行应被过滤
    assert "torch" in artifact["requirements"]
    assert "transformers==4.40" in artifact["requirements"]
    assert not any(r.startswith("#") for r in artifact["requirements"])


def test_m6_dry_run_detects_syntax_error(tmp_path: Path) -> None:
    """dry_run 只做语法检查,故意写个坏文件看有没有抓到。"""
    (tmp_path / "main.py").write_text("def broken(:\n    pass\n", encoding="utf-8")

    from co_scientist.modules.m6_code.code_gen import dry_run

    result = dry_run(tmp_path)
    assert result["ok"] is False
    assert any("main.py" in e for e in result["syntax_errors"])


# ============================================================
# M7:论文写作
# ============================================================


def test_m7_write_section_passes_through(monkeypatch: pytest.MonkeyPatch) -> None:
    """write_section 走 chat(非 chat_json),返回 content 字段字符串。"""
    section = "abstract"
    fake = FakeLLM({
        f"m7_write_{section}": {"content": "本文提出了一种新方法..."},
    })
    _install_fake(monkeypatch, fake)

    from co_scientist.modules.m7_writer.writer import write_section
    from co_scientist.state import Paper

    refs = [Paper(id="p1", title="T", doi="", source="s")]
    text = write_section(section, "150 词摘要", {"tone": "academic"}, "Q", {}, refs)
    assert text.startswith("本文提出")


# ============================================================
# M8:分叉管理
# ============================================================


def test_m8_fork_manager_crud(tmp_path: Path) -> None:
    """
    ForkManager 基本 CRUD。
    用 tmp_path 隔离 SQLite,避免污染项目 data/forks.db。
    """
    from co_scientist.modules.m8_replay.fork_manager import ForkManager

    fm = ForkManager(db_path=tmp_path / "forks.db")

    # 根分叉
    root = fm.create_fork(parent_fork_id="", branch_node="root", description="baseline")
    assert root.status == "running"
    assert root.parent_fork_id == ""

    # 子分叉
    child = fm.create_fork(
        parent_fork_id=root.fork_id, branch_node="m4_critique", description="变体 A"
    )
    assert child.parent_fork_id == root.fork_id

    # 更新状态 + 评分
    fm.update_status(root.fork_id, "done", final_rating=8.5)

    rows = fm.list_forks()
    assert len(rows) == 2
    root_row = next(r for r in rows if r["fork_id"] == root.fork_id)
    assert root_row["status"] == "done"
    assert root_row["final_rating"] == 8.5


def test_m8_build_tree_nests_children(tmp_path: Path) -> None:
    """
    build_tree 返回 {parent_fork_id 或 "root": [child_id, ...]} 的映射。
    根分叉(parent 为空)会被挂在 "root" 键下。
    """
    from co_scientist.modules.m8_replay.fork_manager import ForkManager

    fm = ForkManager(db_path=tmp_path / "forks.db")
    a = fm.create_fork("", "root", "A")
    b = fm.create_fork(a.fork_id, "m4", "A→B")
    c = fm.create_fork(a.fork_id, "m5", "A→C")

    tree = fm.build_tree()
    # a 是根分叉,挂在 "root" 键下
    assert a.fork_id in tree.get("root", [])
    # b 和 c 都应挂在 a 下
    children = set(tree.get(a.fork_id, []))
    assert children == {b.fork_id, c.fork_id}
