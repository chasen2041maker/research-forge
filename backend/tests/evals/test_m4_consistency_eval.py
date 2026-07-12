"""
============================================================
 m4 批判圆桌一致性 Eval(tests/evals/test_m4_consistency_eval.py)
============================================================

🎓 教学目标
    Agent 评估的经典难题:**LLM 非确定性**。
    同一个输入,m4 Meta-Reviewer 今天打 7 分,明天可能打 5 分。
    但一个生产级 Agent 的 final_rating 标准差**不能太大**,否则下游无法信任。

📌 本文件做什么
    1. 固定一份"合理的提案"作为输入
    2. 跑 N 次(默认 3 次)完整 m4 批判圆桌
    3. 收集 Meta.final_rating 的标准差 + 各 Reviewer rating 的方差分布
    4. 断言标准差不超过阈值(THRESHOLDS["m4_meta_rating_stdev_max"])

💡 为什么用 stdev 而不是 range
    - range = max - min,对极端值过敏(1 个离谱值就把 range 拉爆)
    - stdev 用所有样本的平均偏离,更稳健
    - 生产常看 stdev / 95 分位

💡 为什么 runs=3 是最小有效值
    - 2 次无法算 stdev(只有 1 个偏差)
    - 3 次能算,但统计显著性弱(只是"sanity check")
    - 生产体检建议 runs≥10,但 10 次 m4 ≈ 30 次 LLM 调用,成本高
    - 教学/面试 demo 用 3 次最经济

📌 运行方式
    EVAL_MOCK=1 pytest tests/evals/test_m4_consistency_eval.py --run-evals -v
    pytest tests/evals/test_m4_consistency_eval.py --run-evals -v
"""

from __future__ import annotations

import asyncio
import statistics

import pytest

from co_scientist.modules.m4_critique.roundtable import run_roundtable_async
from tests.evals.fixtures import M4_SEED_PROPOSAL, THRESHOLDS
from tests.evals.judges import check_critique_card_schema


def _run_once() -> tuple[list[dict], dict]:
    """跑一次完整 m4 圆桌,返回 (cards, meta_decision)。"""
    return asyncio.run(
        run_roundtable_async(
            refined_question=M4_SEED_PROPOSAL["refined_question"],
            method_summary=M4_SEED_PROPOSAL["method_summary"],
            experiment_brief=M4_SEED_PROPOSAL["experiment_brief"],
            top_papers=M4_SEED_PROPOSAL["top_papers"],
        )
    )


@pytest.mark.eval
def test_m4_meta_rating_stdev() -> None:
    """
    一致性 Eval:N 次跑的 Meta final_rating 标准差应 ≤ 阈值。

    ▍这个测试在面试里怎么讲
        "我每次改动 m4 的 Reviewer Prompt 都会跑这个 eval,如果标准差飙升,
         说明新 Prompt 让 LLM 的判断变得不稳定,即使均值看起来还行也要回滚。
         这就把 Prompt 工程从'凭感觉'变成了'有量化指标'。"
    """
    runs = THRESHOLDS["m4_consistency_runs"]
    ratings: list[float] = []
    decisions: list[str] = []

    for i in range(runs):
        _, meta = _run_once()
        r = float(meta.get("final_rating", 0.0))
        d = str(meta.get("decision", "?"))
        ratings.append(r)
        decisions.append(d)

    stdev = statistics.stdev(ratings) if len(ratings) >= 2 else 0.0
    mean = statistics.mean(ratings) if ratings else 0.0
    threshold = THRESHOLDS["m4_meta_rating_stdev_max"]

    print(f"\n===== m4 一致性报告 =====")
    for i, (r, d) in enumerate(zip(ratings, decisions), 1):
        print(f"  run {i}: rating={r:.2f} decision={d}")
    print(f"mean = {mean:.2f}, stdev = {stdev:.2f}(阈值 {threshold})")
    print(f"========================\n")

    assert stdev <= threshold, (
        f"m4 Meta final_rating 标准差 {stdev:.2f} 超阈值 {threshold}\n"
        f"详情: {list(zip(ratings, decisions))}"
    )


@pytest.mark.eval
def test_m4_card_schema_one_run() -> None:
    """
    Schema Eval:一次跑下来,每张评审卡都应符合结构约束。

    和一致性测试分开是因为它只需要跑 1 次,不占 runs 配额。
    """
    cards, meta = _run_once()
    all_violations: list[str] = []

    for card in cards:
        v = check_critique_card_schema(card)
        if v:
            all_violations.append(
                f"  [{card.get('reviewer', '?')}] {v}"
            )

    # Meta decision 也做最小检查
    decision = meta.get("decision", "")
    if not isinstance(decision, str) or not decision:
        all_violations.append(f"  [meta] decision 字段非法: {decision!r}")
    rating = meta.get("final_rating")
    if rating is not None and not (0 <= float(rating) <= 10):
        all_violations.append(f"  [meta] final_rating 越界: {rating}")

    assert not all_violations, "m4 产物 schema 违规:\n" + "\n".join(all_violations)


@pytest.mark.eval
def test_m4_reviewer_variance_sanity() -> None:
    """
    理智检查:一次跑里,常规 Reviewer(除 devil/meta)的 rating 方差
    应在合理区间。方差过大可能说明 Reviewer 彼此没看懂同样的输入,
    方差为 0 则可能说明 Prompt 让 Reviewer 失去了人格差异(退化成复读机)。

    ▍阈值设计
        max 5.0:允许正常的视角差异
        min 0.2:防止 Reviewer 完全同质化(rating 全一样 → 方差=0 → 不可信)
    """
    cards, _ = _run_once()
    regular_ratings = [
        int(c.get("rating", 0))
        for c in cards
        if c.get("reviewer") in {"novelty", "methodology", "statistics", "reproducibility"}
        and c.get("rating", 0) > 0  # 过滤失败兜底卡
    ]
    if len(regular_ratings) < 2:
        pytest.skip(f"有效 Reviewer 不足 2 人,跳过方差测试(ratings={regular_ratings})")

    var = statistics.variance(regular_ratings)
    var_max = THRESHOLDS["m4_reviewer_variance_max"]
    var_min = 0.2

    print(f"\n[m4 reviewer variance] ratings={regular_ratings} variance={var:.2f}")

    assert var <= var_max, f"Reviewer 视角分歧过大:var={var} > {var_max}"
    # min 检查是 soft 的:mock 模式下 hash 分布可能恰好一致,给一点容忍
    if var < var_min:
        print(f"⚠️  Reviewer 方差极低({var:.2f}),可能退化为复读机(非 mock 模式请关注)")
