"""
============================================================
 模块 3:知识图谱构建(m3_kg/kg_builder.py)
============================================================

🎓 教学目标
    把检索到的论文摘要喂给 LLM 抽三元组 → 存进图数据库 → 可视化。
    这是 GraphRAG 的基础组件。

📌 实现策略
    - 存储:默认 NetworkX(轻量、无需部署),完整模式可切 Neo4j
    - 抽取:批量并行,每篇论文一次 LLM 调用
    - 去噪:关系类型白名单,非列表关系直接丢弃

🔧 研究空白识别(附加价值)
    - 图里有"问题/挑战"类节点但没有对应"方法"节点 → 研究空白
    - 简单启发式实现,后续可换成更复杂的子图匹配

------------------------------------------------------------
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import networkx as nx

import json
import uuid
from typing import Any

from co_scientist.config import settings
from co_scientist.llm import get_llm
from co_scientist.prompts.templates import (
    SYSTEM_M3_GAP_CARD,
    SYSTEM_M3_TRIPLE_EXTRACT,
    USER_M3_GAP_CARD,
    USER_M3_TRIPLE_EXTRACT,
)
from co_scientist.state import GapCard, Paper, ResearchState, Triple
from co_scientist.utils import logger

VALID_RELATIONS = {
    "improves",
    "uses",
    "compares_with",
    "cites",
    "proposes",
    "evaluates_on",
}


# ------------------------------------------------------------
# 三元组抽取(单篇)
# ------------------------------------------------------------


async def _extract_triples_from_paper(paper: Paper) -> list[Triple]:
    """
    LLM 抽取一篇论文的三元组。
    用 asyncio.to_thread 把同步 LLM 调用丢到线程池,避免阻塞事件循环。
    """
    title = paper.get("title", "")
    abstract = paper.get("abstract", "")
    if not abstract:
        return []  # 没摘要直接跳过

    def _call() -> list[Triple]:
        llm = get_llm("chat")  # 批量任务用便宜模型
        try:
            result = llm.chat_json(
                messages=[
                    {"role": "system", "content": SYSTEM_M3_TRIPLE_EXTRACT},
                    {
                        "role": "user",
                        "content": USER_M3_TRIPLE_EXTRACT.format(
                            title=title, abstract=abstract
                        ),
                    },
                ],
                purpose="m3_triple_extract",
                temperature=0.2,
            )
        except Exception as e:
            logger.warning("[M3] 抽取失败 {}: {}", title[:40], e)
            return []

        raw_triples = result.get("triples", [])
        triples: list[Triple] = []
        for t in raw_triples:
            if not isinstance(t, dict):
                continue
            rel = t.get("relation", "").lower().strip()
            if rel not in VALID_RELATIONS:
                continue  # 关系白名单过滤,防噪音
            head = (t.get("head") or "").strip()
            tail = (t.get("tail") or "").strip()
            if not head or not tail:
                continue
            triples.append(
                Triple(
                    head=head,
                    relation=rel,
                    tail=tail,
                    source_paper_id=paper.get("id", ""),
                )
            )
        return triples

    return await asyncio.to_thread(_call)


async def extract_triples_batch(
    papers: list[Paper], concurrency: int = 5
) -> list[Triple]:
    """
    批量抽取三元组。用 Semaphore 限制并发,避免 LLM 限流。
    """
    sem = asyncio.Semaphore(concurrency)

    async def _with_sem(p: Paper) -> list[Triple]:
        async with sem:
            return await _extract_triples_from_paper(p)

    results = await asyncio.gather(*(_with_sem(p) for p in papers))
    flat = [t for sub in results for t in sub]
    logger.info("[M3] 从 {} 篇论文抽出 {} 条三元组", len(papers), len(flat))
    return flat


# ------------------------------------------------------------
# 图构建
# ------------------------------------------------------------


def build_graph(triples: list[Triple]) -> nx.MultiDiGraph:
    """
    三元组 → NetworkX 有向多重图。
    用 MultiDiGraph 是因为 A-improves-B 和 A-compares_with-B 可能同时存在。
    """
    g = nx.MultiDiGraph()
    for t in triples:
        g.add_edge(
            t["head"],
            t["tail"],
            relation=t["relation"],
            source=t.get("source_paper_id", ""),
        )
    logger.info("[M3] 图构建完成: {} 节点, {} 边", g.number_of_nodes(), g.number_of_edges())
    return g


def identify_research_gaps(g: nx.MultiDiGraph) -> list[str]:
    """
    简单的研究空白识别启发式:
      - 找出被 "improves"、"proposes" 指向的实体(多次出现的改进目标)
      - 但本身很少作为 head(很少有人去改它)
      - 这类实体可能是"待改进空白"

    真实项目应该用更复杂的子图模式匹配,这里做个教学版。
    """
    in_improves: dict[str, int] = {}
    out_count: dict[str, int] = {}

    for _h, t, data in g.edges(data=True):
        if data.get("relation") in {"improves", "proposes"}:
            in_improves[t] = in_improves.get(t, 0) + 1

    for h, _t, _data in g.edges(data=True):
        out_count[h] = out_count.get(h, 0) + 1

    gaps: list[str] = []
    for node, cnt in in_improves.items():
        if cnt >= 2 and out_count.get(node, 0) < cnt:
            # 被多人想改进,但本身提供改进方案的次数少 → 可能是待解决问题
            gaps.append(node)

    gaps.sort(key=lambda n: in_improves[n], reverse=True)
    return gaps[:10]


# ------------------------------------------------------------
# 持久化
# ------------------------------------------------------------


def build_gap_cards(
    refined_question: str,
    gap_nodes: list[str],
    papers: list[Paper],
    *,
    top_n: int = 8,
    max_cards: int = 5,
) -> list[GapCard]:
    """
    把启发式识别出的 gap 节点列表升级成结构化 GapCard。

    ▍为什么 m3 既保留 research_gaps 又新增 gap_cards
        整理版 Phase B 是渐进升级:不破坏老下游(m4/m5/m7 仍读 research_gaps),
        同时把新 GapCard 推给愿意消费的下游(整理版要求 m4/m5 优先读 GapCard)。
        两个字段共存到 Phase D 完成后,再考虑废弃 research_gaps。

    ▍为什么不每个 gap 节点单独调一次 LLM
        - 节点数最多 10 个,合并一次调用更省钱、上下文也更连贯;
        - 单次调用让 LLM 能在多个 gap 之间互相比较,排序更稳。

    ▍失败为什么返回空列表
        gap_cards 是新字段,业务模块没有强依赖。失败时让上游降级到只用 research_gaps,
        不应阻塞主流程。

    🔗 下游消费契约
        M5.design_experiment(Phase B 起优先读)
            → 拿 gap_cards[i].datasets/baselines/metrics 作为实验先验拼进 prompt
        M5.5.decide_gate(Phase C)
            → 引用 chosen_gap.missing_piece 拼进 LLM 综合判断
        M4.build_decision_card(Phase C)
            → 用 chosen_gap 的 evidence_level / novelty_score 作为 DecisionCard 上下文
        前端 GapCardList(page.tsx)
            → 渲染卡片,高亮 current_gap_id 选中项
    """
    if not gap_nodes:
        return []
    if not papers:
        # 没有论文,无法构造 evidence_papers,直接降级:每个 gap 节点对应一张半空 GapCard
        return [
            GapCard(
                gap_id=f"gc-{uuid.uuid4().hex[:8]}",
                title=node,
                problem=f"启发式识别的待改进节点:{node}",
                evidence_papers=[],
                existing_methods=[],
                missing_piece="",
                datasets=[],
                baselines=[],
                metrics=[],
                novelty_score=0.0,
                feasibility_score=0.0,
                evidence_level="low",
                risks=["缺少证据论文,需在 M2.5 补全访问状态"],
            )
            for node in gap_nodes[:max_cards]
        ]

    summaries = "\n".join(
        f"[{p.get('id', '?')}] {p.get('title', '')[:80]}: "
        f"{(p.get('abstract') or '')[:200]}"
        for p in papers[:top_n]
    )

    llm = get_llm("reasoner")
    try:
        result = llm.chat_json(
            messages=[
                {"role": "system", "content": SYSTEM_M3_GAP_CARD},
                {
                    "role": "user",
                    "content": USER_M3_GAP_CARD.format(
                        refined_question=refined_question or "(无)",
                        gap_nodes=json.dumps(gap_nodes, ensure_ascii=False),
                        top_n=min(top_n, len(papers)),
                        paper_summaries=summaries,
                        max_cards=max_cards,
                    ),
                },
            ],
            purpose="m3_gap_card",
            temperature=0.3,
            max_tokens=2048,
        )
    except Exception as e:
        logger.warning("[M3] GapCard 生成失败,降级为只输出 research_gaps: {}", e)
        return []

    raw_cards = result.get("gap_cards", []) or []
    cards: list[GapCard] = []
    for raw in raw_cards:
        if not isinstance(raw, dict):
            continue
        title = (raw.get("title") or "").strip()
        if not title:
            continue
        cards.append(
            GapCard(
                gap_id=f"gc-{uuid.uuid4().hex[:8]}",
                title=title,
                problem=(raw.get("problem") or "").strip(),
                evidence_papers=_as_str_list(raw.get("evidence_papers")),
                existing_methods=_as_str_list(raw.get("existing_methods")),
                missing_piece=(raw.get("missing_piece") or "").strip(),
                datasets=_as_str_list(raw.get("datasets")),
                baselines=_as_str_list(raw.get("baselines")),
                metrics=_as_str_list(raw.get("metrics")),
                novelty_score=_as_float(raw.get("novelty_score")),
                feasibility_score=_as_float(raw.get("feasibility_score")),
                evidence_level=_as_evidence_level(raw.get("evidence_level")),
                risks=_as_str_list(raw.get("risks")),
            )
        )

    cards.sort(
        key=lambda c: (
            c.get("novelty_score", 0.0) * c.get("feasibility_score", 0.0)
        ),
        reverse=True,
    )
    logger.info("[M3] 生成 {} 张 GapCard(top novelty*feasibility={:.1f})",
                len(cards),
                cards[0].get("novelty_score", 0) * cards[0].get("feasibility_score", 0)
                if cards else 0)
    return cards[:max_cards]


def _as_str_list(v: Any) -> list[str]:
    if not v:
        return []
    if isinstance(v, list):
        return [str(x).strip() for x in v if str(x).strip()]
    return [str(v).strip()]


def _as_float(v: Any, default: float = 0.0) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _as_evidence_level(v: Any) -> str:
    """归一化 evidence_level 到 high/medium/low,默认 medium。"""
    if not isinstance(v, str):
        return "medium"
    s = v.strip().lower()
    if s in {"high", "medium", "low"}:
        return s
    return "medium"


def save_graph(g: nx.MultiDiGraph, path: Path | None = None) -> Path:
    """把图存成 GraphML 格式,前端可用 Cytoscape.js 直接读。"""
    out = Path(path or settings.OUTPUT_DIR / "knowledge_graph.graphml")
    out.parent.mkdir(parents=True, exist_ok=True)
    nx.write_graphml(g, out)
    logger.info("[M3] 图已保存: {}", out)
    return out


# ------------------------------------------------------------
# LangGraph 节点
# ------------------------------------------------------------


def build_kg_node(state: ResearchState) -> ResearchState:
    """
    LangGraph 节点。

    ▍整理版 Phase B 升级
        在原 research_gaps(list[str])基础上增加 gap_cards(list[GapCard])。
        两个字段并存:旧下游(m4/m5/m7)继续读 research_gaps,新下游(整理版 Phase C
        的 m4/m5)优先读 gap_cards 拿 datasets/baselines/metrics 等先验。
        失败时只回写 research_gaps,不阻塞主流程。
    """
    papers = state.get("papers", [])
    if not papers:
        logger.warning("[M3] 没有论文可用")
        return {}

    # 只对 top-N 抽,省钱
    topn = papers[:30]
    triples = asyncio.run(extract_triples_batch(topn, concurrency=5))
    g = build_graph(triples)
    gaps = identify_research_gaps(g)
    save_graph(g)

    # ---- 整理版 Phase B:升级成 GapCard 列表 ----
    refined_q = (state.get("pico", {}) or {}).get(
        "refined_question", state.get("raw_question", "")
    )
    gap_cards = build_gap_cards(refined_q, gaps, papers)

    patch: dict = {
        "triples": triples,
        "research_gaps": gaps,
    }
    if gap_cards:
        patch["gap_cards"] = gap_cards
        # 默认把第一张(评分最高)设为 current_gap_id;让 M5 默认拿它做先验。
        # 用户/M8 可在 Phase D 中覆盖这个选择。
        patch["current_gap_id"] = gap_cards[0].get("gap_id", "")
    return patch
