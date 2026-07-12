"""
============================================================
 模块 2.5:文献访问状态层(m2_5_access_status/access_status.py)
============================================================

🎓 教学目标
    整理版 §5 提出的 M2.5 层:不要把"找到一篇论文"和"能不能拿到全文/代码/数据集"
    混为一谈。M2 只负责检索,M2.5 把每篇论文的访问状态结构化,
    M3/M4/M5 据此为证据降权。

📌 设计取舍(MVP 版,不联网)
    1. 真实生产应该:发 HTTP HEAD 请求验全文 URL、抓 OpenAlex/Unpaywall 是否 OA、
       crawl GitHub/HuggingFace 找代码与数据集。
    2. MVP 用启发式规则:
       - source=arxiv 或 url 含 arxiv.org / openaccess → fulltext, evidence_level high
       - 已有 doi 但无 abstract → restricted, low
       - cited_by_count 高 + 有摘要 → abstract_only, medium
       - 其余 → abstract_only, medium(默认)
    3. has_code / has_dataset:从 paper.raw 里嗅探 url 字段或论文标题/摘要关键词
       (e.g. "code available at https://github.com/..." → has_code=True)。
    4. 这一层是可插拔的 — 后续可以替换成真正的 Unpaywall/CrossRef 调用,
       state 字段不变,业务模块零感知。

🔧 与图的衔接
    graph.py 在 m2_retrieve 之后、m3_kg 之前插入 m2_5_access_status 节点。
    输出 state.evidence_access_status: list[EvidenceAccessStatus],
    顺序与 state.papers 一一对应(用 paper_id 关联)。

🔗 下游消费契约(谁读这个字段、做什么用)
    M3.build_gap_cards(prompts/templates.py SYSTEM_M3_GAP_CARD)
        → 用 evidence_level 给 GapCard.evidence_level 赋值;有 code+dataset 升档
    M4.build_decision_card(roundtable.py)
        → access_summary 拼进 LLM user prompt(level 分布 / has_code 比例)
    M5.5.decide_gate(_heuristic_gate)
        → 超半数 evidence_level=='low' 直接 fetch_more_evidence(整理版 §5.3)
    前端 AccessStatusSummary
        → 计数与 has_code/has_dataset 数字展示

    所以本节点的 evidence_level 字段是"全链路降权机制"的源头,改启发式规则
    会传播到 4 处下游;改字段名必须同步上述所有消费者。

------------------------------------------------------------
"""

from __future__ import annotations

import re
from typing import Iterable

from co_scientist.state import EvidenceAccessStatus, Paper, ResearchState
from co_scientist.utils import logger


_OPEN_ACCESS_HOST_RE = re.compile(
    r"(arxiv\.org|openaccess|biorxiv\.org|medrxiv\.org|aclanthology\.org|"
    r"openreview\.net|openalex\.org|pubmed\.ncbi)",
    re.IGNORECASE,
)
_GITHUB_RE = re.compile(r"github\.com/[A-Za-z0-9_.\-]+/[A-Za-z0-9_.\-]+", re.IGNORECASE)
_DATASET_HINT_RE = re.compile(
    r"(huggingface\.co/datasets|dataset is publicly|publicly available dataset|"
    r"benchmark.*release|kaggle\.com/datasets)",
    re.IGNORECASE,
)
_BENCHMARK_HINT_RE = re.compile(
    r"(benchmark|leaderboard|standard evaluation|evaluation suite)", re.IGNORECASE
)


def _classify_one(paper: Paper) -> EvidenceAccessStatus:
    """
    单篇论文的启发式分级。所有判断都基于 metadata,不发 HTTP。

    ▍为什么用宽松的"abstract_only + medium"做兜底
        启发式拿不准时,默认状态不应过分悲观/乐观:
          - 标 restricted 会让 M3/M4 整体降权,但很多预印本其实是 OA;
          - 标 fulltext 又可能误导 M5 实验设计依赖一个其实拿不到的数据集。
        abstract_only + medium 让下游"可用,但请审慎",最稳。
    """
    paper_id = paper.get("id", "") or paper.get("arxiv_id", "") or paper.get("doi", "")
    abstract = paper.get("abstract", "") or ""
    url = paper.get("url", "") or ""
    arxiv_id = paper.get("arxiv_id", "") or ""
    source = paper.get("source", "") or ""
    raw = paper.get("raw", "")

    notes: list[str] = []

    # 1) access_status / evidence_level
    looks_open = bool(arxiv_id) or "arxiv" in source.lower() or _OPEN_ACCESS_HOST_RE.search(url or "")
    has_doi = bool(paper.get("doi"))
    has_abstract = bool(abstract.strip())

    if looks_open and has_abstract:
        access_status = "fulltext"
        evidence_level = "high"
        notes.append("命中开放访问站点(arxiv/openreview/biorxiv 等)")
    elif has_abstract and has_doi:
        access_status = "abstract_only"
        evidence_level = "medium"
        notes.append("有 DOI 与摘要,但未识别为 OA")
    elif has_abstract:
        access_status = "abstract_only"
        evidence_level = "medium"
    elif has_doi:
        access_status = "restricted"
        evidence_level = "low"
        notes.append("仅元数据,无摘要")
    else:
        access_status = "failed"
        evidence_level = "low"
        notes.append("元数据残缺")

    # 2) has_code / has_dataset / has_benchmark:在 abstract / raw / url 里嗅探
    blob = " ".join(str(x) for x in (abstract, url, raw))
    has_code = bool(_GITHUB_RE.search(blob))
    has_dataset = bool(_DATASET_HINT_RE.search(blob))
    has_benchmark = bool(_BENCHMARK_HINT_RE.search(blob))

    # 3) 升级规则:有代码 + 数据集 + 全文 → 可保持 high;
    #    有代码 + 摘要 → 升一档(medium → high)
    if has_code and has_dataset and access_status == "fulltext":
        evidence_level = "high"
    elif has_code and access_status == "abstract_only":
        evidence_level = "high"
        notes.append("识别到 GitHub 代码,从 medium 升 high")

    return EvidenceAccessStatus(
        paper_id=paper_id,
        access_status=access_status,
        has_code=has_code,
        has_dataset=has_dataset,
        has_benchmark=has_benchmark,
        evidence_level=evidence_level,
        notes=notes,
    )


def parse_access_status(papers: Iterable[Paper]) -> list[EvidenceAccessStatus]:
    """对一组 papers 批量分级。"""
    out: list[EvidenceAccessStatus] = []
    for p in papers:
        try:
            out.append(_classify_one(p))
        except Exception as e:
            logger.warning("[M2.5] paper={} 分级失败: {}", p.get("id", "?"), e)
            out.append(
                EvidenceAccessStatus(
                    paper_id=p.get("id", "") or "",
                    access_status="failed",
                    has_code=False,
                    has_dataset=False,
                    has_benchmark=False,
                    evidence_level="low",
                    notes=[f"分级异常: {type(e).__name__}"],
                )
            )
    return out


# ------------------------------------------------------------
# LangGraph 节点
# ------------------------------------------------------------


def access_status_node(state: ResearchState) -> dict:
    """
    LangGraph 节点。在 M2 之后跑:对 state.papers 批量解析访问状态,
    写到 state.evidence_access_status。

    ▍为什么不和 M2 合并
        - M2 关注"找论文",已经够复杂(多源 + RRF + embedding rerank);
        - M2.5 关注"论文能不能用",未来要换 Unpaywall/HTTP 探测,接口稳定;
        - 拆开后,各模块单一职责,降级也独立(M2.5 失败不影响 M2 输出)。

    ▍为什么这里不返回 paper_id 列表的 dict 而是 list
        list 顺序与 state.papers 一致,前端/M3/M4 直接 zip 即可对齐;
        如果未来要按 paper_id 索引,在消费方做 {s["paper_id"]: s for s in list} 一行搞定。
    """
    papers = state.get("papers", []) or []
    if not papers:
        logger.info("[M2.5] 无 papers,跳过访问状态解析")
        return {}
    if state.get("evidence_access_status"):
        logger.info("[M2.5] 已有 evidence_access_status,跳过")
        return {}

    statuses = parse_access_status(papers)
    # 简短统计便于排查
    levels: dict[str, int] = {}
    for s in statuses:
        levels[s.get("evidence_level", "?")] = levels.get(s.get("evidence_level", "?"), 0) + 1
    logger.info("[M2.5] 解析 {} 篇:{}", len(statuses), levels)
    return {"evidence_access_status": statuses}
