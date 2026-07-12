"""
============================================================
 模块 2:Citation Chasing(m2_retriever/citation_chase.py)
============================================================

🎓 教学目标
    核心论文的"参考文献"和"被引论文"往往是研究圈子的边界。
    拿到 top K 核心论文后,沿着引用图谱再扩展 1-2 跳,
    可以显著提高综述覆盖率。

    这里用 Semantic Scholar 的 /references 和 /citations 端点。

💡 references vs citations:两个方向的语义差别
    - references: "这篇论文引用了谁" → 往过去走,追溯理论源头 / 经典基线
    - citations:  "谁引用了这篇论文" → 往未来走,发现后续改进 / SOTA 追赶者
    综述两者都要,缺一漏:
      - 只走 references,会错过最新后续工作
      - 只走 citations,会缺理论根基

📌 复杂度控制:为什么不无脑递归
    每篇论文平均有 20-50 条引用/被引,1 跳扩展 10 篇核心 = 200-500 篇。
    所以要:
      - 只对 top-K 论文扩展(一般 K=5):超过 5 篇种子召回增量很小
      - 每篇只取引用最高的 N 篇(N=10):按 S2 的默认相关度排序近似"最相关"
      - 2 跳很危险,默认只做 1 跳:
        * 2 跳 = K × N × N ≈ 5 × 10 × 10 = 500 篇 × 每篇 1 次 API,成本爆炸
        * 且 2 跳出来的论文相关性开始明显稀释

💡 为什么不用 httpx.AsyncClient 复用连接池
    当前每个 _fetch_one 内部都 `async with httpx.AsyncClient()` 建一次新连接,
    严格说来有开销。但 citation_chase 总调用次数 = K × 2(references + citations),
    就 10 次级别,连接开销 < 50ms,简洁写法优先。若未来扩到 K=20+,
    建议改成传入共享 client。

------------------------------------------------------------
"""

from __future__ import annotations

import asyncio

import httpx
from tenacity import AsyncRetrying, stop_after_attempt, wait_exponential

from co_scientist.state import Paper
from co_scientist.utils import logger

S2_API = "https://api.semanticscholar.org/graph/v1/paper/{paper_id}/{direction}"
FIELDS = "title,abstract,authors,year,venue,externalIds,url,citationCount"


def _semantic_scholar_lookup_id(paper: Paper) -> str:
    """
    转成 Semantic Scholar paper endpoint 支持的 ID。

    arXiv 源的 paper.id 是 2507.13374v1 这类本地 ID,直接拼到 S2 URL 会 404。
    S2 支持带前缀的外部 ID,所以优先用 DOI:/ARXIV: 形式查询。
    """
    doi = (paper.get("doi") or "").strip()
    if doi:
        return f"DOI:{doi}"

    arxiv_id = (paper.get("arxiv_id") or "").strip()
    if arxiv_id:
        return f"ARXIV:{arxiv_id}"

    paper_id = (paper.get("id") or "").strip()
    if paper.get("source") == "semantic_scholar" and paper_id:
        return paper_id
    if paper.get("source", "").startswith("s2_") and paper_id:
        return paper_id
    return ""


async def _fetch_one(
    paper_id: str, direction: str, limit: int
) -> list[Paper]:
    """
    direction: 'references' 或 'citations'。

    ▍S2 API 的坑:wrapper 字段名不一致
        同样是引用关系,/references 端点返回 {"citedPaper": {...}},
        /citations 端点返回 {"citingPaper": {...}}。
        代码里用 wrapper.get("citedPaper") or wrapper.get("citingPaper") 统一处理,
        不要为此拆成两个函数,维护成本更高。

    ▍失败返回 []
        单篇论文的引用查询失败不应该影响其他论文。上层 asyncio.gather
        收到空列表会自然合并跳过。
    """
    url = S2_API.format(paper_id=paper_id, direction=direction)
    params = {"limit": limit, "fields": FIELDS}
    try:
        async for attempt in AsyncRetrying(
            stop=stop_after_attempt(3),
            wait=wait_exponential(multiplier=1, min=2, max=8),
            reraise=True,
        ):
            with attempt:
                async with httpx.AsyncClient(timeout=30.0) as client:
                    resp = await client.get(url, params=params)
                    resp.raise_for_status()
                    data = resp.json()
    except Exception as e:
        logger.warning("[chase] {} {} 失败: {}", direction, paper_id, e)
        return []

    out: list[Paper] = []
    for wrapper in data.get("data", []):
        # references 端点返回 {"citedPaper": {...}}, citations 端点返回 {"citingPaper": {...}}
        item = wrapper.get("citedPaper") or wrapper.get("citingPaper") or wrapper
        if not item:
            continue
        ext = item.get("externalIds") or {}
        out.append(
            Paper(
                id=item.get("paperId", "") or "",
                title=(item.get("title") or "").strip(),
                abstract=(item.get("abstract") or "").strip(),
                authors=[a.get("name", "") for a in (item.get("authors") or [])],
                year=item.get("year") or 0,
                venue=item.get("venue") or "",
                arxiv_id=ext.get("ArXiv", "") or "",
                doi=ext.get("DOI", "") or "",
                url=item.get("url") or "",
                source=f"s2_{direction}",
                cited_by_count=item.get("citationCount") or 0,
                score=0.0,
            )
        )
    return out


async def chase_citations(
    seed_papers: list[Paper],
    top_k: int = 5,
    per_paper_limit: int = 10,
) -> list[Paper]:
    """
    对 top-K 核心论文并行拉取 references + citations,合并返回。

    Args:
        seed_papers: 已排序的候选论文(通常是 RRF 后的结果)
        top_k: 只对前 K 篇做扩展
        per_paper_limit: 每篇论文最多取多少条引用/被引

    ▍gather 并发数
        一个 K=5 的典型场景会 gather 10 个协程(5 篇 × 2 方向),
        对 S2 免费 tier(~1 req/s)来说偏激进,可能触发 429。
        本项目依赖 _fetch_one 内部的 tenacity 退避做补救,大部分情况下跑得过。
        要稳起见可以改成 asyncio.Semaphore 控制并发,留作练手。

    ▍返回扁平 list 而不是 dict-by-seed
        下游(retriever.py)会把这些论文和 RRF 结果做二次 RRF,不关心
        每条论文从哪个种子扩来。扁平化最省事。
    """
    # 只处理能被 Semantic Scholar paper endpoint 识别的论文
    seeds = [
        (p, lookup_id)
        for p in seed_papers[:top_k]
        if (lookup_id := _semantic_scholar_lookup_id(p))
    ]
    if not seeds:
        return []

    tasks = []
    for _paper, lookup_id in seeds:
        tasks.append(_fetch_one(lookup_id, "references", per_paper_limit))
        tasks.append(_fetch_one(lookup_id, "citations", per_paper_limit))

    results: list[list[Paper]] = await asyncio.gather(*tasks, return_exceptions=False)
    flat: list[Paper] = [paper for batch in results for paper in batch]
    logger.info("[chase] 从 {} 篇种子扩展出 {} 篇相关", len(seeds), len(flat))
    return flat
