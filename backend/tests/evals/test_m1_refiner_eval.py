"""
============================================================
 m1 Refiner 质量 Eval(tests/evals/test_m1_refiner_eval.py)
============================================================

🎓 教学目标
    对 PICO 精炼节点做两层评估:
      1. **Schema Eval**(硬):字段必须齐全、范围合法
      2. **Quality Eval**(软):LLM-as-Judge 按 rubric 给 1-5 分

📌 运行方式

    # 默认 skip(CI 友好)
    pytest tests/evals/test_m1_refiner_eval.py

    # Mock 模式:不调真 LLM,验证测试代码本身健康
    EVAL_MOCK=1 pytest tests/evals/test_m1_refiner_eval.py --run-evals -v

    # 真实模式:调 DeepSeek + Claude Opus(Judge),产生费用
    pytest tests/evals/test_m1_refiner_eval.py --run-evals -v

💡 为什么 schema eval 不用 LLM 做
    schema 是"硬约束",写错了就是 bug,不需要 LLM 判断。LLM-as-Judge 只用在
    "没办法用规则表达的主观质量"上。工程上把两者分开能省 95% 的裁判成本。
"""

from __future__ import annotations

import statistics

import pytest

from co_scientist.modules.m1_refiner.refiner import build_pico
from tests.evals.fixtures import M1_SEED_QUESTIONS, THRESHOLDS
from tests.evals.judges import (
    RUBRIC_M1_PICO,
    check_pico_schema,
    rubric_judge,
)


@pytest.mark.eval
@pytest.mark.parametrize("seed", M1_SEED_QUESTIONS)
def test_m1_schema(seed) -> None:
    """
    Schema Eval:PICO 四个要素 + refined_question 都应非空且合法。

    这是底线 —— 不管 LLM 今天状态好坏,结构错了就是 bug。
    """
    pico = build_pico(seed["raw_question"], clarifications=[])
    violations = check_pico_schema(pico)
    assert not violations, (
        f"PICO schema 违规:\n  问题: {seed['raw_question']}\n  违规: {violations}\n  产物: {pico}"
    )


@pytest.mark.eval
def test_m1_quality_avg_meets_threshold() -> None:
    """
    Quality Eval:LLM-as-Judge 对全部 seed 问题的 PICO 打分,均值应达标。

    为什么看均值而不是逐条断言?
      - 单条可能因 LLM 非确定性偶尔翻车(得 2 分)
      - 均值稳定反映"平均质量",更符合生产里"看 P50 不看最差"的评估直觉
      - 想看最差可以再加一个 min 断言(下一个测试)
    """
    scores: list[int] = []
    details: list[str] = []
    for seed in M1_SEED_QUESTIONS:
        pico = build_pico(seed["raw_question"], clarifications=[])
        product = (
            f"原问题: {seed['raw_question']}\n"
            f"PICO: P={pico.get('population')}, I={pico.get('intervention')}, "
            f"C={pico.get('comparison')}, O={pico.get('outcome')}\n"
            f"refined_question: {pico.get('refined_question')}"
        )
        judged = rubric_judge(product, RUBRIC_M1_PICO, purpose="judge_m1")
        scores.append(judged["overall_score"])
        details.append(
            f"  - [{judged['overall_score']}] {seed['raw_question'][:30]} → {judged['rationale'][:80]}"
        )

    avg = statistics.mean(scores) if scores else 0
    threshold = THRESHOLDS["m1_quality_min_avg"]

    print(f"\n===== m1 Quality Report =====")
    for line in details:
        print(line)
    print(f"均值 = {avg:.2f} / 5(阈值 {threshold})")
    print(f"==============================\n")

    assert avg >= threshold, (
        f"m1 PICO 平均质量不达标: {avg:.2f} < {threshold}\n" + "\n".join(details)
    )


@pytest.mark.eval
def test_m1_no_catastrophic_failures() -> None:
    """
    Quality Eval:没有单条得 1 分的灾难案例。

    为什么和均值测试分开?
      - 均值可以被高分拉起来,掩盖个别灾难
      - 生产里"偶尔输出完全离谱"比"平均水平降 0.2 分"危险得多
      - 所以加一条"底线"断言,两头把关
    """
    min_acceptable = 2  # 允许偶尔 2 分,但不允许 1 分
    failures: list[str] = []
    for seed in M1_SEED_QUESTIONS:
        pico = build_pico(seed["raw_question"], clarifications=[])
        product = f"PICO: {pico}"
        judged = rubric_judge(product, RUBRIC_M1_PICO, purpose="judge_m1_catastrophic")
        if judged["overall_score"] < min_acceptable:
            failures.append(
                f"{seed['raw_question'][:40]} → {judged['overall_score']}分: {judged['rationale'][:100]}"
            )

    assert not failures, f"出现灾难性输出:\n" + "\n".join(failures)
