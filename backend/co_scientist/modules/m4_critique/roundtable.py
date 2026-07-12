"""
============================================================
 模块 4:批判圆桌编排(m4_critique/roundtable.py)
============================================================

🎓 教学目标
    最核心的 Agent 协作逻辑。学会:
      - 多 Agent 并行评审
      - 分数方差检测 + 二次辩论机制
      - Meta-Reviewer 终裁(用 Claude Opus)
      - **Orchestrator-Subagent 范式**(2025.4 Anthropic 提出)

    这是整个项目最能体现"Agent 工程思想"的模块。
    面试时能把本文件讲清楚,就能展示对多 Agent 系统的深刻理解。

📌 流程(接入 Orchestrator 后)
    0. Orchestrator 看问题,动态选 3-5 个 Reviewer(可由 settings 关闭)
    1. 被选中的 Reviewer 并行评审 → 得到卡片
    2. 计算 rating 方差
       - 方差 ≤ 2:共识 → 直接进 meta
       - 方差 > 2:分歧 → 触发 Devil's Advocate 二次辩论
    3. Meta-Reviewer(Claude Opus)读所有卡 → 给最终决定

📌 对比老版本:动态 Reviewer 的两个价值
    1. 成本:纯理论问题不调用 Reproducibility Reviewer,一次跑省 ~20% token
    2. 信号:不相关 Reviewer 的"弱相关评审"只会稀释 Meta 的决策,砍掉它们
       让方差计算更有意义、Meta 面对的输入更集中

------------------------------------------------------------
"""

from __future__ import annotations

import asyncio
import statistics
from typing import Any

from co_scientist.config import settings
from co_scientist.llm import get_llm
from co_scientist.modules.m4_critique.orchestrator import (
    resolve_personas,
    select_reviewers,
)
from co_scientist.modules.m4_critique.reviewers import (
    ALL_REVIEWERS,
    DEVIL_REVIEWER,
    META_REVIEWER,
    ReviewerPersona,
    review_proposal,
)
from co_scientist.prompts.templates import (
    SYSTEM_M4_DECISION_CARD,
    USER_M4_DECISION_CARD,
    USER_M4_META_DECIDE,
)
from co_scientist.state import CritiqueCard, DecisionCard, EvidenceAccessStatus, GapCard, ResearchState
from co_scientist.utils import logger


async def run_reviewers_parallel(
    refined_question: str,
    method_summary: str,
    experiment_brief: str = "",
    top_papers: str = "",
    personas: list[ReviewerPersona] | None = None,
) -> list[CritiqueCard]:
    """
    并行运行一组 Reviewer。

    Args:
        personas: 本次要跑的 Reviewer 列表。None 表示跑全员(向后兼容)。

    ▍为什么把 personas 做成 Optional 参数
        - 想"无脑并行全员"的旧调用方(老测试 / 用户关闭 Orchestrator 时)
          传 None 即可,保持老行为
        - 想"动态选一组"的新调用方传清洗过的 persona 列表进来
        接口加一个参数、默认值向后兼容,**比造两个函数更优雅**。

    ▍为什么不让这个函数自己调 Orchestrator
        职责分工:
          - 本函数负责"把一组 Reviewer 并行跑起来"
          - Orchestrator 负责"决定跑哪组"
        两件事解耦后,上层 run_roundtable_async 做编排(先选 → 再跑 → 再裁决),
        每个函数单一职责,单元测试也好写。
    """
    chosen = personas if personas is not None else ALL_REVIEWERS

    tasks = [
        asyncio.to_thread(
            review_proposal,
            persona,
            refined_question,
            method_summary,
            experiment_brief,
            top_papers,
        )
        for persona in chosen
    ]
    cards = await asyncio.gather(*tasks)
    return list(cards)


def compute_variance(cards: list[CritiqueCard]) -> float:
    """计算 rating 的方差(排除失败的 0 分卡)。"""
    ratings = [c["rating"] for c in cards if c.get("rating", 0) > 0]
    if len(ratings) < 2:
        return 0.0
    return statistics.pvariance(ratings)


def meta_decide(cards: list[CritiqueCard]) -> dict:
    """Meta-Reviewer 终裁(Claude Opus 4.7)。"""
    llm = get_llm(META_REVIEWER.model_role)  # critical = claude-opus-4-7

    # 格式化所有卡片
    import json

    cards_str = json.dumps(cards, ensure_ascii=False, indent=2)

    try:
        result = llm.chat_json(
            messages=[
                {"role": "system", "content": META_REVIEWER.system_prompt},
                {
                    "role": "user",
                    "content": USER_M4_META_DECIDE.format(cards_json=cards_str),
                },
            ],
            purpose="m4_meta_decision",
            temperature=0.4,
            max_tokens=2048,
        )
    except Exception as e:
        logger.error("[M4-meta] 终裁失败: {}", e)
        # 降级:用常规 reasoner 兜底
        logger.warning("[M4-meta] 降级为 reasoner 兜底")
        llm_fallback = get_llm("reasoner")
        result = llm_fallback.chat_json(
            messages=[
                {"role": "system", "content": META_REVIEWER.system_prompt},
                {
                    "role": "user",
                    "content": USER_M4_META_DECIDE.format(cards_json=cards_str),
                },
            ],
            purpose="m4_meta_decision_fallback",
            temperature=0.4,
        )

    logger.info("[M4-meta] 决定: {} (rating={})",
                result.get("decision"), result.get("final_rating"))
    return result


async def run_roundtable_async(
    refined_question: str,
    method_summary: str,
    experiment_brief: str = "",
    top_papers: str = "",
    variance_threshold: float = 2.0,
) -> tuple[list[CritiqueCard], dict]:
    """
    完整的批判圆桌流程(Orchestrator-Subagent 范式)。

    Returns:
        (所有评审卡片, Meta 决定)

    ▍编排流程
        Step 0: Orchestrator 选 Reviewer(可通过 settings.M4_USE_ORCHESTRATOR 关闭)
        Step 1: 并行评审
        Step 2: 方差 > 阈值 时,让 devil 看 Round 1 摘要再评一次
        Step 3: Meta 终裁

    ▍为什么把 Orchestrator 决定结果也放进 meta_decision 里返回
        下游(graph.py 的 appendix_reflect)想看"这次 m4 用了哪几位 Reviewer"
        来做反思,必须要能拿到 orchestrator 的决定。把它放进 meta_decision 的
        meta 字段是"最经济的信息透传方式",不必另开一个返回字段。
    """
    # ---- Step 0:Orchestrator 决定本次跑哪几位 Reviewer ----
    # 为什么 Orchestrator 调用放在 roundtable 顶部而不是 critique_node:
    #   - roundtable 才是"批判圆桌"的完整业务边界,critique_node 只是 LangGraph 适配
    #   - 未来如果想在 CLI / 单元测试里直接跑 roundtable,Orchestrator 逻辑跟着走
    orch_info: dict[str, Any]
    if settings.M4_USE_ORCHESTRATOR:
        orch_info = select_reviewers(refined_question, method_summary)
        selected_personas = resolve_personas(orch_info["reviewers"])
    else:
        # 开关关闭 → 保持老行为(全员评审)
        orch_info = {
            "reviewers": [r.name for r in ALL_REVIEWERS],
            "reason": "settings.M4_USE_ORCHESTRATOR=False,跳过动态选择",
            "fallback": False,
        }
        selected_personas = list(ALL_REVIEWERS)

    # ---- Round 1:并行评审(只跑被选中的 Reviewer)----
    logger.info(
        "[M4] 🎭 启动批判圆桌(Round 1,{} 位 Reviewer: {})",
        len(selected_personas),
        [p.name for p in selected_personas],
    )
    cards = await run_reviewers_parallel(
        refined_question,
        method_summary,
        experiment_brief,
        top_papers,
        personas=selected_personas,
    )

    # ---- 检查方差 ----
    var = compute_variance(cards)
    logger.info("[M4] Round 1 方差 = {:.2f} (阈值 {})", var, variance_threshold)

    # ---- Round 2:方差高 → 二次辩论 ----
    if var > variance_threshold:
        logger.info("[M4] 🔥 方差超阈值,触发 Devil's Advocate 二次辩论")
        # 让 Devil 看到 Round 1 的结果,再评一次
        round1_summary = "\n".join(
            f"- {c['reviewer']}: rating={c['rating']}, 主要批评={'; '.join(c['weaknesses'][:2])}"
            for c in cards
        )
        devil_r2 = await asyncio.to_thread(
            review_proposal,
            DEVIL_REVIEWER,
            refined_question,
            method_summary + f"\n\n# Round 1 评审摘要\n{round1_summary}",
            experiment_brief,
            top_papers,
        )
        devil_r2["reviewer"] = "devil_round2"
        cards.append(devil_r2)

    # ---- Meta 终裁 ----
    meta_decision = meta_decide(cards)

    # 把 Orchestrator 的选择信息附着到 meta_decision 里,下游反思节点和
    # 前端可以展示"这次召了哪几位 Reviewer + 为什么"
    meta_decision["orchestrator"] = orch_info

    return cards, meta_decision


# ------------------------------------------------------------
# 整理版 Phase C:DecisionCard 输出
# ------------------------------------------------------------


def _summarize_cards(cards: list[CritiqueCard], max_items: int = 6) -> str:
    """把 Reviewer 卡片压成 LLM 友好的简短摘要。"""
    import json
    out = []
    for c in cards[:max_items]:
        out.append({
            "reviewer": c.get("reviewer", ""),
            "rating": c.get("rating", 0),
            "soundness": c.get("soundness", 0),
            "weaknesses": c.get("weaknesses", [])[:3],
            "limitations": c.get("limitations", [])[:2],
        })
    return json.dumps(out, ensure_ascii=False, indent=2)


def _summarize_gap_card(gap: GapCard | None) -> str:
    if not gap:
        return "(无 GapCard)"
    return (
        f"title: {gap.get('title', '')}\n"
        f"missing_piece: {gap.get('missing_piece', '')}\n"
        f"datasets: {gap.get('datasets', [])}\n"
        f"baselines: {gap.get('baselines', [])}\n"
        f"evidence_level: {gap.get('evidence_level', 'medium')}\n"
        f"novelty: {gap.get('novelty_score', 0)}, feasibility: {gap.get('feasibility_score', 0)}"
    )


def _summarize_access(statuses: list[EvidenceAccessStatus]) -> str:
    if not statuses:
        return "(无 access_status,M2.5 未启用或上游无 papers)"
    levels: dict[str, int] = {}
    has_code = 0
    has_dataset = 0
    for s in statuses:
        levels[s.get("evidence_level", "?")] = levels.get(s.get("evidence_level", "?"), 0) + 1
        if s.get("has_code"):
            has_code += 1
        if s.get("has_dataset"):
            has_dataset += 1
    total = len(statuses)
    return (
        f"total={total}, by_level={levels}, "
        f"with_code={has_code}, with_dataset={has_dataset}"
    )


def build_decision_card(
    meta_decision: dict,
    cards: list[CritiqueCard],
    gap: GapCard | None,
    access_statuses: list[EvidenceAccessStatus],
) -> DecisionCard:
    """
    在 Meta 终裁基础上,综合 GapCard / 文献访问状态分布,构造结构化 DecisionCard。

    ▍为什么不直接把 meta_decision 当 DecisionCard 用
        meta_decision 是 Reviewer 卡片之间的"评议结论"(有 final_rating / decision /
        rationale 等),不带流程动作语义。整理版 §7.4 要求 M4 输出能驱动 M5/M5.5/M8 的
        action / target_node / branch_count。我们追加一次 LLM 调用,
        让它专门负责"综合各路信号,产出可执行决策"。

    ▍失败为什么有兜底 DecisionCard
        DecisionCard 是新接口,M5.5 / M8 / 前端等多个下游强依赖。
        失败时不能让 critique_node 整体崩,要给一张"保守的 minor_revision"卡兜底。

    🔗 下游消费契约(为什么失败兜底必须给 m5 target_node)
        M5.5.decide_gate(_heuristic_gate)
            → 服从 recommended_action / target_node;失败兜底取 "revise_experiment"
              + "m5",让 M5.5 走 revise_experiment 分支(回 M5 重设实验)
              这是最保守的"不破坏数据 + 不强行通过"动作
        M8.multi_branch.merge_winner / score_branches_with_llm
            → 用 final_rating 排序;失败兜底从 meta_decision.final_rating 取,
              如果连 meta_decision 都没拿到就 fallback 5.0(中位数)
        前端 DecisionCardView
            → 直接渲染所有字段;兜底卡的 reason 字段会写"LLM 生成 DecisionCard 失败"
              让用户一眼看出是兜底而不是真决策
    """
    import json
    llm = get_llm("reasoner")
    try:
        result = llm.chat_json(
            messages=[
                {"role": "system", "content": SYSTEM_M4_DECISION_CARD},
                {
                    "role": "user",
                    "content": USER_M4_DECISION_CARD.format(
                        meta_decision=json.dumps(meta_decision, ensure_ascii=False, indent=2),
                        cards_summary=_summarize_cards(cards),
                        gap_card_summary=_summarize_gap_card(gap),
                        access_summary=_summarize_access(access_statuses),
                    ),
                },
            ],
            purpose="m4_decision_card",
            temperature=0.3,
            max_tokens=1024,
        )
    except Exception as e:
        logger.warning("[M4] DecisionCard 生成失败,使用保守兜底: {}", e)
        rating_fallback = float(meta_decision.get("final_rating", 5.0) or 5.0)
        return DecisionCard(
            passed=False,
            decision="minor_revision",
            final_rating=rating_fallback,
            recommended_action="revise_experiment",
            target_node="m5",
            branch_count=1,
            branch_variants=[],
            blocking_issues=["DecisionCard 构造失败,默认要求复审"],
            required_fixes=["人工 review meta_decision 后重试"],
            reason=f"LLM 生成 DecisionCard 失败: {type(e).__name__}",
        )

    return DecisionCard(
        passed=bool(result.get("passed", False)),
        decision=str(result.get("decision", "minor_revision")),
        final_rating=float(result.get("final_rating", 0.0) or 0.0),
        recommended_action=str(result.get("recommended_action", "continue")),
        target_node=str(result.get("target_node", "m5")),
        branch_count=int(result.get("branch_count", 1) or 1),
        branch_variants=[str(x) for x in (result.get("branch_variants") or [])],
        blocking_issues=[str(x) for x in (result.get("blocking_issues") or [])],
        required_fixes=[str(x) for x in (result.get("required_fixes") or [])],
        reason=str(result.get("reason", "")),
    )


# ------------------------------------------------------------
# LangGraph 节点
# ------------------------------------------------------------


def critique_node(state: ResearchState) -> ResearchState:
    """LangGraph 节点。"""
    pico = state.get("pico", {})
    refined_q = pico.get("refined_question", state.get("raw_question", ""))
    if not refined_q:
        return {"error_log": ["[M4] 缺少研究问题"]}

    # 方法摘要:从 PICO 组装一个简短描述
    method_summary = (
        f"Population: {pico.get('population', 'N/A')}\n"
        f"Intervention: {pico.get('intervention', 'N/A')}\n"
        f"Comparison: {pico.get('comparison', 'N/A')}\n"
        f"Outcome: {pico.get('outcome', 'N/A')}"
    )

    # 实验方案(如果模块 5 先跑过)
    exp = state.get("experiment_plan", {})
    experiment_brief = ""
    if exp:
        experiment_brief = (
            f"数据集: {[d.get('name') for d in exp.get('datasets', [])]}\n"
            f"基线: {exp.get('baselines', [])}\n"
            f"指标: {exp.get('metrics', [])}"
        )

    # Top 论文作为背景
    papers = state.get("papers", [])[:5]
    top_papers_str = "\n".join(
        f"- [{p.get('year', '?')}] {p.get('title', '')} ({p.get('venue', '')})"
        for p in papers
    )

    cards, decision = asyncio.run(
        run_roundtable_async(
            refined_question=refined_q,
            method_summary=method_summary,
            experiment_brief=experiment_brief,
            top_papers=top_papers_str,
        )
    )

    # ---- 整理版 Phase C:基于 GapCard / Access Status 综合输出 DecisionCard ----
    gap_cards = state.get("gap_cards", []) or []
    current_gap_id = state.get("current_gap_id", "")
    chosen_gap: GapCard | None = None
    if gap_cards:
        if current_gap_id:
            for gc in gap_cards:
                if gc.get("gap_id") == current_gap_id:
                    chosen_gap = gc
                    break
        if chosen_gap is None:
            chosen_gap = gap_cards[0]

    access_statuses = state.get("evidence_access_status", []) or []
    decision_card = build_decision_card(decision, cards, chosen_gap, access_statuses)
    logger.info(
        "[M4] DecisionCard: action={} → target={} (rating={:.1f}, branch_count={})",
        decision_card.get("recommended_action"),
        decision_card.get("target_node"),
        decision_card.get("final_rating", 0.0),
        decision_card.get("branch_count", 1),
    )

    return {
        "critiques": cards,
        "meta_decision": decision,        # legacy
        "decision_card": decision_card,   # 整理版 Phase C 新输出
    }
