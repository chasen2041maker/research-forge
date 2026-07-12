"""
============================================================
 模块 1:研究问题精炼(m1_refiner/refiner.py)
============================================================

🎓 教学目标
    第一个真正的"Agent"!学会:
      - 如何把一个简单业务封装成 LangGraph 节点
      - 如何用 LLM 做"判断 + 反问"循环
      - 怎样用 PICO 框架结构化研究问题

📌 工作流程
    1. 用 reasoner 模型判断问题是否足够具体
    2. 不具体 → 使用前端/API 提供的澄清信息;没有提供时记录待澄清问题但不阻塞
    3. 收齐信息 → 输出 PICO

🔧 与 LangGraph 的衔接
    本模块导出一个 `refine_question_node(state)` 函数,
    LangGraph 会把它当作图节点调用,自动传入并合并 State。

------------------------------------------------------------
"""

from __future__ import annotations

from co_scientist.llm import get_llm
from co_scientist.prompts.templates import (
    SYSTEM_M1_REFINER,
    USER_M1_BUILD_PICO,
    USER_M1_CHECK_SPECIFICITY,
)
from co_scientist.state import PICO, ResearchState
from co_scientist.utils import logger


# ------------------------------------------------------------
# 业务逻辑:三段式
# ------------------------------------------------------------


def check_specificity(question: str) -> tuple[bool, str]:
    """判断问题是否足够具体。返回 (是否具体, 下一个澄清问题或原因)。"""
    llm = get_llm("reasoner")  # 判断类任务用推理强的模型
    result = llm.chat_json(
        messages=[
            {"role": "system", "content": SYSTEM_M1_REFINER},
            {
                "role": "user",
                "content": USER_M1_CHECK_SPECIFICITY.format(question=question),
            },
        ],
        purpose="m1_check_specificity",
        temperature=0.2,  # 判断类任务温度低
    )
    return bool(result.get("specific", False)), result.get(
        "next_question", result.get("reason", "")
    )


def build_pico(raw_question: str, clarifications: list[dict[str, str]]) -> PICO:
    """根据澄清后的信息构建 PICO。"""
    llm = get_llm("reasoner")
    result = llm.chat_json(
        messages=[
            {"role": "system", "content": SYSTEM_M1_REFINER},
            {
                "role": "user",
                "content": USER_M1_BUILD_PICO.format(
                    raw_question=raw_question,
                    clarifications=clarifications,
                ),
            },
        ],
        purpose="m1_build_pico",
        temperature=0.3,
    )
    return PICO(
        population=result.get("population", ""),
        intervention=result.get("intervention", ""),
        comparison=result.get("comparison", ""),
        outcome=result.get("outcome", ""),
        refined_question=result.get("refined_question", raw_question),
        clarifications=clarifications,
    )


# ------------------------------------------------------------
# LangGraph 节点函数
# ------------------------------------------------------------


def _normalize_clarifications(value: object) -> list[dict[str, str]]:
    """从 API metadata 中提取 M1 澄清信息,最多使用 3 条。"""
    if not isinstance(value, list):
        return []

    clarifications: list[dict[str, str]] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        answer = str(item.get("a") or item.get("answer") or "").strip()
        if not answer:
            continue
        question = str(item.get("q") or item.get("question") or "前端补充说明").strip()
        clarifications.append({"q": question or "前端补充说明", "a": answer})
        if len(clarifications) >= 3:
            break
    return clarifications


def _question_with_clarifications(raw_question: str, clarifications: list[dict[str, str]]) -> str:
    if not clarifications:
        return raw_question
    return f"{raw_question}\n\n补充信息:\n" + "\n".join(
        f"Q: {c['q']}\nA: {c['a']}" for c in clarifications
    )


def refine_question_node(state: ResearchState) -> ResearchState:
    """
    LangGraph 节点函数。

    输入:state(必须有 raw_question)
    输出:patch state(只返回需要更新的字段,LangGraph 会自动 merge)

    💡 关键点
        - 节点函数返回的字典只需包含**变化的字段**,不需要返回整个 state
        - LangGraph 会按字段的 reducer 自动合并
        - 如果 state 已经有 pico,说明前面跑过了(支持断点续跑),直接跳过
    """
    if state.get("pico", {}).get("refined_question"):
        logger.info("[M1] PICO 已存在,跳过精炼")
        return {}  # 不动 state

    raw_q = state["raw_question"]
    logger.info("[M1] 开始精炼问题: {}", raw_q[:80])

    # ---- 澄清循环:最多 3 轮 ----
    # 所有用户输入都必须来自前端/API metadata,后端节点不能读 stdin。
    meta = state.get("metadata", {}) or {}
    provided_clarifications = _normalize_clarifications(meta.get("m1_clarifications"))
    clarifications: list[dict[str, str]] = []
    current_q = raw_q
    pending_follow_up = ""

    for turn in range(3):
        is_specific, follow_up = check_specificity(current_q)
        if is_specific:
            logger.info("[M1] 第 {} 轮判定问题已具体", turn + 1)
            break

        pending_follow_up = follow_up
        if len(clarifications) >= len(provided_clarifications):
            logger.info("[M1] 需要澄清但前端未提供更多输入,继续自动构建 PICO")
            break

        clarifications.append(provided_clarifications[len(clarifications)])
        # 累加上下文,让下一轮判断时模型看到更完整的信息。
        current_q = _question_with_clarifications(raw_q, clarifications)

    # ---- 构建 PICO ----
    pico = build_pico(raw_q, clarifications)
    logger.info("[M1] ✅ 完成精炼: {}", pico.get("refined_question", "")[:100])

    patch: ResearchState = {"pico": pico}
    if pending_follow_up:
        patch["metadata"] = {
            **meta,
            "m1_pending_clarification": pending_follow_up,
        }
    return patch
