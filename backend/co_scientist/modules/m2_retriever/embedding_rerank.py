"""
============================================================
 模块 2:Embedding 语义重排(m2_retriever/embedding_rerank.py)
============================================================

🎓 教学目标
    RRF 融合只看"rank 排名",完全不懂语义。举个例子:
      query = "RAG 减少幻觉"
      RRF 命中的 top-20 可能混进"RAG 图像生成"这种同名不同意的论文。
    这就需要一层**向量重排**:把 query 和每篇论文的 title+abstract
    都向量化,用余弦相似度再排一次,把"字面匹配但语义漂"的过滤掉。

📌 为什么放在 RRF 之后,而不是完全替代 RRF
    1. 纯向量召回对"OOV/新术语"很弱(比如新模型名),BM25+关键词更准
    2. 但向量排序擅长"语义过滤",适合做二阶段 rerank
    这种"关键词粗召回 + 向量精排"就是工业界 RAG 的主流架构
    (Semantic Scholar、Perplexity 都这么做)。

💡 降级策略
    - 任何一步失败(embedding API 挂、向量维度对不上、空输入)
      都回退到原始 RRF 顺序,不阻塞主流程
    - 降级时打 logger.warning,便于排查"为什么今天 rerank 没起作用"

------------------------------------------------------------
"""

from __future__ import annotations

import math
from typing import Protocol, runtime_checkable

from co_scientist.llm import get_llm
from co_scientist.state import Paper
from co_scientist.utils import logger


@runtime_checkable
class EmbeddingClient(Protocol):
    def embed(
        self,
        texts: list[str],
        *,
        model: str | None = None,
        purpose: str = "embed",
    ) -> list[list[float]]:
        ...


def _cosine(v1: list[float], v2: list[float]) -> float:
    """
    标准余弦相似度。
    不用 numpy 是为了减少依赖 —— 重排向量维度一般 1024/1536,纯 Python
    足够快(一次 rerank 常量级开销远小于 LLM 调用本身)。
    """
    dot = sum(a * b for a, b in zip(v1, v2))
    n1 = math.sqrt(sum(a * a for a in v1))
    n2 = math.sqrt(sum(b * b for b in v2))
    if n1 == 0 or n2 == 0:
        return 0.0
    return dot / (n1 * n2)


def _doc_text(paper: Paper) -> str:
    """
    把论文浓缩成一段可向量化的文本。
    只拼 title + abstract 前 500 字,超过 embedding 上限风险小,
    也避免长摘要里的无关段落稀释主题信号。
    """
    title = paper.get("title", "") or ""
    abstract = (paper.get("abstract", "") or "")[:500]
    return f"{title}\n{abstract}".strip()


def rerank_by_embedding(
    query: str,
    papers: list[Paper],
    *,
    alpha: float = 0.5,
    top_k: int | None = None,
) -> list[Paper]:
    """
    用 embedding 余弦相似度对候选重排。

    Args:
        query: 用户/精炼后的研究问题
        papers: RRF 融合后的候选列表(带 'score' 字段)
        alpha: 原 RRF 分数 和 余弦分数的加权。
               alpha=1 → 完全用 RRF(等同不 rerank)
               alpha=0 → 完全用语义相似度
               默认 0.5:两者各半,兼顾关键词命中和语义相关。
        top_k: 只对前 top_k 条做 rerank(省 embedding 调用)。None=全部。

    Returns:
        重排后的新列表,每个 Paper 新增 'rerank_score' 字段(方便排查)

    ▍为什么不直接替换 score 字段
        保留原始 RRF 分数便于事后对比"rerank 到底改变了多少顺序"。
        在 _doc_text 失败或 embedding 挂掉时也好快速回退。
    """
    if not papers or not query.strip():
        return papers

    # 限制 rerank 范围,避免 100+ 篇都调 embedding。
    candidates = papers if top_k is None else papers[:top_k]
    tail = papers[len(candidates):]  # top_k 之后的原样保留

    # 客户端拿到一个就够用;这里用 chat 角色的 GPT relay 客户端,
    # 它内部的 SDK 已经建好连接池,embedding 走同一个 OpenAI 兼容端点。
    llm = get_llm("chat")
    if not isinstance(llm, EmbeddingClient):
        # 万一 chat 角色换成了不支持 embedding 的客户端,不改变原顺序。
        logger.warning("[M2-rerank] 当前 chat 客户端无 embedding 能力,跳过重排")
        return papers

    texts = [_doc_text(p) for p in candidates]
    if not all(texts):
        logger.warning("[M2-rerank] 候选中存在空文本,跳过重排")
        return papers

    try:
        # 一次批量请求:query + N 篇论文。API 返回顺序与 input 顺序一致。
        all_vecs = llm.embed([query] + texts, purpose="m2_embed_rerank")
    except Exception as e:
        # embedding 层的任何失败都降级,主流程照常走
        logger.warning("[M2-rerank] embedding 失败,回退到 RRF 原序: {}", e)
        return papers

    if len(all_vecs) != len(texts) + 1:
        logger.warning("[M2-rerank] 向量数量不匹配,跳过重排")
        return papers

    q_vec = all_vecs[0]
    doc_vecs = all_vecs[1:]

    # ---- 归一化 RRF 原分到 [0, 1],和余弦可比较 ----
    scores = [p.get("score", 0.0) for p in candidates]
    s_max = max(scores) if scores else 1.0
    s_max = s_max or 1.0  # 防 0

    reranked: list[Paper] = []
    for paper, dv, raw_score in zip(candidates, doc_vecs, scores):
        cos = _cosine(q_vec, dv)
        # 加权融合,new_score 同时塞回 rerank_score 便于调试
        new_score = alpha * (raw_score / s_max) + (1 - alpha) * cos
        # Paper 是 TypedDict,直接赋值即可(不修改原对象,拷一份)
        p2 = dict(paper)
        p2["score"] = new_score
        p2["rerank_score"] = cos  # type: ignore[typeddict-unknown-key]
        reranked.append(p2)  # type: ignore[arg-type]

    reranked.sort(key=lambda p: p.get("score", 0.0), reverse=True)
    logger.info("[M2-rerank] 对 top-{} 做向量重排完成", len(reranked))
    return reranked + tail
