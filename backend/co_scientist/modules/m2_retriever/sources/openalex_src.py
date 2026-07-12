"""
============================================================
 模块 2:数据源 - OpenAlex(m2_retriever/sources/openalex_src.py)
============================================================

🎓 教学目标
    OpenAlex 是 Microsoft Academic Graph 关停后的接班人,完全免费且无强限流。
    覆盖最广(2 亿+ 论文),元数据丰富,适合做主力数据源。

💡 为什么它是本项目的"主力源"
    - 无 key 即可用,单日限额宽(10 万请求)
    - 字段最全:DOI + arXiv ID + cited_by_count + venue 一网打尽
    - RRF 融合时能"补全"其他源缺的字段(见 fusion.py 的补全逻辑)

📌 实用提示
    OpenAlex 推荐在 User-Agent 里带上你的邮箱(进入 polite pool,限流更宽松)。
    具体是:服务器看到带邮箱的 UA → 给你分配更高限流配额,且优先调度。
    这是 OpenAlex 的"贿赂机制",不带邮箱也能用,只是峰值容易被 throttle。

💡 特殊之处:abstract_inverted_index
    OpenAlex 为省带宽把摘要存成倒排索引(见文件末尾 _decode_inverted_abstract),
    其他两个源直接给纯文本摘要。所以这里多了一步"还原"。

------------------------------------------------------------
"""

from __future__ import annotations

import httpx
from tenacity import AsyncRetrying, retry_if_exception_type, stop_after_attempt, wait_exponential

from co_scientist.state import Paper
from co_scientist.utils import logger

API_URL = "https://api.openalex.org/works"


async def search_openalex(query: str, max_results: int = 20) -> list[Paper]:
    """异步查 OpenAlex。"""
    params = {
        "search": query,
        "per-page": max_results,
        "select": (
            "id,title,abstract_inverted_index,authorships,publication_year,"
            "primary_location,doi,cited_by_count,ids"
        ),
    }
    headers = {"User-Agent": "AI-Co-Scientist/0.1 (mailto:co-scientist@example.com)"}

    async for attempt in AsyncRetrying(
        retry=retry_if_exception_type((httpx.HTTPStatusError, httpx.TimeoutException)),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        reraise=True,
    ):
        with attempt:
            try:
                async with httpx.AsyncClient(timeout=30.0, headers=headers) as client:
                    resp = await client.get(API_URL, params=params)
                    resp.raise_for_status()
                    data = resp.json()
            except Exception as e:
                logger.error("[openalex] 查询失败 q={}: {}", query, e)
                return []

            results = data.get("results", []) or []
            papers: list[Paper] = []
            for item in results:
                # OpenAlex 把 abstract 存成倒排索引以省空间,需要还原
                abstract = _decode_inverted_abstract(
                    item.get("abstract_inverted_index") or {}
                )
                ids = item.get("ids") or {}
                arxiv_url = ids.get("arxiv") or ""
                arxiv_id = arxiv_url.rsplit("/", 1)[-1] if arxiv_url else ""
                authors = [
                    (a.get("author") or {}).get("display_name", "")
                    for a in (item.get("authorships") or [])
                ]
                primary_location = item.get("primary_location") or {}
                venue = (
                    (primary_location.get("source") or {}).get("display_name", "")
                    or ""
                )

                papers.append(
                    Paper(
                        id=item.get("id", "").split("/")[-1],
                        title=(item.get("title") or "").strip(),
                        abstract=abstract,
                        authors=authors,
                        year=item.get("publication_year") or 0,
                        venue=venue,
                        arxiv_id=arxiv_id,
                        doi=(item.get("doi") or "").replace("https://doi.org/", ""),
                        url=item.get("id") or "",
                        source="openalex",
                        cited_by_count=item.get("cited_by_count") or 0,
                        score=0.0,
                    )
                )
            logger.info("[openalex] q={} → {} 篇", query[:40], len(papers))
            return papers

    return []


def _decode_inverted_abstract(inv: dict[str, list[int]]) -> str:
    """
    OpenAlex 用倒排索引存摘要(节省空间):
        {"the": [0, 5], "model": [1, 6]}
    意思是 "the" 出现在位置 0、5,"model" 出现在 1、6。
    我们要把它还原成正常文本。

    ▍为什么 OpenAlex 要这么存
        倒排索引在全文检索场景复用度极高 —— 他们内部也要做检索,存一份倒排
        既满足 API 输出又满足内部索引,省了一次转换。对我们的副作用就是
        多写这 10 行解码代码。

    ▍边界处理
        - inv 为空 → 返回空字符串(论文没摘要的情况)
        - max_pos=-1 → 摘要被删空,返回空串
        - 位置越界 → 丢弃该词(防守式编程,理论不会触发)
    """
    if not inv:
        return ""
    # 找出最大位置 → 数组长度
    max_pos = max((max(positions) for positions in inv.values()), default=-1)
    if max_pos < 0:
        return ""
    words: list[str] = [""] * (max_pos + 1)
    for word, positions in inv.items():
        for p in positions:
            if 0 <= p < len(words):
                words[p] = word
    return " ".join(w for w in words if w)
