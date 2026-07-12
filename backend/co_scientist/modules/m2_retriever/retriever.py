"""
============================================================
 模块 2:主流程(m2_retriever/retriever.py)
============================================================

🎓 教学目标
    把上面所有子模块(Query Rewriting、3 个数据源、RRF、时间衰减、
    Citation Chasing)串起来,形成完整的文献检索流水线。

    这是整个模块的"门面",也是 LangGraph 图节点的入口。

📌 流水线
    1. Query Rewriting:1 个原问题 → 4-6 条英文 query
    2. 并行检索:每条 query 同时查 arXiv + Semantic Scholar + OpenAlex
    3. RRF 融合 + 去重
    4. 时间衰减加权
    5. Citation Chasing:对 top-5 再扩展引用
    6. 再做一次 RRF(把扩展结果并入)
    7. 截断到 top_k 返回

🔥 性能提示
    假设 5 条 query × 3 个源 = 15 次并发请求,每次 ~2 秒,
    串行要 30 秒,并行 < 3 秒。asyncio.gather 的威力。

------------------------------------------------------------
"""

from __future__ import annotations

import asyncio
from typing import Any

from co_scientist.modules.m2_retriever.citation_chase import chase_citations
from co_scientist.modules.m2_retriever.embedding_rerank import rerank_by_embedding
from co_scientist.modules.m2_retriever.fusion import (
    apply_time_decay,
    reciprocal_rank_fusion,
)
from co_scientist.modules.m2_retriever.query_rewriter import rewrite_queries
# ------------------------------------------------------------
# 检索源的 import:按 settings.USE_MCP 开关二选一
# ------------------------------------------------------------
# 两种模式对外契约完全一致(search_xxx(query, max_results) → list[Paper]),
# 所以这里通过 feature flag 决定导入哪一份:
#   USE_MCP=False(默认):直调 sources/*.py 的异步函数,进程内完成,简单快
#   USE_MCP=True         :走 mcp_client.py,启动 MCP Server 子进程通信
# 下游 hybrid_search_async 不需要做任何 if-else 分支 —— 这是 feature flag
# 最干净的用法:入口分流,运行路径透明。
# ------------------------------------------------------------
from co_scientist.config import settings

if settings.USE_MCP:
    from co_scientist.modules.m2_retriever.mcp_client import (
        search_arxiv,
        search_openalex,
        search_semantic_scholar,
    )
else:
    from co_scientist.modules.m2_retriever.sources import (
        search_arxiv,
        search_openalex,
        search_semantic_scholar,
    )

from co_scientist.state import Paper, ResearchState
from co_scientist.utils import logger


async def hybrid_search_async(
    question: str,
    pico: dict[str, Any] | None = None,
    *,
    top_k: int = 30,
    per_source_limit: int = 15,
    enable_citation_chase: bool = True,
    enable_embedding_rerank: bool = True,
) -> tuple[list[Paper], list[str]]:
    """
    执行完整的多源检索 pipeline(异步版)。

    Returns:
        (论文列表, 使用的 rewritten queries)
    """
    # 运行时打一条日志,让读者/面试官能直接从日志看出当前走的是哪种模式
    # 别的节点都是 LLM 调用,这条 "MCP mode" 的日志
    # 一眼就能识别"哦,他接了 MCP",有时候比文档还直观
    mode = "MCP" if settings.USE_MCP else "direct"
    logger.info("[M2] 检索模式: {}(通过 settings.USE_MCP 切换)", mode)

    # ---- Step 1: Query Rewriting ----
    queries = rewrite_queries(question, pico or {}, n=5)
    if not queries:
        queries = [question]
    logger.info("[M2] 改写得到 {} 条 query", len(queries))

    # ---- Step 2: 并行检索 ----
    # 每条 query × 3 个源 = len(queries) * 3 个并发任务
    # 用 return_exceptions=True 让单个源失败不影响整体(符合失败兜底原则)
    tasks = []
    for q in queries:
        tasks.append(search_arxiv(q, max_results=per_source_limit))
        tasks.append(search_semantic_scholar(q, max_results=per_source_limit))
        tasks.append(search_openalex(q, max_results=per_source_limit))

    raw_results = await asyncio.gather(*tasks, return_exceptions=True)

    # 剔除异常,只保留成功的 list
    source_lists: list[list[Paper]] = []
    for r in raw_results:
        if isinstance(r, Exception):
            logger.warning("[M2] 某源失败: {}", r)
            continue
        source_lists.append(r)  # type: ignore[arg-type]

    if not source_lists:
        logger.error("[M2] 所有检索源失败!")
        return [], queries

    # ---- Step 3: RRF 融合去重 ----
    merged = reciprocal_rank_fusion(source_lists, k=60)

    # ---- Step 4: 时间衰减 ----
    merged = apply_time_decay(merged, half_life_years=3.0)

    # ---- Step 5: Citation Chasing(可选) ----
    if enable_citation_chase and merged:
        try:
            chased = await chase_citations(merged, top_k=5, per_paper_limit=10)
            if chased:
                # 把扩展结果作为一个额外的"源",再 RRF 一次
                merged = reciprocal_rank_fusion([merged, chased], k=60)
                merged = apply_time_decay(merged, half_life_years=3.0)
        except Exception as e:
            logger.warning("[M2] Citation chase 失败,跳过: {}", e)

    # ---- Step 5.5: Embedding 语义重排(可选)----
    # 🎓 教学点:为什么 rerank 放在 citation_chase 之后而不是之前
    #   citation_chase 会引入一批"被 top-5 引用的相关论文",它们没参与
    #   原始关键词检索,rank 可能不太准。先做 rerank 会把这些引用扩展的
    #   结果压下去;放最后,能让引用扩展的语义相关论文也被语义分数公平评估。
    # 🎓 "为什么不完全替代 RRF":见 embedding_rerank.py 开头的教学注释
    if enable_embedding_rerank and merged:
        # 优先用精炼后的问题做 query 语义向量(如果有 PICO 里的)
        rerank_query = (
            (pico or {}).get("refined_question") or question
        )
        merged = rerank_by_embedding(
            rerank_query,
            merged,
            alpha=0.5,        # 关键词分数与语义相似度各占一半
            top_k=top_k * 2,  # 对 top_k 的 2 倍做 rerank,不 rerank 尾部省钱
        )

    # ---- Step 6: 截断 ----
    return merged[:top_k], queries


def retrieve_node(state: ResearchState) -> ResearchState:
    """
    LangGraph 节点函数(同步包装)。

    LangGraph 的节点默认是同步调用的,但我们内部是 asyncio。
    用 asyncio.run 包装一下。如果上层已经在事件循环里,
    可以换成 asyncio.get_event_loop().run_until_complete。

    这里为什么不把整个节点直接改成 async def?
      因为这个项目的图编排、CLI、测试入口目前都按同步风格组织。
      如果单独把节点改成 async,上层每一层都要跟着感知异步边界:
      build_graph、invoke、测试桩、异常处理都会更绕。教学版先把异步复杂度
      收敛在模块内部,让读者把注意力放在"并发检索流水线"本身。

    这个函数本质上做了两层转换:
      1. 把 LangGraph 传进来的 state,翻译成 hybrid_search_async 所需参数
      2. 再把异步检索结果翻译回 state patch(dict)
    所以它很像一个"适配层"。真正复杂的检索逻辑在 hybrid_search_async,
    节点函数只负责接图和吐图。

    为什么这里对 RuntimeError 要单独兜底?
      因为在 Jupyter / FastAPI / 某些测试环境里,外层可能已经有事件循环在跑,
      这时直接 asyncio.run(...) 会报错。很多初学者第一次把 async 代码接到 Web
      服务里时,最常见的坑就是这里。教学版显式保留这个分支,是为了告诉你:
      "异步代码能跑" 和 "异步代码能被不同宿主环境稳定调用" 是两回事。
    """
    if state.get("papers"):
        logger.info("[M2] 已有 {} 篇论文,跳过检索", len(state["papers"]))
        return {}

    question = (
        state.get("pico", {}).get("refined_question") or state.get("raw_question") or ""
    )
    if not question:
        logger.error("[M2] 没有可用问题")
        return {"error_log": ["[M2] 缺少研究问题"]}

    try:
        papers, queries = asyncio.run(
            hybrid_search_async(question, state.get("pico", {}))
        )
    except RuntimeError:
        # 如果已经在事件循环里(如 Jupyter、FastAPI),用 nest_asyncio 兜底
        # 这里简单处理:开新线程跑
        import concurrent.futures

        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            papers, queries = pool.submit(
                lambda: asyncio.run(hybrid_search_async(question, state.get("pico", {})))
            ).result()

    logger.info("[M2] ✅ 检索完成: {} 篇论文", len(papers))
    return {"papers": papers, "rewritten_queries": queries}
