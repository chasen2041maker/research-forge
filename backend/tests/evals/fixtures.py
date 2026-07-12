"""
============================================================
 Eval 种子数据(tests/evals/fixtures.py)
============================================================

🎓 教学目标
    好的 eval 最关键的是**高质量的 seed 数据集**。
    这里集中管理:
      - 几个代表性的研究问题(给 m1 喂)
      - 一份"完整的提案材料"(给 m4 评审)

💡 为什么把 seed 数据集中在一处而不是散在各 test 文件里
    1. 同一份问题可能被多个 eval 复用(m1 出 PICO,下游 m4 也要用)
    2. 想扩展数据集时改一个文件,所有 eval 自动看到
    3. Eval 数据集是 Agent 项目的"资产",和代码一样应该版本化管理
"""

from __future__ import annotations

# ------------------------------------------------------------
# m1 Refiner 种子问题
# 每条包含:原始问题 + 期望的 PICO 四元素(用于 judge 评估)
# ------------------------------------------------------------
M1_SEED_QUESTIONS = [
    {
        "raw_question": "RAG 如何降低 LLM 幻觉?",
        "expect_hints": {
            "population": "大语言模型",
            "intervention": "检索增强",
            "outcome": "幻觉",
        },
    },
    {
        "raw_question": "小样本场景下,LoRA 微调比全参微调效果好吗?",
        "expect_hints": {
            "population": "小样本任务",
            "intervention": "LoRA",
            "comparison": "全参",
        },
    },
    {
        "raw_question": "Agent 加入记忆能否提升长期任务的一致性?",
        "expect_hints": {
            "population": "Agent",
            "intervention": "记忆",
            "outcome": "一致性",
        },
    },
]


# ------------------------------------------------------------
# m4 Critique 种子提案(给 Reviewer 评审的原始材料)
# 一份合理的提案材料,用于跑一致性测试
# ------------------------------------------------------------
M4_SEED_PROPOSAL = {
    "refined_question": "RAG 能否降低大语言模型在开放域问答中的幻觉率?",
    "method_summary": (
        "在 NaturalQuestions 和 TriviaQA 两个数据集上,比较三种设置:\n"
        "1. 无检索基线(closed-book)\n"
        "2. 稠密检索(DPR) + LLaMA-3-8B\n"
        "3. 混合检索(BM25 + DPR 融合) + LLaMA-3-8B\n"
        "主要指标:FActScore(事实性),次要指标:EM / F1。"
    ),
    "experiment_brief": (
        "训练集:NQ train。测试集:NQ test + TriviaQA test。\n"
        "每个配置 3 个随机种子,报告均值 ± 标准差。\n"
        "显著性:paired bootstrap(n=1000)。"
    ),
    "top_papers": (
        "1. Lewis et al., RAG (2020) — 检索增强生成框架\n"
        "2. Min et al., FActScore (2023) — 事实性评估指标\n"
        "3. Karpukhin et al., DPR (2020) — 稠密检索"
    ),
}


# ------------------------------------------------------------
# 阈值(可在 eval 里引用,集中管理方便调优)
# ------------------------------------------------------------
THRESHOLDS = {
    # m1 PICO:Judge 给出的 overall_score(1-5),均值应 ≥
    "m1_quality_min_avg": 3.5,
    # m4 一致性:同输入跑 N 次,Meta.final_rating 的标准差应 ≤
    "m4_meta_rating_stdev_max": 2.0,
    # m4 一致性:所有 Reviewer 的 rating 方差应 ≤(不算 devil/meta)
    "m4_reviewer_variance_max": 5.0,
    # 一致性测试的重复次数:越多越准但越贵,3 是最小有效值
    "m4_consistency_runs": 3,
}
