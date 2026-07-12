"""
============================================================
 模块 2:数据源 - arXiv(m2_retriever/sources/arxiv_src.py)
============================================================

🎓 教学目标
    arXiv 提供官方 Atom XML API,Python 包 `arxiv` 帮我们封装好了。
    教学要点:
      - 异步包装(arxiv 包是同步的,我们用 to_thread 转异步)
      - 字段归一化(把 arxiv.Result 映射到本项目的 Paper TypedDict)
      - 错误处理 + 限流退避

💡 为什么三个数据源放三个独立文件而不是一个"统一 Source 基类"
    每个源的 SDK / 字段 / 限流策略都不一样,写基类的抽象成本大于收益。
    拆开后每个文件都能独立修改(比如 arXiv 新加 category 过滤),
    不会被基类签名束缚。统一接口只要"同签名的 async 函数 + 返回 list[Paper]"
    这一层契约,足够 retriever.py 用 asyncio.gather 调度。

💡 arXiv 的"坑"
    - 官方 API 没有速率限制说明,但实测 ~3 秒一次比较稳,再快会偶发 503
    - entry_id 带版本号(v1/v2),做 dedupe 时要去掉版本才能跟其他源对齐
    - authors 字段里的单位符号(如 ~)偶尔出现,靠下游展示时再清洗

------------------------------------------------------------
"""

from __future__ import annotations

import asyncio
from typing import Any

import arxiv
from tenacity import AsyncRetrying, stop_after_attempt, wait_exponential

from co_scientist.state import Paper
from co_scientist.utils import logger


def _to_paper(result: Any) -> Paper:
    """
    把 arxiv.Result 转成统一的 Paper 结构。

    ▍字段映射的两个细节
      - entry_id 形如 "http://arxiv.org/abs/2305.12345v1",
        id 保留版本("2305.12345v1")用于精确追踪,
        arxiv_id 去掉版本("2305.12345")用于跨源 dedupe。
      - cited_by_count 固定 0:arXiv 不提供这个字段,靠 OpenAlex / S2 补。
      - raw 不存原对象:arxiv.Result 带一堆 lazy 属性,存进 dict 会拖慢下游。
    """
    return Paper(
        id=result.entry_id.split("/")[-1],  # 例如 "2305.12345v1"
        title=result.title.strip().replace("\n", " "),
        abstract=(result.summary or "").strip().replace("\n", " "),
        authors=[a.name for a in result.authors],
        year=result.published.year if result.published else 0,
        venue="arXiv",
        arxiv_id=result.entry_id.split("/")[-1].split("v")[0],
        doi=result.doi or "",
        url=result.entry_id,
        source="arxiv",
        cited_by_count=0,  # arxiv 不提供引用数,留 0
        score=0.0,  # 由 RRF 阶段填充
        raw=None,  # 原对象太大,不存
    )


async def search_arxiv(query: str, max_results: int = 20) -> list[Paper]:
    """
    异步查 arXiv。

    💡 为什么要包成异步?
        - 我们要并行查多个数据源(arXiv / Semantic Scholar / OpenAlex)
        - asyncio.gather 要求每个调用是 awaitable
        - arxiv 包是同步的,用 asyncio.to_thread 把它丢到线程池

    ▍delay_seconds=1.0 的含义
        arxiv 包内部会在分页之间 sleep,防止触发 arXiv 的静默限流。
        单次 max_results=20 通常只有 1 页不会触发,但保留这个参数更保险。

    ▍重试策略:只做 3 次指数退避
        arXiv 限流是服务器侧软限制,重试间隔指数增长能快速让对方消气。
        超过 3 次仍失败大概率是服务本身挂了,再重试意义不大,让上层感知失败。
    """

    def _sync_search() -> list[Paper]:
        try:
            client = arxiv.Client(page_size=max_results, delay_seconds=1.0)
            search = arxiv.Search(
                query=query,
                max_results=max_results,
                sort_by=arxiv.SortCriterion.Relevance,
            )
            return [_to_paper(r) for r in client.results(search)]
        except Exception as e:  # 限流/网络错误等
            logger.error("[arxiv] 查询失败 q={}: {}", query, e)
            raise

    # 用 tenacity 做异步重试(arXiv 偶尔限流)
    async for attempt in AsyncRetrying(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        reraise=True,
    ):
        with attempt:
            papers = await asyncio.to_thread(_sync_search)
            logger.info("[arxiv] q={} → {} 篇", query[:40], len(papers))
            return papers

    return []  # 不会到这,仅为类型完整
