"""
============================================================
 模块 2:数据源 - Semantic Scholar(m2_retriever/sources/semantic_src.py)
============================================================

🎓 教学目标
    Semantic Scholar Graph API 是免费的学术 API,提供:
      - 论文元数据
      - 引用图谱(被引/引用关系)
      - 作者信息
    我们直接用 httpx 异步访问 REST 端点。

💡 为什么 S2 相比 arXiv / OpenAlex 仍值得加进来
    - 只有它提供结构化引用图(/references, /citations),citation_chase 重度依赖
    - cited_by_count 精确,作为 RRF 后的排序信号很可靠
    - 覆盖会议论文(NeurIPS/ACL/...)比 arXiv 全面

📌 限流注意
    免费 tier 对未登录请求限流较严(约 1 req/s)。
    如果超额可申请 API key(免费),配置在请求头 x-api-key。

💡 本文件和 arxiv_src.py 的 async 写法差异
    arXiv 用的是 arxiv 包(同步) + to_thread 桥接;
    S2 用 httpx.AsyncClient 原生异步。原生异步更轻量,能真正在 gather 里做
    IO 并发;to_thread 走线程池,并发数受 Python 默认线程池大小限制。
    选 httpx 是因为 S2 没有像 arxiv 包那样的官方 SDK。

------------------------------------------------------------
"""

from __future__ import annotations

import httpx
from tenacity import AsyncRetrying, retry_if_exception_type, stop_after_attempt, wait_exponential

from co_scientist.state import Paper
from co_scientist.utils import logger

API_URL = "https://api.semanticscholar.org/graph/v1/paper/search"

# 想要的字段。逗号分隔,API 只返回我们要的字段省流量
FIELDS = "title,abstract,authors,year,venue,externalIds,url,citationCount"


async def search_semantic_scholar(query: str, max_results: int = 20) -> list[Paper]:
    """
    异步查 Semantic Scholar。

    ▍错误处理的分层
        - 429(限流): 让 tenacity 继续退避重试
        - 其他 HTTP 错(403/500): 直接返回空列表(retrieval 不因单源挂而全崩)
        - 网络异常/超时:同上,返回空

    ▍为什么 fields 参数精确列出想要的字段
        S2 默认返回一堆不用的字段(如 authors 的 hIndex、paperId 列表),
        响应体能从 50KB 降到 10KB,批量查询时节省带宽且更快。
    """
    params = {"query": query, "limit": max_results, "fields": FIELDS}

    async for attempt in AsyncRetrying(
        retry=retry_if_exception_type((httpx.HTTPStatusError, httpx.TimeoutException)),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        reraise=True,
    ):
        with attempt:
            try:
                async with httpx.AsyncClient(timeout=30.0) as client:
                    resp = await client.get(API_URL, params=params)
                    resp.raise_for_status()
                    data = resp.json()
            except httpx.HTTPStatusError as e:
                # 429 限流时让 tenacity 重试
                if e.response.status_code == 429:
                    logger.warning("[s2] 限流,退避重试")
                    raise
                logger.error("[s2] HTTP 错误 {}: {}", e.response.status_code, e)
                return []
            except Exception as e:
                logger.error("[s2] 查询失败 q={}: {}", query, e)
                return []

            results = data.get("data", []) or []
            papers: list[Paper] = []
            for item in results:
                ext = item.get("externalIds") or {}
                papers.append(
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
                        source="semantic_scholar",
                        cited_by_count=item.get("citationCount") or 0,
                        score=0.0,
                    )
                )
            logger.info("[s2] q={} → {} 篇", query[:40], len(papers))
            return papers

    return []
