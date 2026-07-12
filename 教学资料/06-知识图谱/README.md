# 06 - 知识图谱

> 从论文摘要抽三元组 → 构建图 → 识别研究空白。本章涵盖 LLM-as-Extractor、NetworkX、GraphRAG 基础。

---

## 6.1 为什么要建知识图谱

RAG 只能"基于文本回答问题",但:
- 看不到"方法 A 改进了方法 B"这种结构关系
- 无法做"找出所有对 RAG 的改进"这种图查询
- 难以识别研究脉络和空白

**KG(知识图谱)补足这块**:把论文关系建模成图,做结构化分析。

---

## 6.2 三元组抽取:让 LLM 做 NER+RE

### 传统 NLP 做法
- NER(命名实体识别)+ RE(关系抽取)
- 要训数据、调模型,工作量大

### LLM 做法
```python
SYSTEM = """\
从论文摘要中抽取三元组 (head, relation, tail)。
关系限定:[improves, uses, compares_with, cites, proposes, evaluates_on]
返回 JSON: {"triples": [...]}"""

result = llm.chat_json([
    {"role": "system", "content": SYSTEM},
    {"role": "user", "content": f"标题: {title}\n摘要: {abstract}"}
])
```

### 关系类型白名单
**只允许 6 种关系**,防止 LLM 瞎编。抽取后过滤:
```python
VALID = {"improves", "uses", "compares_with", "cites", "proposes", "evaluates_on"}
triples = [t for t in extracted if t["relation"] in VALID]
```

### 为什么关系要限制?
- 开放式关系会产生 "improves_with_better_performance" 这种口水词
- 图查询/可视化需要类型稳定
- 下游聚合需要统一名

📌 **项目对应**:`backend/co_scientist/modules/m3_kg/kg_builder.py`

---

## 6.3 批量并行 + 并发限制

```python
sem = asyncio.Semaphore(5)  # 同时最多 5 个 LLM 调用,防限流

async def with_sem(paper):
    async with sem:
        return await extract_triples(paper)

results = await asyncio.gather(*(with_sem(p) for p in papers))
```

### 成本估算
- 30 篇论文 × deepseek-chat 约 $0.01-0.02
- 便宜到可以忽略

---

## 6.4 NetworkX 图操作

### 为什么选 NetworkX 而不是 Neo4j
- **轻量**:pip install 即用,无需部署
- **Python 原生**:操作直接
- 够一般分析用
- 要做大规模查询时再切 Neo4j

### 构建
```python
import networkx as nx

g = nx.MultiDiGraph()  # 多重有向图
for t in triples:
    g.add_edge(t["head"], t["tail"],
               relation=t["relation"],
               source=t["source_paper_id"])
```

为什么 `MultiDiGraph`?
- `Multi`:同两点间可以多条边(A improves B,同时 A compares_with B)
- `Di`:有向(improves 有方向)

### 常用操作
```python
g.number_of_nodes()             # 节点数
g.number_of_edges()             # 边数
g.nodes()                       # 所有节点
g.edges(data=True)              # 带属性的所有边

g.in_edges("RAG", data=True)    # 指向 RAG 的边
g.out_edges("RAG")              # 从 RAG 出发的边

list(g.predecessors("RAG"))     # RAG 的前驱节点
list(g.successors("RAG"))       # RAG 的后继节点

nx.shortest_path(g, "A", "B")   # A 到 B 的最短路径
```

### 持久化:GraphML 格式
```python
nx.write_graphml(g, "kg.graphml")
# 前端 Cytoscape.js 直接读,D3.js 也支持
```

---

## 6.5 研究空白识别

### 启发式定义
"研究空白" = 被多篇论文识别为问题 / 目标,但很少人提解决方案。

### 实现
```python
def identify_gaps(g):
    # 谁被 "improves"/"proposes" 指向?
    improved = Counter()
    for _, tail, data in g.edges(data=True):
        if data["relation"] in ("improves", "proposes"):
            improved[tail] += 1

    # 谁作为 head?(即谁主动提方案)
    out_count = Counter(h for h, _, _ in g.edges(data=True))

    # 被多人想改但自己很少提方案 → 研究空白
    gaps = [node for node, cnt in improved.most_common(20)
            if cnt >= 2 and out_count[node] < cnt]
    return gaps
```

### 真实做法:子图模式匹配
```cypher
// 用 Cypher(Neo4j)查:
// 找出被 3 篇以上论文指出为 "问题" 的节点
MATCH (p:Paper)-[r:identifies_problem]->(n)
WITH n, count(DISTINCT p) as problem_count
WHERE problem_count >= 3
// 同时这个节点没有被 "proposes_solution" 指向过
AND NOT EXISTS((:Paper)-[:proposes_solution]->(n))
RETURN n, problem_count ORDER BY problem_count DESC
```

---

## 6.6 GraphRAG:向量 + 图路径

### 普通 RAG
```
问题 → embedding → 向量检索 top-K → 拼 prompt
```

### GraphRAG
```
问题 → embedding → 向量检索 top-K 实体节点
     → 图路径扩展(BFS 2 跳)→ 收集相关节点/边
     → 把节点+边作为结构化上下文
     → 拼 prompt
```

### 好处
向量只能捕获**语义相似**,图能捕获**结构关联**。
"X 是 RAG 的一种实现" → 向量难找到,图一跳就到。

### 实现(本项目骨架)
```python
def graph_rag_retrieve(question, g, top_k=5):
    # 1. 向量找入口节点
    entry_nodes = vector_search(question, top_k=3)

    # 2. BFS 扩展 2 跳
    context_nodes = set(entry_nodes)
    for n in entry_nodes:
        context_nodes |= set(nx.descendants_at_distance(g, n, 1))
        context_nodes |= set(nx.descendants_at_distance(g, n, 2))

    # 3. 收集节点+边作为上下文
    subgraph = g.subgraph(context_nodes)
    return format_subgraph(subgraph)
```

本项目没实现 GraphRAG,这是你可以扩展的方向。

---

## 6.7 可视化

### 前端选型
| 库 | 特点 |
|----|------|
| **Cytoscape.js** | 最成熟,社区大,支持大图 |
| **D3.js** | 灵活但要手写交互 |
| **React Flow** | React 原生,适合中小图 |
| **vis.js** | 简单易用 |

### 读 GraphML
```js
// Cytoscape.js
const cy = cytoscape({
  container: document.getElementById('graph'),
  elements: await fetch('/api/kg.graphml').then(r => r.text()).then(parseGraphML),
  style: [
    { selector: 'node', style: { label: 'data(id)' } },
    { selector: 'edge', style: { label: 'data(relation)' } },
  ],
  layout: { name: 'cose' },  // force-directed
});
```

---

## 6.8 Neo4j 升级路径

当图规模 > 10 万节点,NetworkX 内存撑不住时切 Neo4j。

### 安装(Docker)
```yaml
services:
  neo4j:
    image: neo4j:5-community
    ports: ["7474:7474", "7687:7687"]
    environment:
      NEO4J_AUTH: neo4j/password
```

### Python 客户端
```python
from neo4j import GraphDatabase

driver = GraphDatabase.driver("bolt://localhost:7687", auth=("neo4j", "password"))

with driver.session() as sess:
    sess.run("""
        MERGE (h:Entity {name: $head})
        MERGE (t:Entity {name: $tail})
        MERGE (h)-[:RELATES {type: $rel}]->(t)
    """, head="A", tail="B", rel="improves")
```

### Cypher 查询
```cypher
MATCH (n)-[r:RELATES {type: "improves"}]->(m)
RETURN n.name, m.name
LIMIT 20
```

---

## 📝 面试常见问题

1. **KG 和 RAG 的关系?**
   - KG 提供结构化关系,补 RAG 向量检索的不足。两者融合 = GraphRAG

2. **为什么限制关系类型?**
   - 防止 LLM 生成无穷多"近义关系",下游聚合/查询不稳定

3. **MultiDiGraph 什么时候用?**
   - 同两节点间可能多种关系,且关系有方向

4. **如何识别研究空白?**
   - 启发式:被多人想改但自己少提方案的节点;高级做法用子图模式匹配

5. **NetworkX 何时切 Neo4j?**
   - 图规模 > 10 万节点,或需要 ACID 事务、多进程共享

6. **GraphRAG 比 RAG 强在哪?**
   - 能利用结构关联,向量相似之外的路径关联也能召回

---

## 🎯 练手题

1. 把关系类型从 6 种扩到 10 种(加 `extends`、`generalizes`、`refutes` 等)
2. 实现 Cypher-ish 的子图查询:"所有对 X 做改进的论文"
3. 把 NetworkX 换成 Neo4j Community,对比性能
4. 实现最简 GraphRAG:向量召回 top-3 + 图扩展 2 跳

---

## ✅ 练手题参考答案

### 答案 1:扩展到 10 种关系

`state/research_state.py` 的 `Triple.relation` 注释改成 10 种:
```python
# relation 限定为以下 10 种:
#   improves, uses, compares_with, cites, proposes, evaluates_on,
#   extends       -- 在已有工作基础上扩展(不等于 improves,可能只是换场景)
#   generalizes   -- 泛化到更广场景(e.g. RAG → multi-modal RAG)
#   refutes       -- 反驳 / 指出错误
#   combines      -- 组合两个已有方法
```

`prompts/templates.py` 的三元组抽取 prompt 里同步更新关系枚举清单。

要点:
- **新类型要有清晰定义**,否则 LLM 会把 extends 和 improves 混用
- 存量三元组如果想迁移,写一个一次性脚本用 LLM 重判就行,NetworkX 不需要 migration

### 答案 2:"所有对 X 做改进的论文"

在 `m3_kg/kg_builder.py` 加查询方法:
```python
import networkx as nx

def papers_improving(graph: nx.MultiDiGraph, target: str) -> list[str]:
    """
    返回所有 (X)-[improves|extends]->(target) 的 X(论文节点)。
    """
    results = []
    for u, v, data in graph.in_edges(target, data=True):
        if data.get("relation") in ("improves", "extends"):
            results.append(u)
    return results

def papers_improving_transitive(graph, target, max_hops=2) -> set[str]:
    """2 跳:X->Y->target 也算。"""
    seen = set()
    frontier = {target}
    for _ in range(max_hops):
        next_frontier = set()
        for node in frontier:
            for u, _, data in graph.in_edges(node, data=True):
                if data.get("relation") in ("improves", "extends") and u not in seen:
                    seen.add(u); next_frontier.add(u)
        frontier = next_frontier
    return seen
```

要点:
- **MultiDiGraph 的 `in_edges` 自带 data=True** 能拿到边属性
- 2 跳遍历用 BFS,别写递归(深图会栈溢出)
- 真要做复杂查询(路径 / 环 / 最短路),直接上 Neo4j

### 答案 3:Neo4j 替换对比

加 `m3_kg/neo4j_store.py`:
```python
from neo4j import GraphDatabase
from co_scientist.config import settings

class Neo4jStore:
    def __init__(self):
        self.driver = GraphDatabase.driver(
            settings.NEO4J_URI,
            auth=("neo4j", settings.NEO4J_PASSWORD.get_secret_value()),
        )

    def upsert_triples(self, triples: list[Triple]) -> None:
        with self.driver.session() as s:
            s.execute_write(self._write, triples)

    @staticmethod
    def _write(tx, triples):
        tx.run("UNWIND $rows AS r "
               "MERGE (a:Entity {name: r.head}) "
               "MERGE (b:Entity {name: r.tail}) "
               "MERGE (a)-[e:REL {type: r.relation}]->(b) "
               "SET e.paper = r.source_paper_id",
               rows=[dict(t) for t in triples])

    def query_improving(self, target: str, max_hops: int = 2):
        cypher = """
        MATCH (x:Entity)-[r:REL*1..%d]->(t:Entity {name: $target})
        WHERE ALL(e IN r WHERE e.type IN ['improves','extends'])
        RETURN DISTINCT x.name
        """ % max_hops
        with self.driver.session() as s:
            return [rec["x.name"] for rec in s.run(cypher, target=target)]
```

性能对比(1 万三元组、查询 "X -improves-> target",2 跳):
| 方案 | 导入耗时 | 单查询耗时 | 内存占用 |
|---|---|---|---|
| NetworkX in-memory | 2s | 5ms | ~200MB |
| Neo4j(本地 Docker) | 20s(批量 UNWIND) | 30ms | 独立进程 |

要点:
- **1 万级三元组 NetworkX 够用**,Neo4j 的价值在 >10 万节点 + 多人共享
- Neo4j 查询要**一定用参数绑定**(`$target`),别拼字符串(Cypher 注入风险)

### 答案 4:最简 GraphRAG

```python
from FlagEmbedding import BGEM3FlagModel
from qdrant_client import QdrantClient, models

embed = BGEM3FlagModel("BAAI/bge-m3", use_fp16=True)
qdrant = QdrantClient(url=settings.QDRANT_URL)

def build_kg_with_vectors(triples, papers):
    """每个实体存一个向量(用它的 top 摘要拼接)。"""
    entity_texts: dict[str, list[str]] = {}
    for t in triples:
        for e in (t["head"], t["tail"]):
            entity_texts.setdefault(e, []).append(t["head"] + " " + t["relation"] + " " + t["tail"])
    points = []
    for i, (ent, texts) in enumerate(entity_texts.items()):
        vec = embed.encode(" ".join(texts[:5]))["dense_vecs"]
        points.append(models.PointStruct(id=i, vector=vec.tolist(), payload={"name": ent}))
    qdrant.upsert("entities", points=points)

def graph_rag_query(graph, question: str, top_k: int = 3, hops: int = 2):
    # 1) 向量召回种子实体
    qvec = embed.encode(question)["dense_vecs"]
    hits = qdrant.search("entities", query_vector=qvec.tolist(), limit=top_k)
    seeds = [h.payload["name"] for h in hits]
    # 2) 图扩展 N 跳,收集上下文
    visited = set(seeds)
    for _ in range(hops):
        frontier = set()
        for s in list(visited):
            if s in graph:
                frontier.update(graph.successors(s))
                frontier.update(graph.predecessors(s))
        visited |= frontier
    # 3) 拼成 context 给 LLM
    ctx = "\n".join(f"{u} --{d['relation']}--> {v}" for u,v,d in graph.edges(data=True) if u in visited and v in visited)
    return ctx
```

要点:
- **向量召回定义"入口",图扩展补全"周边"**,两者互补
- 控 hops≤2,再多 context 会炸
- 想严格的 GraphRAG 看微软开源的 graphrag 包,核心思路一致
