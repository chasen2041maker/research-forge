"""
============================================================
 模块 7:引用校验(m7_writer/citation_verify.py)
============================================================

🎓 教学目标
    学术幻觉最严重区:LLM 编造不存在的引用。
    本模块给每条引用做双重校验:
      1. arxiv_id 必须能在 arXiv API 反查到(verify_arxiv)
      2. 标题必须在原始检索池里出现过(verify_in_pool)
    两关任一通过即视为可信;都不过 → 上层(writer.py)打警告或删除。

💡 为什么要做这件事
    不做的话,LLM 写 Related Work 时有 5-15% 概率编出"看起来像真的"引用
    (标题格式对、作者名合理、年份合理,但论文根本不存在)。这种幻觉极难人工
    发现,一旦被评审抓到直接灭顶。代价是对每条引用多打一次 arXiv API,
    主流程耗时增加 <5%,绝对划算。

📌 策略:为什么只覆盖 arXiv 而不做通用 DOI 校验
    - arXiv API 公开免费、无 key、反查单篇 <100ms → 可批量跑
    - DOI 反查要走 CrossRef 或 Semantic Scholar,前者返回慢、后者限流严
    - 项目的主力检索源里 arXiv 占 60%+ 命中率,覆盖 80% 场景已够 MVP
    - 想扩展只需加一个 verify_doi() 并在 writer 里并联调用,不改调用协议

💡 为什么 verify_in_pool 用 Jaccard 而不是语义相似
    在"已经知道检索池"的前提下,标题几乎是确定性匹配:
      - 子串互相包含:前缀副标题差异
      - 词集合 Jaccard ≥ 0.8:大小写/标点/一两个词增减
    两步足以召回 95%+ 真实引用。上 embedding 会引入误判(如 "BERT" vs "BART"
    余弦相似度偏高),在"校验"场景里我们宁愿误杀少数也不要放过幻觉。

------------------------------------------------------------
"""

from __future__ import annotations

import httpx

from co_scientist.state import Paper
from co_scientist.utils import logger


async def verify_arxiv(arxiv_id: str) -> bool:
    """
    快速查 arXiv 这个 ID 是否真实存在。

    ▍判定方式
        直接看响应文本里有没有 <entry> 标签。有就是存在,没有就是假 ID。
        不解析 XML,是因为一条校验只看存在性,正则/子串比 XML 解析快一个量级。

    ▍为什么任何异常都返回 False
        写作阶段出现的引用 ID 来源不可信,网络 timeout / 被限流 / id 格式非法
        统统视作"校验不通过"。下游对待 False 的逻辑是"标警告或删除",
        这里宁可误杀也不放过。
    """
    if not arxiv_id:
        return False
    url = f"http://export.arxiv.org/api/query?id_list={arxiv_id}&max_results=1"
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            return "<entry>" in resp.text
    except Exception as e:
        logger.warning("[verify] arXiv 查 {} 失败: {}", arxiv_id, e)
        return False


def verify_in_pool(paper: Paper, pool: list[Paper], threshold: float = 0.8) -> bool:
    """
    标题是否在检索池里(简单子串+Jaccard)。

    ▍两步判定,成本由低到高
        1. 子串包含:处理"主标题 vs 主标题+副标题"这种常见差异,几乎免费
        2. Jaccard:两边词集合的交并比,阈值默认 0.8
           - 选 Jaccard 而不是 cosine on counts:标题很短(10-20 词),
             词频信息稀薄,退化成集合交并更干净
           - 0.8 是经验值:低于 0.7 会把同主题但不同论文也当同一篇;
             高于 0.9 会漏掉大小写 / 连字符 / 缩略词差异

    ▍为什么不 return 第一个命中就早退出
        写了 return True 就早退出啊(看第 53 行 if title in pt 或 pt in title)。
        只是把最贵的 Jaccard 放到最后,子串命中就跳过集合计算。
    """
    title = (paper.get("title") or "").lower()
    if not title:
        return False
    for p in pool:
        pt = (p.get("title") or "").lower()
        if not pt:
            continue
        if title in pt or pt in title:
            return True
        ts = set(title.split())
        ps = set(pt.split())
        if ts and ps:
            sim = len(ts & ps) / len(ts | ps)
            if sim >= threshold:
                return True
    return False
