"""
============================================================
 模块 8 多分支批处理 runner(m8_replay/multi_branch.py)
============================================================

🎓 教学目标
    整理版 §9.2 / §9.3 提出的能力:把 K 张 TopicCard(或 K 个 PICO 变体)各自跑一条
    完整的 M1-M7 流程,跑完后比较结果,选 winner 标记 mainline。

📌 设计取舍(MVP 串行版)
    1. 串行而不是并行:
       - LangGraph SqliteSaver 在多线程并发写时会有锁竞争;
       - 同一进程内并行还要处理 LLM 限流、cost_tracker 共享 SQLite 等问题;
       - MVP 顺序跑最稳,后续可改 ProcessPool / asyncio.to_thread。
    2. 不在 LangGraph 内部做回边:
       - 整理版 Phase D 不实现"M5.5→M2 自动跳回"的图内回边;
       - 而是让 multi_branch runner 看完每条 final state 的 metadata.research_gate,
         如果是 fetch_more_evidence/refine_question/choose_new_topic,就再开一条新 fork。
       - 这样图本身保持简单线性,Git-like 语义全在 runner 这一层。
    3. 每条 fork 用独立 fork_id 作 LangGraph thread_id,checkpointer 自然落到该分叉。

🔧 入口
    - run_topic_branches:有 M0 时,K 张 TopicCard → K 条 fork
    - run_pico_variant_branches:无 M0 时,K 个 PICO/raw_question 变体 → K 条 fork
    - 共同流程:create_fork → run_pipeline(thread_id=fork_id) → update_status →
      compare → mark_mainline

🧠 fork_id 与 LangGraph thread_id 的双重身份(关键概念)
    本项目里:fork_id == LangGraph thread_id == 同一个字符串。
    一个 ID 串起两套存储:
      - forks.db          按 fork_id 主键存元数据(parent / branch_node / final_rating)
      - graph.sqlite      按 thread_id 存 LangGraph 完整 State 快照

    为什么这么设计?
      1) Single source of truth:每条研究路径有唯一 ID
      2) 创建分支 = 给 LangGraph 拉个新 thread:
           graph.invoke(state, config={"configurable": {"thread_id": fork_id}})
      3) 续跑某分支 = 用同一个 thread_id 让 SqliteSaver 加载快照
      4) 前端拿 fork_id 同时能查元数据(快)和详细 state(走 GET /api/forks/{id})

    这是"Git 提交哈希"思路:一个不透明 ID 锚定一个完整状态。

------------------------------------------------------------
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Callable

from co_scientist.llm import get_llm
from co_scientist.modules.m8_replay.fork_manager import ForkManager, ForkMeta
from co_scientist.state import ResearchState, TopicCard
from co_scientist.utils import logger


# ------------------------------------------------------------
# 单个 fork 跑完后的结果对象
# ------------------------------------------------------------


@dataclass
class BranchResult:
    """
    单条 fork 的运行结果,聚合给 compare/winner 选择用。

    fork_meta     : ForkManager 里登记的元信息(已被本 runner 更新过 status/rating)
    final_state   : LangGraph 跑完的 ResearchState(含 paper_draft/decision_card/...)
    summary       : 提炼出来的简短摘要(给 LLM/前端看的)
    error         : 跑失败时的异常信息;成功为空字符串
    """

    fork_meta: ForkMeta
    final_state: ResearchState | None
    summary: dict[str, Any]
    error: str = ""


# ------------------------------------------------------------
# 摘要工具:把 final state 压成对比卡片
# ------------------------------------------------------------


def _summarize_state(state: ResearchState | None) -> dict[str, Any]:
    """
    把 final_state 压成 ~10 个字段的摘要,便于 compare LLM 读 / 前端渲染。

    ▍为什么不直接把整个 state 丢给 LLM
        State 里 papers/triples 体量很大,塞进 compare prompt 浪费 token,
        重要的是高层信号:论文标题、决策、评分、实验关键字段。
    """
    if not state:
        return {}
    meta_dec = state.get("meta_decision") or {}
    decision_card = state.get("decision_card") or {}
    exp = state.get("experiment_plan") or {}
    paper_draft = state.get("paper_draft") or {}
    research_gate = (state.get("metadata") or {}).get("research_gate") or {}

    return {
        "raw_question": state.get("raw_question", "")[:200],
        "refined_question": (state.get("pico") or {}).get("refined_question", "")[:200],
        "n_papers": len(state.get("papers") or []),
        "n_gap_cards": len(state.get("gap_cards") or []),
        "n_critiques": len(state.get("critiques") or []),
        "decision": meta_dec.get("decision", "") or decision_card.get("decision", ""),
        "final_rating": float(
            meta_dec.get("final_rating", 0)
            or decision_card.get("final_rating", 0)
            or 0.0
        ),
        "recommended_action": decision_card.get("recommended_action", ""),
        "research_gate_decision": research_gate.get("gate_decision", ""),
        "experiment_name": exp.get("name", ""),
        "experiment_baselines": exp.get("baselines", [])[:3],
        "experiment_metrics": exp.get("metrics", []),
        "paper_title": paper_draft.get("title", "")[:120],
    }


# ------------------------------------------------------------
# 单条 fork 跑完后的元数据回写
# ------------------------------------------------------------


def _persist_fork_result(
    fm: ForkManager,
    fork_meta: ForkMeta,
    final_state: ResearchState | None,
    error: str,
) -> ForkMeta:
    """跑完后把 status / final_rating 写回 forks 表,并刷新 ForkMeta 字段。"""
    if error or final_state is None:
        fm.update_status(fork_meta.fork_id, "abandoned", final_rating=0.0)
        fork_meta.status = "abandoned"
        fork_meta.final_rating = 0.0
        return fork_meta

    rating = float(
        (final_state.get("decision_card") or {}).get("final_rating", 0)
        or (final_state.get("meta_decision") or {}).get("final_rating", 0)
        or 0.0
    )
    fm.update_status(fork_meta.fork_id, "done", final_rating=rating)
    fork_meta.status = "done"
    fork_meta.final_rating = rating
    return fork_meta


# ------------------------------------------------------------
# Runner 主体
# ------------------------------------------------------------


# 默认拿 graph.run_pipeline 当 runner;测试可以注入桩函数避免真跑 LangGraph。
RunPipelineFn = Callable[..., ResearchState]


def _default_run_pipeline(*args: Any, **kwargs: Any) -> ResearchState:
    """延迟 import 避免循环依赖(graph.py 反过来不依赖本文件)。"""
    from co_scientist.graph import run_pipeline as _rp
    return _rp(*args, **kwargs)


def run_topic_branches(
    raw_question: str,
    topic_cards: list[TopicCard],
    *,
    parent_fork_id: str = "",
    execution_mode: str = "generate_only",
    budget_usd: float | None = None,
    fork_manager: ForkManager | None = None,
    run_pipeline: RunPipelineFn | None = None,
) -> tuple[BranchResult | None, list[BranchResult]]:
    """
    整理版 §9.2:有 M0 时,K 张 TopicCard 各跑一条完整 M1-M7 fork。

    Args:
        raw_question:用户的初始问题(无 M0 时也作 fallback,这里主要给 fork 描述)
        topic_cards:M0 输出的候选课题卡片列表
        parent_fork_id:可选,根分叉 ID;空串表示当前是研究会话的开端
        execution_mode:M6 执行档位(generate_only / dry_run / full_execute)
        budget_usd:每条 fork 的预算上限(None=用 settings 默认)
        fork_manager / run_pipeline:依赖注入,便于测试

    Returns:
        (winner_branch, all_branches)
        winner_branch 为 final_rating 最高且非 abandoned 的;若全失败返回 None。

    ▍为什么 winner 选 final_rating 最高
        Phase D 第一阶段先用规则:final_rating 是 M4 Meta-Reviewer 给的综合分,
        最能代表"这条研究方向的整体质量"。Phase D.3 加 LLM 评分时再综合多维度。

    ▍每条 fork 跑完用 update_status 持久化原因
        即使没人 list_forks,也要保证下次进程启动能从 forks.db 看到完整树。
        rating 也写回去,前端列表展示无需把 final state 都查一遍。
    """
    if not topic_cards:
        logger.warning("[M8.multi_branch] topic_cards 为空,跳过")
        return None, []

    fm = fork_manager or ForkManager()
    runner = run_pipeline or _default_run_pipeline

    # Step 1:为每张 TopicCard 创建一条 fork
    metas = fm.branch_from_topic_cards(topic_cards, parent_fork_id=parent_fork_id)

    # Step 2:逐条跑 graph(MVP 串行)
    results: list[BranchResult] = []
    for card, meta in zip(topic_cards, metas):
        # 把 candidate_question 当作这条 fork 的 raw_question(若无则用卡片 title)
        fork_question = (
            card.get("candidate_question")
            or card.get("title")
            or raw_question
        )
        logger.info(
            "[M8.multi_branch] 跑 fork={} topic={} question='{}'",
            meta.fork_id, meta.topic_id, fork_question[:60],
        )
        try:
            final_state = runner(
                fork_question,
                execution_mode=execution_mode,
                fork_id=meta.fork_id,
                budget_usd=budget_usd,
                metadata={"skip_m0": True, "selected_topic_id": meta.topic_id},
            )
        except Exception as e:
            logger.warning("[M8.multi_branch] fork {} 跑挂: {}", meta.fork_id, e)
            updated = _persist_fork_result(fm, meta, None, error=str(e))
            results.append(BranchResult(
                fork_meta=updated, final_state=None, summary={}, error=str(e),
            ))
            continue

        updated = _persist_fork_result(fm, meta, final_state, error="")
        results.append(BranchResult(
            fork_meta=updated,
            final_state=final_state,
            summary=_summarize_state(final_state),
            error="",
        ))

    # Step 3:选 winner(final_rating 最高,非 abandoned)
    winner_meta = fm.get_winner([m.fork_id for m in metas])
    winner_branch: BranchResult | None = None
    if winner_meta:
        for r in results:
            if r.fork_meta.fork_id == winner_meta.fork_id:
                winner_branch = r
                break

    return winner_branch, results


def run_pico_variant_branches(
    raw_question: str,
    pico_variants: list[str],
    *,
    parent_fork_id: str = "",
    execution_mode: str = "generate_only",
    budget_usd: float | None = None,
    fork_manager: ForkManager | None = None,
    run_pipeline: RunPipelineFn | None = None,
) -> tuple[BranchResult | None, list[BranchResult]]:
    """
    整理版 §9.3:无 M0 时,把 raw_question 改写出 K 个 PICO/问题变体,各跑一条 fork。

    本函数只接受现成的 variant 字符串列表(变体生成由调用方负责,通常用 m1.refine 多次或
    手动给定)。把"变体生成"和"批量跑"职责拆开,便于复用与测试。
    """
    if not pico_variants:
        logger.warning("[M8.multi_branch] pico_variants 为空,跳过")
        return None, []

    fm = fork_manager or ForkManager()
    runner = run_pipeline or _default_run_pipeline

    metas: list[ForkMeta] = []
    for i, _ in enumerate(pico_variants, start=1):
        metas.append(fm.create_fork(
            parent_fork_id=parent_fork_id,
            branch_node="m1_refine",
            description=f"PICO 变体 #{i}",
        ))

    results: list[BranchResult] = []
    for variant_q, meta in zip(pico_variants, metas):
        try:
            final_state = runner(
                variant_q,
                execution_mode=execution_mode,
                fork_id=meta.fork_id,
                budget_usd=budget_usd,
                metadata={"skip_m0": True},
            )
        except Exception as e:
            logger.warning("[M8.multi_branch] fork {} 跑挂: {}", meta.fork_id, e)
            updated = _persist_fork_result(fm, meta, None, error=str(e))
            results.append(BranchResult(
                fork_meta=updated, final_state=None, summary={}, error=str(e),
            ))
            continue

        updated = _persist_fork_result(fm, meta, final_state, error="")
        results.append(BranchResult(
            fork_meta=updated,
            final_state=final_state,
            summary=_summarize_state(final_state),
            error="",
        ))

    winner_meta = fm.get_winner([m.fork_id for m in metas])
    winner_branch: BranchResult | None = None
    if winner_meta:
        for r in results:
            if r.fork_meta.fork_id == winner_meta.fork_id:
                winner_branch = r
                break
    return winner_branch, results


# ------------------------------------------------------------
# Phase D.3:LLM 综合评分(critical 角色)
# ------------------------------------------------------------


COMPARE_SYSTEM = """\
你是科研路线对比专家。读取多条研究分支的摘要(每条含研究问题、决策、评分、实验关键字段、论文标题等),
综合评估并选出 winner。考虑维度:

1. 研究问题清晰度 + 与原始意图的契合度
2. 评审 final_rating + recommended_action(高分 + continue 优先)
3. 实验方案完整性(数据集 / baseline / 指标是否齐全)
4. 论文草稿是否产出
5. ResearchGate 是否放行(continue_to_m6 优先)
6. 风险信号(blocking_issues / abandoned 状态)

输出 JSON 严格 schema:
{
  "winner_fork_id": "...",
  "winner_score": 0-10,
  "ranking": [
    {"fork_id": "...", "score": 0-10, "reason": "<=80 字"},
    ...
  ],
  "comparison_summary": "全局对比结论 <=120 字"
}

要求:
- ranking 必须包含全部输入 fork_id;按 score 降序。
- winner_fork_id = ranking[0].fork_id。
- 严格输出 JSON,不要额外 markdown。
"""


def score_branches_with_llm(
    branches: list[BranchResult],
    *,
    use_critical: bool = True,
) -> dict[str, Any]:
    """
    用 LLM 综合多个维度对分支评分,返回 winner_fork_id + ranking。

    Args:
        branches:run_topic_branches 返回的 all_branches
        use_critical:True 用 critical(Claude opus)做关键裁决;False 用 reasoner

    Returns:
        {"winner_fork_id": "...", "winner_score": 0-10, "ranking": [...], "comparison_summary": "..."}
        失败时返回 {} 让上层降级到规则版(final_rating 最高)。

    ▍为什么默认 critical
        多分支对比是高价值低频次的关键裁决,符合整理版 §3.2 critical 触发条件:
        "M8 多分支评分接近,winner 不明显"。这种情况下 Claude opus + Extended Thinking
        比 GPT 给的 ranking 更有方向性。
    ▍为什么允许降级到 reasoner
        预算紧张 / 中转站 critical 不可用 / Phase D 起步阶段 → use_critical=False
        让分支评分仍然能跑,只是质量打折。
    """
    successful = [b for b in branches if b.error == "" and b.summary]
    if len(successful) < 2:
        logger.info(
            "[M8.compare] 成功分支不足 2 条({}),不调用 LLM",
            len(successful),
        )
        return {}

    payload = [
        {"fork_id": b.fork_meta.fork_id, "description": b.fork_meta.description, **b.summary}
        for b in successful
    ]
    role = "critical" if use_critical else "reasoner"

    try:
        llm = get_llm(role)
        result = llm.chat_json(
            messages=[
                {"role": "system", "content": COMPARE_SYSTEM},
                {
                    "role": "user",
                    "content": (
                        "以下是各分支摘要(JSON):\n"
                        + json.dumps(payload, ensure_ascii=False, indent=2)
                        + "\n\n请输出 ranking JSON。"
                    ),
                },
            ],
            purpose="m8_compare",
            temperature=0.3,
            max_tokens=2048,
        )
    except Exception as e:
        logger.warning("[M8.compare] LLM 评分失败,降级规则版: {}", e)
        return {}

    winner_id = (result.get("winner_fork_id") or "").strip()
    valid_ids = {b.fork_meta.fork_id for b in successful}
    if winner_id not in valid_ids:
        # LLM 偶发幻觉给了不存在的 fork_id;取 ranking 第一项兜底
        ranking = result.get("ranking") or []
        if ranking and isinstance(ranking, list):
            top = ranking[0]
            if isinstance(top, dict) and (top.get("fork_id") in valid_ids):
                winner_id = top["fork_id"]
        if winner_id not in valid_ids:
            logger.warning("[M8.compare] LLM 输出 winner_fork_id 不在候选,降级")
            return {}

    return {
        "winner_fork_id": winner_id,
        "winner_score": float(result.get("winner_score", 0.0) or 0.0),
        "ranking": result.get("ranking") or [],
        "comparison_summary": str(result.get("comparison_summary", "")),
    }


# ------------------------------------------------------------
# 简单 merge:把 winner 标记为 mainline
# ------------------------------------------------------------


def merge_winner(
    branches: list[BranchResult],
    *,
    fork_manager: ForkManager | None = None,
    use_llm_compare: bool = False,
) -> BranchResult | None:
    """
    整理版 §9.5 第一阶段:merge = 选评分最高 / LLM 综合 / 用户确认的 winner,标记为 mainline。

    Args:
        branches:run_topic_branches / run_pico_variant_branches 返回的 all_branches
        use_llm_compare:True 时先调用 score_branches_with_llm 综合评分;失败回落规则版
                        (规则版 = final_rating 最高,等价于 fork_manager.get_winner)
    Returns:
        被选为 winner 的 BranchResult;全失败时 None。

    ▍为什么默认 use_llm_compare=False
        - 规则版零 LLM 成本,够 MVP 用;
        - LLM 综合评分是 Phase D.3 的"加分项",不应让基础 merge 强依赖。
        调用方需要更细粒度时显式传 True 即可。
    """
    fm = fork_manager or ForkManager()
    candidate_ids = [b.fork_meta.fork_id for b in branches if b.error == ""]
    if not candidate_ids:
        logger.warning("[M8.multi_branch] 没有可 merge 的成功分支")
        return None

    winner_id: str | None = None
    if use_llm_compare:
        ranking = score_branches_with_llm(branches)
        if ranking:
            winner_id = ranking.get("winner_fork_id") or None

    if not winner_id:
        winner_meta = fm.get_winner(candidate_ids)
        if winner_meta:
            winner_id = winner_meta.fork_id

    if not winner_id:
        return None

    fm.mark_mainline(winner_id)
    for b in branches:
        if b.fork_meta.fork_id == winner_id:
            b.fork_meta.status = "mainline"
            return b
    return None
