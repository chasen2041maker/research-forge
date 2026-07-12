"""
============================================================
 模块 2:Query Rewriting(m2_retriever/query_rewriter.py)
============================================================

🎓 教学目标
    用户输入"RAG 减少幻觉"这种短句直接喂给 arXiv 检索效果差。
    需要 LLM 先把它**改写成多个英文学术 query**,再并行检索。

    这是 RAG 进阶技巧之一:Query Rewriting。

💡 为什么直接用用户原问题查不行
    - 语种错位:学术论文 99% 是英文,用户输入中文或中英混合
    - 术语错位:"幻觉"→"hallucination"、"大模型"→"LLM/large language model"
    - 粒度错位:"减少"这种虚词对 BM25 无帮助,应换成具体做法名
    - 召回单一:一条 query 走 BM25 只能命中一个切面,多 query 能覆盖
      "hallucination reduction"、"retrieval augmented generation"、
      "factuality improvement" 等不同切入点

💡 为什么用 chat 模型而不是 reasoner
    query 改写是"格式变换"任务,不需要深度推理,用 chat 角色的 GPT 中转模型足够。
    reasoner 会过度思考、给出一串带 CoT 的长 query,反而降低检索命中率。

💡 为什么温度 0.7 而不是 0.2
    我们要的是"多条不重复的 query"。低温度会导致 LLM 反复输出相似改写
    ("RAG for hallucination" / "RAG hallucination reduction" / ...),
    适度升温能让它主动换切面。但不能到 1.0 以上,否则开始瞎编术语。

📌 工作流程
    输入:中文/口语化研究问题 + PICO
    输出:4-6 条精准英文 query

------------------------------------------------------------
"""

from __future__ import annotations

from co_scientist.llm import get_llm
from co_scientist.prompts.templates import (
    SYSTEM_M2_QUERY_REWRITE,
    USER_M2_QUERY_REWRITE,
)
from co_scientist.state import PICO
from co_scientist.utils import logger


def rewrite_queries(question: str, pico: PICO | None = None, n: int = 5) -> list[str]:
    """
    改写为多条英文检索 query。

    Args:
        question: 原始问题
        pico: PICO 信息(辅助提取关键术语)
        n: 期望生成的 query 数(LLM 不一定严格遵守)

    Returns:
        英文 query 列表

    ▍格式异常时的兜底
        如果 LLM 返回的结构不是 {"queries": [...]}(偶发),就直接把原问题
        作为唯一 query 返回。下游 retriever 拿到单条 query 仍然能检索,
        总比整条流程挂掉好。

    ▍为什么返回 list[str] 而不是带权重的 dict
        多条 query 的结果会走 RRF 融合,RRF 只用 rank 不看权重,给 query
        分配权重没意义。保持扁平 list 让上层调用最简单。
    """
    llm = get_llm("chat")  # 简单改写任务,用便宜模型
    result = llm.chat_json(
        messages=[
            {"role": "system", "content": SYSTEM_M2_QUERY_REWRITE},
            {
                "role": "user",
                "content": USER_M2_QUERY_REWRITE.format(
                    question=question,
                    pico=pico or {},
                ),
            },
        ],
        purpose="m2_query_rewrite",
        temperature=0.7,  # 适当高温,鼓励多样性
    )
    queries = result.get("queries", [])
    if not isinstance(queries, list):
        logger.warning("[M2] Query 改写返回格式异常: {}", result)
        return [question]
    # 截断到 n 条
    return [str(q).strip() for q in queries[:n] if str(q).strip()]
