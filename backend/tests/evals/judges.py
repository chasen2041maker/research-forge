"""
============================================================
 LLM-as-Judge 评分器(tests/evals/judges.py)
============================================================

🎓 教学目标
    2024-2026 Agent eval 的主流做法:**让 LLM 当裁判给产物打分**。
    比纯正则/规则评估更灵活,比人工打分便宜。

📌 本文件提供两个 Judge

    1. rubric_judge:按 rubric(评分标准)给产物打 1-5 分 + rationale
       - 用于 m1 PICO 质量、m7 论文质量等"主观质量"评估

    2. structural_judge:检查产物是否满足硬性结构约束
       - 用于"refined_question 非空、population 不是 None 这种确定性检查"
       - 不调 LLM,走纯逻辑

💡 为什么 LLM-as-Judge 不是万能的
    - 同模型自审偏高分(self-enhancement bias)→ 裁判换不同模型家族
    - 对长文本有位置偏好(position bias)→ 评 pairwise 时要两向都评
    - Rubric 写得含糊 → 打分噪音大 → 所以 rubric 要写"明确的 1 分到 5 分各是什么样"

💡 用 critical 档(Claude Opus)当裁判的理由
    - Reviewer 用 DeepSeek(reasoner),裁判用 Claude → 跨模型家族减少 self-enhancement
    - 裁判更重要,愿意多花一点
    - 和 m4 Meta-Reviewer 同档位,口径一致
"""

from __future__ import annotations

from typing import Any

from co_scientist.llm import get_llm


# ------------------------------------------------------------
# Judge 1:rubric 式主观打分
# ------------------------------------------------------------

JUDGE_SYSTEM = """\
你是 Agent 产物评估员。根据给定的 rubric 对产物打 1-5 分,严格返回 JSON:
{"overall_score": 1-5, "rationale": "..."}

评分语义:
- 5 = 卓越,无明显缺陷,可直接交付
- 4 = 良好,有小瑕疵但不影响使用
- 3 = 及格,有明显问题但基本可用
- 2 = 不及格,问题较多需重做
- 1 = 不可用,完全偏离预期

只回 JSON,不要任何多余文字。"""


def rubric_judge(
    product: str,
    rubric: str,
    *,
    purpose: str = "judge_rubric",
    judge_role: str = "critical",
) -> dict[str, Any]:
    """
    用 LLM 按 rubric 给产物打分。

    Args:
        product: 被评估的产物文本
        rubric: 评分标准(写明 5/4/3/2/1 分各对应什么样)
        purpose: 成本跟踪标签
        judge_role: 评分用哪档模型(默认 critical = Claude Opus,减少自审偏差)

    Returns:
        {"overall_score": int 1-5, "rationale": str}

    ▍为什么 temperature 固定 0
        评分要复现性,不要创造性。temperature=0 让同一输入多次打分基本一致,
        是 evaluator 的黄金配置。
    """
    llm = get_llm(judge_role)
    result = llm.chat_json(
        messages=[
            {"role": "system", "content": JUDGE_SYSTEM},
            {
                "role": "user",
                "content": f"# Rubric\n{rubric}\n\n# 产物\n{product}",
            },
        ],
        purpose=purpose,
        temperature=0.0,
        max_tokens=512,
    )
    # 归一化:overall_score 可能是 int / float / str,统一成 int
    raw_score = result.get("overall_score", 0)
    try:
        score = int(float(raw_score))
    except (TypeError, ValueError):
        score = 0
    return {
        "overall_score": max(1, min(5, score)) if score else 0,
        "rationale": str(result.get("rationale", "")),
    }


# ------------------------------------------------------------
# Judge 2:结构校验(纯逻辑,不调 LLM)
# ------------------------------------------------------------


def check_pico_schema(pico: dict[str, Any]) -> list[str]:
    """
    硬性校验 PICO 结构完整性,返回违规列表(空列表 = 全通过)。

    比 LLM Judge 快 100 倍,用于"格式一定不能错"的底线检查。
    真实项目里 schema 校验常用 pydantic / jsonschema,这里直接手写便于读者理解思路。
    """
    violations: list[str] = []
    required_non_empty = ["population", "intervention", "outcome", "refined_question"]
    for field in required_non_empty:
        if not pico.get(field) or not str(pico[field]).strip():
            violations.append(f"字段 `{field}` 为空或缺失")

    # refined_question 不能只是把原题复读
    q = str(pico.get("refined_question", ""))
    if len(q) < 10:
        violations.append(f"refined_question 过短(len={len(q)}),可能未被真正精炼")

    return violations


def check_critique_card_schema(card: dict[str, Any]) -> list[str]:
    """检查 m4 的 CritiqueCard 是否符合结构约束。"""
    violations: list[str] = []

    # 数值字段的合法范围
    ranges = {
        "rating": (1, 10),
        "confidence": (1, 5),
        "soundness": (1, 5),
        "contribution": (1, 5),
        "presentation": (1, 5),
    }
    for field, (lo, hi) in ranges.items():
        v = card.get(field)
        # 允许 0(失败兜底)但要标记
        if v == 0:
            continue
        if not isinstance(v, (int, float)) or not (lo <= v <= hi):
            violations.append(f"字段 `{field}` 越界:{v} (期望 {lo}-{hi})")

    # list 字段应为 list
    for field in ["strengths", "weaknesses", "questions", "limitations"]:
        if field in card and not isinstance(card[field], list):
            violations.append(f"字段 `{field}` 类型错误:期望 list")

    return violations


# ------------------------------------------------------------
# 常用 rubric 模板
# ------------------------------------------------------------

RUBRIC_M1_PICO = """\
评估标准(1-5 分):
- 5 分:四个要素(P/I/C/O)都具体明确,refined_question 清晰可检索
- 4 分:四个要素基本到位,refined_question 略冗长但方向对
- 3 分:有一个要素含糊或遗漏,但整体可用
- 2 分:两个以上要素含糊/冗余/跑题
- 1 分:完全偏离研究问题或结构混乱"""


RUBRIC_M4_META = """\
评估 Meta-Reviewer 的终裁质量(1-5 分):
- 5 分:决定明确(accept/reject/revision),理由与各 Reviewer 卡内容一致,覆盖关键争议
- 4 分:决定明确,理由合理但漏掉一两个关键点
- 3 分:决定含糊或理由与卡内容有轻微冲突
- 2 分:决定明确但理由与卡内容严重不一致
- 1 分:决定荒谬或与证据完全矛盾"""
