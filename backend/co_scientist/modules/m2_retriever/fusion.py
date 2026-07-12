"""
============================================================
 模块 2:RRF 融合 + 去重 + 时间衰减(m2_retriever/fusion.py)
============================================================

🎓 教学目标(RAG 高级技巧核心)
    多源检索会得到 N 份不同排名的论文列表,怎么"合并"?
    直接用分数加权不行,因为不同源的分数不可比。

    标准做法:**Reciprocal Rank Fusion(RRF,倒数排名融合)**
        score(d) = Σ_i  1 / (k + rank_i(d))
    其中 k 是平滑常数(论文推荐 60),rank_i 是论文 d 在第 i 个源的排名。

    为什么有效?
      - 对每个源,只用"排名"而不用"原始分数",消除量纲差异
      - 1/(k+rank) 是凸递减,top 几名分数差别大,尾部差别小
      - 多个源都排前的论文,总分会高 → 自动过滤噪音

💡 为什么 k=60 而不是 10 / 100
    k 是"压平头部差距"的旋钮:
      - k 越大,rank=1 和 rank=5 的分差越小(趋同),尾部信号相对更重要 → 利于召回
      - k 越小,头部赢家通吃(rank=1 几乎等于答案),尾部噪音被抹掉 → 利于精确
    TREC 的 RRF 原论文(Cormack et al., 2009)用 k=60 作为经验最优,
    多数后续工作沿用这个值。改动它之前务必用评测集做 ablation。

💡 为什么不用"加权平均分数"这种"看起来更科学"的方案
    不同源的分数量纲完全不同:arXiv 的 BM25、S2 的 relevance、OpenAlex 的
    BM25-ish 既不同阈值也不同分布。强行归一化到 [0,1] 反而会把噪音放大。
    "只用排名"等价于承认"我不知道绝对相关度,但你自己内部排序是可信的",
    这比假设一个伪科学分数更稳。

    再加:
      - 时间衰减:近 3 年论文额外加权(研究热点往往是新的)
      - 去重:同一论文在不同源出现,合并且累加分数

------------------------------------------------------------
"""

from __future__ import annotations

import math
from datetime import datetime

from co_scientist.state import Paper
from co_scientist.utils import logger


def _dedupe_key(paper: Paper) -> str:
    """
    论文唯一性判断 key。优先用 doi > arxiv_id > 标题前 60 字符归一化。

    同一篇论文从不同源拿到,doi/arxiv_id 应该相同。
    最后兜底用标题,是因为有些会议论文没 doi。

    ▍为什么这个顺序(而不是反过来从标题开始)
        doi / arxiv_id 是结构化标识符,不同源之间严格一致(大小写归一后),
        误判概率接近 0。标题在实际数据里会出现:
          - "A-B-C" vs "A B C"(标点差异)
          - "ICLR 2024. Title" vs "Title"(会议前缀)
          - 预印本标题被期刊版改动
        所以只在"实在没结构化 id"时才退到标题兜底。

    ▍为什么标题要前 60 个 alphanum 字符而不是完整标题
        LLM/用户拼标题偶尔会把版本号、冒号后副标题加进去,导致同一篇文章
        两个版本 key 不同。前 60 个字符足以区分不同论文(碰撞率经验 <1%),
        又能吸收大部分尾部噪音。
    """
    if paper.get("doi"):
        return f"doi:{paper['doi'].lower()}"
    if paper.get("arxiv_id"):
        return f"arxiv:{paper['arxiv_id'].lower()}"
    title = (paper.get("title") or "").lower()
    # 去掉空白和标点后取前 60 字符
    normalized = "".join(c for c in title if c.isalnum())[:60]
    return f"title:{normalized}"


def reciprocal_rank_fusion(
    result_lists: list[list[Paper]],
    k: int = 60,
) -> list[Paper]:
    """
    RRF 融合多个来源的论文列表。

    Args:
        result_lists: 多个已排序的论文列表(每个内部按源的相关性降序)
        k: RRF 平滑常数,论文推荐 60

    Returns:
        去重合并后的论文列表,按 RRF 分数降序

    ▍关于"补齐字段"的小心思
        同一篇论文从不同源合并时,有些源字段更全(如 OpenAlex 的 cited_by_count
        比 arXiv 准),所以遇到已有条目时不简单丢弃新数据,而是"缺啥补啥"。
        这让后续排序 / 时间衰减 / 下游展示的字段完整度最高。
    """
    # key -> Paper(合并后的)
    merged: dict[str, Paper] = {}

    for rlist in result_lists:
        for rank, paper in enumerate(rlist, start=1):
            key = _dedupe_key(paper)
            contribution = 1.0 / (k + rank)

            if key in merged:
                # 已存在,累加 RRF 分数
                merged[key]["score"] = (merged[key].get("score") or 0.0) + contribution
                # 如果本条的字段更完整,选择性补充(如摘要更长、年份更新)
                if not merged[key].get("abstract") and paper.get("abstract"):
                    merged[key]["abstract"] = paper["abstract"]
                if not merged[key].get("cited_by_count") and paper.get("cited_by_count"):
                    merged[key]["cited_by_count"] = paper["cited_by_count"]
            else:
                # 新论文,深拷贝关键字段并设初始分数
                new_paper: Paper = dict(paper)  # type: ignore[assignment]
                new_paper["score"] = contribution
                merged[key] = new_paper

    results = list(merged.values())
    results.sort(key=lambda p: p.get("score", 0.0), reverse=True)
    logger.info("[rrf] {} 个源合并,去重后 {} 篇", len(result_lists), len(results))
    return results


def apply_time_decay(papers: list[Paper], half_life_years: float = 3.0) -> list[Paper]:
    """
    时间衰减:近 3 年论文加权。

    权重公式:w = exp(-λ * age),其中 λ = ln(2) / half_life
    等价于"每过 half_life 年,权重降为原来的一半"。

    我们把衰减乘到 score 上,让新论文排名往前。

    ▍为什么用指数衰减而不是硬截断(只保留近 3 年)
        硬截断会丢掉"老但重要"的经典论文(如 Transformer 原论文 2017),
        指数衰减只是相对降权,仍保留它们在检索池里,靠综合分排序。

    ▍half_life 选 3 年的理由
        AI/NLP 领域技术迭代快,3 年内论文占研究热点的 60%+(经验值)。
        做生物/物理综述时建议改成 10-15 年,让经典理论占优。
        想彻底关掉时间衰减传一个很大的值(比如 100)即可。

    ▍year 缺失怎么办
        arXiv 偶尔返回 year=0(早期论文),这里选择"不动 score",
        等价于默认它很旧但我们也拿不准。不直接 score=0 是避免误杀。
    """
    current_year = datetime.now().year
    lam = math.log(2) / half_life_years

    for p in papers:
        year = p.get("year") or 0
        if year <= 0:
            continue
        age = max(0, current_year - year)
        weight = math.exp(-lam * age)
        p["score"] = (p.get("score") or 0.0) * weight

    papers.sort(key=lambda p: p.get("score", 0.0), reverse=True)
    return papers
