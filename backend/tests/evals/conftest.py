"""
============================================================
 Eval 套件 pytest 配置(tests/evals/conftest.py)
============================================================

🎓 教学目标
    Eval 跟普通单元测试的最大区别:**会真的调 LLM,要花钱**。
    所以:
      1. 默认 **skip** 所有 eval 测试,避免 `pytest` 一键全跑把钱烧光
      2. 用 `--run-evals` flag 显式打开真实 LLM 调用
      3. 提供 `EVAL_MOCK=1` 环境变量用**假 LLM** 跑,验证 eval 基础设施是否健康
         (CI 用这个模式,不花钱也能发现 eval 代码本身的 bug)

📌 三种运行模式

    A. 默认(CI 友好):pytest tests/evals/
       → 全部 skip,瞬间过

    B. Mock 模式:EVAL_MOCK=1 pytest tests/evals/ --run-evals
       → 所有 LLM 调用被打桩,测试真跑,但不花钱
       → 验证 eval 代码本身是否健康

    C. 真实模式:pytest tests/evals/ --run-evals
       → 调真实 LLM,跑完出质量报告
       → 改了 m4 批判逻辑想验证效果时跑这个

💡 为什么不直接把 eval 文件名改成 `eval_*.py` 让 pytest 默认不收集
    因为我们希望 `pytest --run-evals` 能从 tests/evals/ 下**自动发现**新写的 eval 文件,
    不必手动注册。用 marker + skip 的方式既能默认 skip,又能一键打开。
"""

from __future__ import annotations

import os
from typing import Any

import pytest


# ------------------------------------------------------------
# 命令行选项:--run-evals
# ------------------------------------------------------------
def pytest_addoption(parser: pytest.Parser) -> None:
    """
    注册 --run-evals 命令行参数。

    不传这个 flag 时,所有打了 @pytest.mark.eval 的测试都会被 skip。
    """
    parser.addoption(
        "--run-evals",
        action="store_true",
        default=False,
        help="真正跑 eval 测试(会调 LLM,可能产生费用)",
    )


def pytest_configure(config: pytest.Config) -> None:
    """注册 `eval` marker,让 pytest 不报 Unknown marker 警告。"""
    config.addinivalue_line(
        "markers",
        "eval: Agent eval 测试(调真实 LLM,需要 --run-evals 才真跑)",
    )


def pytest_collection_modifyitems(
    config: pytest.Config, items: list[pytest.Item]
) -> None:
    """
    不带 --run-evals 时,给所有 eval 测试打 skip 标记。

    为什么在 collection 阶段做而不是在 setup 阶段?
      - 在 collection 阶段 skip,pytest 输出会直接显示 "s"(skipped),看起来很快;
      - 在 setup 阶段 skip,每个测试要先进入 setup 才能跳过,体感慢很多。
    """
    if config.getoption("--run-evals"):
        return
    skip_marker = pytest.mark.skip(
        reason="默认 skip eval 测试;传 --run-evals 打开"
    )
    for item in items:
        if "eval" in item.keywords:
            item.add_marker(skip_marker)


# ------------------------------------------------------------
# Mock 模式:把 get_llm 换成假客户端
# ------------------------------------------------------------
@pytest.fixture(autouse=True)
def _eval_mock_mode(monkeypatch: pytest.MonkeyPatch, request: pytest.FixtureRequest):
    """
    如果设置了 EVAL_MOCK=1,把所有模块里的 get_llm 替换成返回固定数据的假客户端。

    为什么 autouse=True:
      - 每个 eval 测试都默认吃 mock 开关,不必每个测试手动声明
      - 没开 EVAL_MOCK 就是 no-op,不影响真实模式

    设计要点:
      - Mock LLM 按 purpose 字段返回不同的打桩数据
      - 这样 m1_build_pico / m4_review_novelty 等不同调用各取所需
    """
    # 只对打了 eval marker 的测试生效,其他单元测试不受影响
    if "eval" not in request.keywords:
        yield
        return

    if os.environ.get("EVAL_MOCK") != "1":
        yield
        return

    # ---- 安装假 LLM ----
    from co_scientist.llm import factory as llm_factory

    class _MockResponse(dict):
        """模拟 LLMResponse 字典。"""

    class FakeLLM:
        """
        按 purpose 分派的假客户端。

        只实现 chat / chat_json,其他方法按需添加。
        """

        model_family = "mock"
        default_model = "mock"

        def chat(self, messages, *, purpose="", **kwargs) -> _MockResponse:
            # 普通 chat 调用(evaluator 自己要用),返回一个兜底打分 JSON
            return _MockResponse(
                content='{"overall_score": 4, "rationale": "mock response"}',
                input_tokens=100,
                output_tokens=20,
                cost_usd=0.0,
            )

        def chat_json(self, messages, *, purpose="", **kwargs) -> dict[str, Any]:
            p = purpose or ""
            # m1 相关
            if "m1_check" in p or "check_specificity" in p:
                return {"specific": True, "next_question": ""}
            if "m1_build" in p or "build_pico" in p:
                return {
                    "population": "大语言模型",
                    "intervention": "RAG 检索增强",
                    "comparison": "无检索 baseline",
                    "outcome": "幻觉率",
                    "refined_question": "RAG 能否降低 LLM 在开放域问答中的幻觉率?",
                }
            # m4 review 相关:根据 purpose 里的角色名返回略有差异的分数
            if p.startswith("m4_review_"):
                role = p.replace("m4_review_", "")
                # 用 hash 制造"有差异但可复现"的分数,方差测试能区分
                base = 6 + (hash(role) % 3)  # 6 / 7 / 8
                return {
                    "soundness": 4,
                    "contribution": 3,
                    "presentation": 4,
                    "strengths": ["mock strength"],
                    "weaknesses": ["mock weakness"],
                    "questions": [],
                    "limitations": [],
                    "rating": base,
                    "confidence": 4,
                    "rationale": f"mock rationale for {role}",
                }
            if "m4_meta" in p:
                return {
                    "decision": "accept_with_revision",
                    "final_rating": 7.0,
                    "reasons": ["mock reason"],
                }
            # judge 评分(LLM-as-judge)
            if "judge" in p:
                return {"overall_score": 4, "rationale": "mock judge: looks ok"}
            # 兜底:返回空字典,具体测试应避免走到这里
            return {}

    def _mock_get_llm(role: str = "chat"):
        return FakeLLM()

    monkeypatch.setattr(llm_factory, "get_llm", _mock_get_llm)
    monkeypatch.setattr("co_scientist.llm.get_llm", _mock_get_llm, raising=False)

    # ---- 关键:各模块是 `from co_scientist.llm import get_llm`,
    # 绑定的是各自命名空间里的本地引用,patch factory 不会影响它们。
    # 所以要遍历所有消费点,逐个把它们命名空间里的 get_llm 替换掉。
    consumer_modules = [
        "co_scientist.modules.m1_refiner.refiner",
        "co_scientist.modules.m2_retriever.embedding_rerank",
        "co_scientist.modules.m2_retriever.query_rewriter",
        "co_scientist.modules.m3_kg.kg_builder",
        "co_scientist.modules.m4_critique.reviewers",
        "co_scientist.modules.m4_critique.roundtable",
        "co_scientist.modules.m5_experiment.designer",
        "co_scientist.modules.m6_code.code_gen",
        "co_scientist.modules.m7_writer.writer",
        "co_scientist.appendix.evolve.memory",
        "co_scientist.appendix.evolve.prompt_ab",
        "co_scientist.appendix.adversarial.red_blue",
        "tests.evals.judges",
    ]
    for mod_path in consumer_modules:
        monkeypatch.setattr(f"{mod_path}.get_llm", _mock_get_llm, raising=False)

    yield
