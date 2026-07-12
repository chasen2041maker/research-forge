"""
============================================================
 模块 0:候选课题发现器(m0_topic_discovery/discovery.py)
============================================================

🎓 教学目标
    把"用户给一个粗粒度兴趣方向 → 系统生成 K 个候选研究方向"的能力
    单独包装成一个 LangGraph 节点。这是整理版架构 §4 提出的能力。

📌 设计取舍(MVP 版)
    1. 不依赖 M2 检索结果。M0 在主流程的第一步,此时还没跑 M2/M3,
       因此 MVP 版只用 LLM 直接基于 raw_question 生成候选方向。
    2. 后续可扩展:在 M0 内部做一次轻量 web/arxiv search,把检索到的
       trending papers 作为 seed_evidence 拼进 prompt。
    3. score 由 LLM 自己估算,不做客观打分。一致性靠 prompt 约束。

🔧 与图的衔接
    本模块导出 topic_discovery_node(state),由 graph.py 在
    Direction Intake 路径里调用,把生成的 list[TopicCard] 写到
    state.topic_cards;后续 user_select_topic_node 让用户/M8 选定
    一个 topic_id,把对应 candidate_question 注入 raw_question/PICO 流。

🛡️ 失败兜底为什么返回 [] 而不是抛
    M0 是新模块,且整理版主图设计为"USE_M0_DISCOVERY=True 才挂这个节点",
    它的失败不应阻塞下游。两层兜底:
      1) discover_topics 函数级:LLM 失败 → return [],日志 warning
      2) topic_discovery_node 节点级:cards 为空 → 直接 return {} (空 patch)
                                     user_select_topic_node 检查到 topic_cards=[]
                                     也 return {},主图退化到"raw_question→m1"老路径
    设计哲学:**新增能力的失败永远不应让老能力崩**。如果 M0 挂了,系统应该
    像"USE_M0_DISCOVERY=False"那样跑,而不是抛 RuntimeError。

🔗 下游消费契约
    user_select_topic_node(graph.py)
        → 读 topic_cards 列表 + 用户输入选 1 张 → 写 current_topic_id + 覆盖 raw_question
    ForkManager.branch_from_topic_cards(整理版 Phase D)
        → 拿 topic_cards 列表批量 create_fork 跑多分支
    前端 TopicCardList(page.tsx)
        → 渲染卡片网格,高亮 current_topic_id 选中项

    所以本模块输出的 TopicCard 字段必须保证:
      - title:必填(非空字符串过滤已在 _make_topic_card 做了)
      - candidate_question:必填(空时回落到 title,user_select_topic 才有东西注入)
      - score:用于 Top-K 排序,LLM 自评不严谨时 default=0 不会让排序崩
      - 其他字段(novelty_rationale 等)空字符串/空列表 OK,不影响主流程

------------------------------------------------------------
"""

from __future__ import annotations

import uuid
from typing import Any

from co_scientist.config import settings
from co_scientist.llm import get_llm
from co_scientist.prompts.templates import (
    SYSTEM_M0_TOPIC_DISCOVERY,
    USER_M0_TOPIC_DISCOVERY,
)
from co_scientist.state import ResearchState, TopicCard
from co_scientist.utils import logger


def discover_topics(
    raw_question: str,
    *,
    k: int = 3,
    constraints: str = "",
    seed_evidence: str = "",
) -> list[TopicCard]:
    """
    给定一个粗粒度的研究兴趣,LLM 生成 k 个候选 TopicCard。

    Args:
        raw_question: 用户的研究兴趣或粗粒度方向
        k: 候选数量(整理版默认 3-5)
        constraints: 约束条件(时间/成本/数据可用性等)
        seed_evidence: 种子证据(种子论文标题/keywords),空字符串表示无前置检索

    Returns:
        list[TopicCard],按 score 降序排列;失败时返回空 list 让上层降级。

    ▍为什么用 reasoner 而不是 chat
        选题需要权衡创新性 / 可行性 / 用户意图,信号弱、约束多,
        reasoner 比 chat 在多目标权衡上稳得多。整理版 §3.3 推荐 M0 用 reasoner。

    ▍失败为什么返回 [] 而不是抛
        M0 是新模块,出错时主流程应该能降级到"无 M0 模式"(直接走 M1)。
        节点层会读 [] 判断要不要走 M0 → M1 的旁路。
    """
    if not raw_question.strip():
        logger.warning("[M0] raw_question 为空,跳过候选课题发现")
        return []

    llm = get_llm("reasoner")
    try:
        result = llm.chat_json(
            messages=[
                {"role": "system", "content": SYSTEM_M0_TOPIC_DISCOVERY},
                {
                    "role": "user",
                    "content": USER_M0_TOPIC_DISCOVERY.format(
                        raw_question=raw_question,
                        constraints=constraints or "(无)",
                        seed_evidence=seed_evidence or "(无)",
                        k=k,
                    ),
                },
            ],
            purpose="m0_discover",
            temperature=0.7,  # 选题阶段需要发散,温度略高
            max_tokens=2048,
        )
    except Exception as e:
        logger.warning("[M0] LLM 调用失败,返回空候选: {}", e)
        return []

    raw_topics = result.get("topics", []) or []
    cards: list[TopicCard] = []
    for raw in raw_topics:
        if not isinstance(raw, dict):
            continue
        title = (raw.get("title") or "").strip()
        if not title:
            continue  # 没标题的卡片直接丢弃
        card = TopicCard(
            topic_id=f"tc-{uuid.uuid4().hex[:8]}",
            title=title,
            research_direction=(raw.get("research_direction") or "").strip(),
            candidate_question=(raw.get("candidate_question") or title).strip(),
            suspected_gap=(raw.get("suspected_gap") or "").strip(),
            key_evidence=_as_list(raw.get("key_evidence")),
            novelty_rationale=(raw.get("novelty_rationale") or "").strip(),
            feasibility_rationale=(raw.get("feasibility_rationale") or "").strip(),
            risk_factors=_as_list(raw.get("risk_factors")),
            score=_as_float(raw.get("score"), default=0.0),
        )
        cards.append(card)

    cards.sort(key=lambda c: c.get("score", 0.0), reverse=True)
    logger.info("[M0] 生成 {} 个候选 TopicCard(top score={:.1f})",
                len(cards), cards[0]["score"] if cards else 0.0)
    return cards


def _as_list(v: Any) -> list[str]:
    """把 LLM 输出的列表字段稳健地归一化成 list[str]。"""
    if not v:
        return []
    if isinstance(v, list):
        return [str(x).strip() for x in v if str(x).strip()]
    return [str(v).strip()]


def _as_float(v: Any, *, default: float = 0.0) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


# ------------------------------------------------------------
# LangGraph 节点
# ------------------------------------------------------------


def topic_discovery_node(state: ResearchState) -> ResearchState:
    """
    LangGraph 节点:把 raw_question 转成 list[TopicCard]。

    ▍何时跳过
        - state 里已有 topic_cards(支持断点续跑) → 不重复跑
        - state 没 raw_question → 报错日志后空返回(safe_node 兜底)

    ▍constraints / seed_evidence 怎么进来
        从 state.metadata 里读 m0_constraints / m0_seed_evidence 两个键,
        用户可在 make_initial_state(metadata={...}) 时显式传入。
        没传就走"零先验"模式。
    """
    meta = state.get("metadata", {}) or {}
    if meta.get("skip_m0"):
        logger.info("[M0] metadata.skip_m0=True,跳过候选课题发现")
        return {}

    if state.get("topic_cards"):
        logger.info("[M0] 已有 topic_cards,跳过候选课题发现")
        return {}

    raw_q = (state.get("raw_question") or "").strip()
    if not raw_q:
        return {"error_log": ["[M0] 缺少 raw_question"]}

    constraints = meta.get("m0_constraints", "") or ""
    seed_evidence = meta.get("m0_seed_evidence", "") or ""
    k = int(meta.get("m0_k", settings.M0_DEFAULT_K) or settings.M0_DEFAULT_K)

    logger.info("[M0] 启动候选课题发现 raw='{}', k={}", raw_q[:60], k)
    cards = discover_topics(
        raw_q,
        k=k,
        constraints=constraints,
        seed_evidence=seed_evidence,
    )
    if not cards:
        logger.warning("[M0] 未生成任何候选,主流程将旁路到 M1")
        return {}

    return {"topic_cards": cards}
