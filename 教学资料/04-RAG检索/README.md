# 04 - RAG 高级检索

> 这是项目最有学习价值的一章。涵盖 Query Rewriting、多源并发、RRF 融合、时间衰减、Citation Chasing。
> 学完后,你的 RAG 不再是"调一次 vector search"的初级水平。

---

## 4.1 RAG 进化路线

```
Level 1: 单库向量检索       ← 大多数教程到这就停
Level 2: + Query Rewriting  ← 把用户问题改写成多种检索 query
Level 3: + 多源融合(RRF)   ← 多个检索源并行 + 排名融合
Level 4: + 重排(Re-ranking) ← Cross-Encoder 二次排序
Level 5: + Citation Chasing ← 沿引用图谱扩展
Level 6: + GraphRAG         ← 向量 + 图路径混合检索
```

本项目实现到 Level 5,留 Level 6 作扩展。

---

## 4.2 Query Rewriting:最被低估的技巧

### 痛点
用户输入"RAG 减少幻觉",直接喂 arXiv 检索,召回率很低:
- 中文 vs 英文论文不匹配
- 缺学术术语(如 "retrieval-augmented generation")
- 没覆盖同义表达

### 解决:LLM 改写
```python
SYSTEM = """你是学术检索专家,把研究问题改写成多个英文 query。
- 用学术术语
- 覆盖同义词
- 长度 5-12 词
返回 JSON: {"queries": [...]}"""

queries = llm.chat_json([
    {"role": "system", "content": SYSTEM},
    {"role": "user", "content": "RAG 减少 LLM 幻觉"},
])
# 输出例如:
# - "retrieval augmented generation hallucination reduction"
# - "knowledge grounding large language models"
# - "RAG factuality improvement"
# - "retrieval-based mitigation LLM hallucination"
# - "evidence-based language model question answering"
```

### 数量怎么定?
- 太少(1-2):覆盖不全
- 太多(10+):浪费 API 配额
- **推荐 4-6 条**

### 高级版:HyDE(Hypothetical Document Embeddings)
让 LLM 直接写一段"假设的答案",用这段答案做 embedding 检索。
本项目没用,但你可以扩展。

📌 **项目对应**:`backend/co_scientist/modules/m2_retriever/query_rewriter.py`

---

## 4.3 多数据源:三大学术 API

| API | 优势 | 缺点 |
|-----|------|------|
| **arXiv** | 预印本最新、官方 SDK | 限流严、只有 STEM、无引用数 |
| **Semantic Scholar** | 引用图谱、覆盖广 | 免费 tier 限流(~1 req/s) |
| **OpenAlex** | 完全免费、无强限流、2 亿+ 论文 | 部分元数据缺 |

### 选用建议
- **MVP**:OpenAlex 单源已够用
- **进阶**:OpenAlex + arXiv 互补
- **完整**:三个全上 + RRF

### 调用模式

#### arXiv(同步包装异步)
```python
import arxiv
import asyncio

async def search_arxiv(query: str, max_results: int = 20):
    def _sync():
        client = arxiv.Client(page_size=max_results, delay_seconds=1.0)
        search = arxiv.Search(query=query, max_results=max_results)
        return list(client.results(search))
    return await asyncio.to_thread(_sync)  # 同步函数丢线程池
```

#### Semantic Scholar(httpx 异步)
```python
async with httpx.AsyncClient(timeout=30.0) as client:
    resp = await client.get(
        "https://api.semanticscholar.org/graph/v1/paper/search",
        params={"query": q, "limit": 20, "fields": "title,abstract,..."}
    )
    return resp.json()["data"]
```

#### OpenAlex(注意 polite pool)
```python
headers = {"User-Agent": "MyApp/1.0 (mailto:you@example.com)"}
# 加邮箱进 polite pool,限流更宽松
```

### OpenAlex 倒排摘要还原
OpenAlex 为省空间用倒排索引存摘要:
```json
{"the": [0, 5], "model": [1, 6]}
// 含义:the 在位置 0、5;model 在位置 1、6
```
还原:
```python
def decode(inv):
    max_pos = max((max(p) for p in inv.values()), default=-1)
    words = [""] * (max_pos + 1)
    for word, positions in inv.items():
        for p in positions:
            words[p] = word
    return " ".join(w for w in words if w)
```

📌 **项目对应**:`backend/co_scientist/modules/m2_retriever/sources/`

---

## 4.4 异步并发:asyncio.gather

### 串行 vs 并行
5 条 query × 3 个源 = 15 次请求
- 串行(每次 2s):**30s**
- 并行:**~3s**(最慢的那个)

### 标准模式
```python
tasks = []
for q in queries:
    tasks.append(search_arxiv(q))
    tasks.append(search_semantic(q))
    tasks.append(search_openalex(q))

results = await asyncio.gather(*tasks, return_exceptions=True)
#                                          ^^^^^^^^^^^^^^^^^^
#                                          关键:单个失败不影响整体

# 剔除异常
ok_results = [r for r in results if not isinstance(r, Exception)]
```

### 并发限制(Semaphore)
LLM 抽取场景容易触发限流,用 Semaphore 限并发:
```python
sem = asyncio.Semaphore(5)  # 同时最多 5 个

async def with_sem(task):
    async with sem:
        return await task

results = await asyncio.gather(*(with_sem(t) for t in tasks))
```

---

## 4.5 RRF 融合:面试必考

### 问题
3 个源各返回 20 篇论文,有重叠也有不同。怎么合并成一个统一排序?

### 错误做法
直接合并 + 按"原始相关性分数"排序。
**问题**:不同源的分数完全不可比(arXiv 的 0.7 和 OpenAlex 的 4.2 没关系)。

### RRF(Reciprocal Rank Fusion)
```
score(d) = Σ_i  1 / (k + rank_i(d))
```
- `rank_i(d)`:文档 d 在第 i 个源的排名(1 是第一名)
- `k`:平滑常数,论文推荐 60

### 为什么有效
1. **只用排名,消除分数量纲差异**
2. **1/(k+rank) 凸递减**:top 几名分数差大,尾部差小
3. **多个源都靠前的文档,总分高** → 自动过滤噪音

### 实现
```python
def rrf(result_lists, k=60):
    merged = {}
    for rlist in result_lists:
        for rank, paper in enumerate(rlist, start=1):
            key = dedupe_key(paper)
            score = 1.0 / (k + rank)
            if key in merged:
                merged[key]["score"] += score
            else:
                merged[key] = {**paper, "score": score}
    return sorted(merged.values(), key=lambda p: -p["score"])
```

### 去重 key 设计
```python
def dedupe_key(paper):
    if paper.get("doi"):       return f"doi:{paper['doi'].lower()}"
    if paper.get("arxiv_id"):  return f"arxiv:{paper['arxiv_id']}"
    # 兜底:标题归一化
    title = re.sub(r'\W', '', paper["title"].lower())[:60]
    return f"title:{title}"
```

📌 **项目对应**:`backend/co_scientist/modules/m2_retriever/fusion.py`

---

## 4.6 时间衰减加权

### 动机
2017 年的 RAG 论文和 2025 年的 RAG 论文哪个更值得读?
研究热点往往新→旧,需要给新论文加权。

### 公式
```
weight = exp(-λ * age)
λ = ln(2) / half_life
```
"每过 half_life 年,权重降为原来的 1/2"。

### 实现
```python
import math
from datetime import datetime

def time_decay(papers, half_life_years=3.0):
    current = datetime.now().year
    lam = math.log(2) / half_life_years
    for p in papers:
        age = max(0, current - p["year"])
        p["score"] *= math.exp(-lam * age)
    return sorted(papers, key=lambda p: -p["score"])
```

### 替代方案
- **被引数加权**:`score *= log(1 + cited_count)`
- **会议级别加权**:NeurIPS/ICLR > 一般会议
- **混合**:`score *= time_w * citation_w * venue_w`

---

## 4.7 Citation Chasing

### 思路
找到 top-K 核心论文后,沿着引用图谱扩展:
- references(它引用了谁)→ 找到经典前作
- citations(谁引用了它)→ 找到最新进展

### 复杂度控制
单论文平均 20-50 条引用,K=5 扩展可能 200-500 篇。
要限制:
- 只对 top-K 扩展(K=5)
- 每篇取引用最高的 N 篇(N=10)
- **默认只 1 跳**(2 跳数量爆炸)

### 实现要点
```python
# Semantic Scholar 提供 references / citations 端点
url = f"https://api.semanticscholar.org/graph/v1/paper/{pid}/references"

# references 端点返回 {"citedPaper": {...}}
# citations 端点返回 {"citingPaper": {...}}
```

### 流程整合
```
1. RRF 融合得到 100 篇候选
2. 取 top-5 做 Citation Chasing → 50 篇扩展
3. 把扩展结果当作"第 4 个源",再 RRF 一次
4. 时间衰减
5. 截断到 top-30
```

📌 **项目对应**:`backend/co_scientist/modules/m2_retriever/citation_chase.py`

---

## 4.8 错误处理与降级

### 真实失败模式
- arXiv 返回 429(限流)
- Semantic Scholar timeout
- OpenAlex 字段缺失

### 兜底原则
```python
results = await asyncio.gather(*tasks, return_exceptions=True)
source_lists = [r for r in results if not isinstance(r, Exception)]

if not source_lists:
    logger.error("所有源都挂了")
    return []  # 让上层决定如何提示用户

# 单源能用就继续
```

### 限流退避(tenacity 异步版)
```python
async for attempt in AsyncRetrying(
    retry=retry_if_exception_type(httpx.HTTPStatusError),
    stop=stop_after_attempt(3),
    wait=wait_exponential(min=2, max=10),
    reraise=True,
):
    with attempt:
        resp = await client.get(...)
        resp.raise_for_status()
```

---

## 4.9 评估指标

跑完后,如何证明你的 RAG 比单源好?

| 指标 | 怎么测 |
|------|-------|
| **召回率** | 准备 20 个主题的"金标准"论文集,看你的 top-K 命中多少 |
| **NDCG@10** | 标 top 10 的相关性,算归一化折损累计增益 |
| **多样性** | top-K 中不同会议/年份的占比 |
| **时间贡献** | 加时间衰减前后,top-K 中近 3 年论文比例 |

---

## 📝 面试常见问题

1. **怎么改进朴素 RAG?**
   - Query Rewriting → 多源 → RRF → 时间衰减 → Citation Chasing → Re-ranking

2. **RRF 公式?为什么 k=60?**
   - `Σ 1/(k+rank)`,k=60 是论文经验值,平滑作用

3. **为什么不用加权平均合并多源?**
   - 不同源分数量纲不同,直接加会被某一源主导

4. **多源失败如何处理?**
   - `gather(return_exceptions=True)` + 剔除异常 + 单源也能继续

5. **如何防止 LLM 在 Query Rewriting 时跑偏?**
   - low temperature + 严格 system prompt + 限制单 query 长度

6. **Citation Chasing 数据量爆炸怎么办?**
   - top-K 限制 + 单论文 N 条限制 + 默认 1 跳

7. **时间衰减用什么函数?**
   - 指数衰减 `exp(-λ * age)`,λ 由 half-life 决定

---

## 🎯 练手题

1. 加第 4 个数据源(CrossRef 或 PubMed),复用现有 RRF
2. 把"时间衰减"换成"被引数加权 + 时间衰减混合",对比 top-10 变化
3. 加一层 BGE Re-ranker(本地小模型)做 RRF 后的二次排序
4. 给 RRF 加权重:让 OpenAlex 的贡献比 arXiv 大 1.5 倍

---

## ✅ 练手题参考答案

### 答案 1:加 CrossRef 数据源

新建 `m2_retriever/sources/crossref_src.py`:
```python
import httpx
from co_scientist.state import Paper
from co_scientist.utils import logger

API = "https://api.crossref.org/works"

async def search_crossref(query: str, max_results: int = 20) -> list[Paper]:
    params = {"query": query, "rows": max_results, "select": "DOI,title,abstract,author,issued,container-title,is-referenced-by-count"}
    headers = {"User-Agent": "AI-Co-Scientist/0.1 (mailto:you@example.com)"}  # polite pool
    try:
        async with httpx.AsyncClient(timeout=30.0, headers=headers) as c:
            r = await c.get(API, params=params)
            r.raise_for_status()
            items = r.json().get("message", {}).get("items", []) or []
    except Exception as e:
        logger.error("[crossref] 失败: {}", e); return []

    out = []
    for it in items:
        out.append(Paper(
            id=it.get("DOI", "") or "",
            title=(it.get("title") or [""])[0],
            abstract=(it.get("abstract") or "").replace("<jats:p>", "").replace("</jats:p>", ""),
            authors=[f"{a.get('given','')} {a.get('family','')}".strip() for a in (it.get("author") or [])],
            year=((it.get("issued") or {}).get("date-parts") or [[0]])[0][0] or 0,
            venue=(it.get("container-title") or [""])[0],
            arxiv_id="", doi=it.get("DOI", ""), url=f"https://doi.org/{it.get('DOI','')}",
            source="crossref",
            cited_by_count=it.get("is-referenced-by-count") or 0,
            score=0.0,
        ))
    return out
```

在 `retriever.py` 里把它加进 `asyncio.gather`,不改 fusion:RRF 天然支持任意数量的源。

要点:CrossRef 摘要会带 JATS XML 标签,要清洗;作者是 `given/family` 分字段,要拼;DOI 做 id。

### 答案 2:被引数 + 时间混合

**代码现状**:`fusion.py:125` 已经有 `apply_time_decay(papers, half_life_years=3.0)`,被 `retriever.py:95,104` 调用。它只做时间衰减,没考虑被引数。本题做的是在现有 `apply_time_decay` 基础上加一层被引加权。

方案 A — 最小侵入(推荐):保留原函数,加一个 `apply_citation_boost` 在它之后串联。

```python
# 追加到 fusion.py 末尾
def apply_citation_boost(papers: list[Paper], alpha: float = 0.3) -> list[Paper]:
    """
    被引数加权:score *= 1 + α · log1p(citations)。
    假设调用链里已经先做过 apply_time_decay,本函数只叠一层引用 boost。
    """
    for p in papers:
        cite_w = 1 + alpha * math.log1p(p.get("cited_by_count") or 0)
        p["score"] = (p.get("score") or 0.0) * cite_w
    papers.sort(key=lambda p: p.get("score", 0.0), reverse=True)
    return papers
```

然后在 `retriever.py` 里紧跟现有 `apply_time_decay` 调用后面加一行:
```python
merged = apply_time_decay(merged, half_life_years=3.0)
merged = apply_citation_boost(merged, alpha=0.3)   # ← 新增
```

方案 B — 彻底替换(想简化成一个函数):新写 `apply_hybrid_score` 同时做两件事,把 `retriever.py` 里两处 `apply_time_decay` 调用改为调新函数。

要点:
- **用 `log1p(citations)` 压缩长尾**:Transformer 有 10 万引用,普通论文才 10 个,直接乘会完全被经典论文统治
- α 调大(比如 0.6)会把经典论文提到 top,适合找"综述必引"
- α 调小(0.1)保持新论文优势,适合追前沿
- **推荐方案 A**:`apply_time_decay` 已经有完整注释,改动少;方案 B 虽然更"优雅"但破坏了现有调用方

对比 top-10:新论文(高 RRF + 低引用) vs 经典论文(中 RRF + 高引用),α=0.3 一般在前 10 里两种各占一半。

### 答案 3:BGE Re-ranker

```python
# requirements: sentence-transformers, FlagEmbedding
from FlagEmbedding import FlagReranker

_reranker: FlagReranker | None = None

def get_reranker() -> FlagReranker:
    global _reranker
    if _reranker is None:
        _reranker = FlagReranker("BAAI/bge-reranker-v2-m3", use_fp16=True)
    return _reranker

def bge_rerank(query: str, papers: list[Paper], top_k: int = 30) -> list[Paper]:
    """对 RRF 后的前 top_k 篇用 BGE 做 cross-encoder 重排。"""
    rr = get_reranker()
    head = papers[:top_k]
    pairs = [(query, (p.get("title","") + " " + (p.get("abstract","") or ""))[:512]) for p in head]
    scores = rr.compute_score(pairs, normalize=True)
    for p, s in zip(head, scores):
        p["score"] = float(s)  # 覆盖 RRF 分
    head.sort(key=lambda p: p["score"], reverse=True)
    return head + papers[top_k:]  # 重排 top_k,其余保持原序
```

要点:
- **只重排 head 部分**:cross-encoder 贵,对 1000 篇全跑太慢
- **标题 + 摘要截 512 token**:BGE 最大 seq len 有限
- **首次加载模型会下载 ~600MB**,配合 diskcache 缓存 score 键 = (query_hash, paper_id)

### 答案 4:加权 RRF

改 `reciprocal_rank_fusion` 签名:
```python
def reciprocal_rank_fusion(
    result_lists: list[list[Paper]],
    weights: list[float] | None = None,
    k: int = 60,
) -> list[Paper]:
    if weights is None:
        weights = [1.0] * len(result_lists)
    assert len(weights) == len(result_lists)

    merged: dict[str, Paper] = {}
    for rlist, w in zip(result_lists, weights):
        for rank, paper in enumerate(rlist, start=1):
            key = _dedupe_key(paper)
            contrib = w * 1.0 / (k + rank)
            if key in merged:
                merged[key]["score"] = merged[key].get("score", 0.0) + contrib
            else:
                new = dict(paper); new["score"] = contrib
                merged[key] = new
    return sorted(merged.values(), key=lambda p: p.get("score", 0.0), reverse=True)

# retriever.py 调用处
fused = reciprocal_rank_fusion(
    [arxiv_results, openalex_results, s2_results],
    weights=[1.0, 1.5, 1.0],
)
```

要点:**权重是相对值**,重要的是比例不是绝对大小。要系统地调权重建议用人工评测 100 条 query 的 MRR 做 grid search,而不是拍脑袋。
