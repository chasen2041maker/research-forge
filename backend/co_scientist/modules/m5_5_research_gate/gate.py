"""
============================================================
 模块 5.5:ResearchGate 质量门禁(m5_5_research_gate/gate.py)
============================================================

🎓 教学目标
    整理版 §8.2 提出的 M5.5 是实验方案后的质量门禁,负责判断:
      - 实验是否可执行
      - 证据是否足够
      - baseline 是否明确
      - 数据集是否可获得
      - 是否需要回退

📌 设计取舍(MVP 版)
    1. 优先用启发式规则做硬性判断(experiment_plan 缺数据集/baseline/指标 → revise_experiment;
       access_status 整体 low → fetch_more_evidence)。
    2. 上层若提供 LLM(USE_M5_5_LLM=True),再让 LLM 综合 DecisionCard 生成更细的理由;
       否则纯规则就够。
    3. Phase C 阶段不实际做"图回边",只把决策写到 state.metadata.research_gate,
       让 M8(Phase D)消费,或者前端展示让用户决策。

🔧 与图的衔接
    graph.py 在 m5_experiment 之后、m6_generate 之前插入 m5_5_gate 节点。
    输出 state.metadata.research_gate = {gate_decision, rationale, blocking_issues, required_fixes}。

🔗 LLM 与启发式协作模型(关键设计)
    decide_gate 函数有两条执行路径:
      _heuristic_gate(默认)─── 纯函数规则版,零 LLM 成本
      LLM 综合优化层(可选)── USE_M5_5_LLM=True 时叠加,用 prompts/SYSTEM_M5_5_GATE

    协作原则:
      1) 启发式优先:三类规则(完整性 / 低证据 / DecisionCard 服从)在启发式
         里硬编码;LLM 不能推翻这些硬性失败信号
      2) LLM 只补充细颗粒 rationale:启发式给"continue_to_m6",LLM 能加上
         具体理由"实验方案完整 + 80% 证据 high level"
      3) LLM 输出非法动作 → 沿用启发式:gate_decision 不在 GATE_ACTIONS 集合时
         decide_gate 直接 return base(启发式结果),保证主流程不被 LLM 幻觉拖崩
      4) LLM 调用失败 → 沿用启发式:任何 except 都让 base 兜底

    这种"启发式先,LLM 后"的协作避免了"LLM 是单点故障"的常见 Agent 项目问题:
      纯 LLM 决策 = LLM 挂 / 输出乱 → 整个 gate 不可用
      启发式 + LLM = 启发式总能跑;LLM 是优化层,挂了不阻塞

🔗 下游消费契约
    Phase D M8 multi_branch.runner 不读 metadata.research_gate;它读 final_state
    后由调用方决定要不要 ForkManager.branch_from_gate_decision 派生新分支。
    前端 ResearchGateView 直接渲染 gate_decision pill + rationale。

------------------------------------------------------------
"""

from __future__ import annotations

import json
from typing import Any

from co_scientist.config import settings
from co_scientist.llm import get_llm
from co_scientist.prompts.templates import (
    SYSTEM_M5_5_GATE,
    USER_M5_5_GATE,
)
from co_scientist.state import (
    DecisionCard,
    EvidenceAccessStatus,
    Experiment,
    GapCard,
    ResearchState,
)
from co_scientist.utils import logger


# 与整理版 §8.2 / SYSTEM_M5_5_GATE 中的动作枚举保持一致
GATE_ACTIONS = {
    "continue_to_m6",
    "revise_experiment",
    "fetch_more_evidence",
    "refine_question",
    "choose_new_topic",
    "stop",
}


def _heuristic_gate(
    experiment_plan: Experiment,
    decision_card: DecisionCard | dict,
    access_statuses: list[EvidenceAccessStatus],
) -> dict:
    """
    纯规则的快速门禁。无 LLM 调用,失败兜底也由它担。

    返回:{"gate_decision": str, "rationale": str, "blocking_issues": [...], "required_fixes": [...]}
    """
    issues: list[str] = []
    fixes: list[str] = []

    # 1) 实验方案完整性
    if not experiment_plan.get("datasets"):
        issues.append("实验方案缺数据集")
        fixes.append("M5 重新设计:补充数据集字段")
    if not experiment_plan.get("baselines") or len(experiment_plan.get("baselines", [])) < 1:
        issues.append("实验方案缺 baseline")
        fixes.append("M5 重新设计:至少提供 1 个 baseline")
    if not experiment_plan.get("metrics"):
        issues.append("实验方案缺指标")
        fixes.append("M5 重新设计:补充评测指标")
    missing = experiment_plan.get("_missing", []) or []  # type: ignore[index]
    if missing:
        for m in missing:
            issues.append(f"M5 self_check 缺项:{m}")

    if issues:
        return {
            "gate_decision": "revise_experiment",
            "rationale": "实验方案不完整,M5 自检或字段检查失败",
            "blocking_issues": issues,
            "required_fixes": fixes,
        }

    # 2) 证据等级
    if access_statuses:
        low = sum(1 for s in access_statuses if s.get("evidence_level") == "low")
        ratio = low / len(access_statuses)
        if ratio > 0.5:
            return {
                "gate_decision": "fetch_more_evidence",
                "rationale": f"超过半数证据为 low (ratio={ratio:.0%}),需补检索/全文",
                "blocking_issues": [f"low evidence ratio={ratio:.0%}"],
                "required_fixes": ["回 M2/M2.5 补强证据,优先 fulltext + has_code"],
            }

    # 3) 服从 DecisionCard 的指向(若已给定)
    rec = (decision_card or {}).get("recommended_action") if decision_card else None
    rec = (rec or "").strip()
    target = (decision_card or {}).get("target_node", "") if decision_card else ""
    rating = float((decision_card or {}).get("final_rating", 0.0) or 0.0)
    if rec in {"refine_question", "rebuild_gap", "choose_new_topic", "stop"}:
        # 把 DecisionCard 的指向翻译成 M5.5 的动作
        mapped = {
            "refine_question": "refine_question",
            "rebuild_gap": "fetch_more_evidence",
            "choose_new_topic": "choose_new_topic",
            "stop": "stop",
        }[rec]
        return {
            "gate_decision": mapped,
            "rationale": f"服从 DecisionCard.recommended_action={rec} (target={target})",
            "blocking_issues": list((decision_card or {}).get("blocking_issues", []) or []),
            "required_fixes": list((decision_card or {}).get("required_fixes", []) or []),
        }
    if rec == "fetch_more_evidence":
        return {
            "gate_decision": "fetch_more_evidence",
            "rationale": f"DecisionCard 要求补证据(rating={rating:.1f})",
            "blocking_issues": list((decision_card or {}).get("blocking_issues", []) or []),
            "required_fixes": list((decision_card or {}).get("required_fixes", []) or []),
        }
    if rec == "revise_experiment":
        return {
            "gate_decision": "revise_experiment",
            "rationale": f"DecisionCard 要求修订实验(rating={rating:.1f})",
            "blocking_issues": list((decision_card or {}).get("blocking_issues", []) or []),
            "required_fixes": list((decision_card or {}).get("required_fixes", []) or []),
        }

    # 4) 默认放行
    return {
        "gate_decision": "continue_to_m6",
        "rationale": "实验方案完整,证据可用,Decision 未阻塞 → 放行",
        "blocking_issues": [],
        "required_fixes": [],
    }


def decide_gate(
    experiment_plan: Experiment,
    decision_card: DecisionCard | dict | None,
    access_statuses: list[EvidenceAccessStatus] | None = None,
    *,
    use_llm: bool = False,
    gap_card: GapCard | None = None,
) -> dict:
    """
    判断是否放行进入 M6。

    use_llm=True 时叠加一次 LLM 综合判断;否则只跑启发式。
    返回 dict 而不是 dataclass:M5.5 输出直接挂在 state.metadata.research_gate,
    供前端 / M8 / 用户消费。
    """
    base = _heuristic_gate(experiment_plan, decision_card or {}, access_statuses or [])
    if not use_llm:
        return base

    # LLM 兜底/优化:让 LLM 给更细的 rationale
    try:
        llm = get_llm("reasoner")
        result = llm.chat_json(
            messages=[
                {"role": "system", "content": SYSTEM_M5_5_GATE},
                {
                    "role": "user",
                    "content": USER_M5_5_GATE.format(
                        experiment_plan=json.dumps(dict(experiment_plan), ensure_ascii=False, indent=2),
                        decision_card=json.dumps(dict(decision_card or {}), ensure_ascii=False, indent=2),
                        gap_card_summary=(
                            f"title: {gap_card.get('title', '')}, "
                            f"missing_piece: {gap_card.get('missing_piece', '')}"
                            if gap_card else "(无)"
                        ),
                        access_summary=_access_summary(access_statuses or []),
                    ),
                },
            ],
            purpose="m5_5_gate",
            temperature=0.2,
            max_tokens=1024,
        )
        action = str(result.get("gate_decision", "")).strip()
        if action in GATE_ACTIONS:
            return {
                "gate_decision": action,
                "rationale": str(result.get("rationale", "")) or base["rationale"],
                "blocking_issues": list(result.get("blocking_issues") or base["blocking_issues"]),
                "required_fixes": list(result.get("required_fixes") or base["required_fixes"]),
            }
        logger.warning("[M5.5] LLM 输出 gate_decision={!r} 不在合法集,沿用启发式", action)
    except Exception as e:
        logger.warning("[M5.5] LLM 综合失败,沿用启发式: {}", e)

    return base


def _access_summary(statuses: list[EvidenceAccessStatus]) -> str:
    if not statuses:
        return "(无 access_status)"
    levels: dict[str, int] = {}
    for s in statuses:
        levels[s.get("evidence_level", "?")] = levels.get(s.get("evidence_level", "?"), 0) + 1
    return f"total={len(statuses)}, by_level={levels}"


# ------------------------------------------------------------
# LangGraph 节点
# ------------------------------------------------------------


def research_gate_node(state: ResearchState) -> dict:
    """
    LangGraph 节点。在 m5_experiment 之后跑,m6_generate 之前。
    Phase C 不做实际回边,只把决策写到 state.metadata.research_gate。
    Phase D 由 M8 消费这个字段决定 fork 内回退或开新分支。
    """
    exp = state.get("experiment_plan", {}) or {}
    if not exp:
        # M5 未跑成功,直接报告为 revise_experiment
        gate = {
            "gate_decision": "revise_experiment",
            "rationale": "M5 未产出 experiment_plan",
            "blocking_issues": ["缺 experiment_plan"],
            "required_fixes": ["回 M5 重新设计"],
        }
    else:
        # current GapCard
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

        gate = decide_gate(
            exp,
            state.get("decision_card") or {},
            state.get("evidence_access_status") or [],
            use_llm=settings.USE_M5_5_LLM,
            gap_card=chosen_gap,
        )

    logger.info(
        "[M5.5] gate={} (issues={})",
        gate.get("gate_decision"),
        len(gate.get("blocking_issues", [])),
    )

    # 写到 metadata.research_gate(metadata 是 dict 字段,merge 由 LangGraph 不做特殊处理,
    # 这里整个覆盖即可)
    meta_patch = {"research_gate": gate}
    return {"metadata": meta_patch}
