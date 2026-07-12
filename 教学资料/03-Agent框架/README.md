# 03 - Agent 框架(LangGraph)

> LangGraph 是 LangChain 团队为"有状态、可中断、可分叉"的 Agent 设计的编排框架。
> 本章覆盖:StateGraph、Reducer、Checkpointer、interrupt、条件边。

---

## 3.1 为什么不用纯 Python 函数?

### 简单流程:函数链就够
```python
def pipeline(question):
    refined = refine(question)
    papers = retrieve(refined)
    critique = review(papers)
    return critique
```

### 但 Agent 项目需要:
1. **可中断 + 续跑**:跑到一半断网,重启从断点继续
2. **可分叉**:在某个节点拉出 3 条假设并行跑
3. **可回溯**:回到任意节点重新跑
4. **人工介入**:某些节点需要用户做选择再继续
5. **可观测**:每个节点的输入输出都能审计

纯函数链做不到这些。LangGraph 把"流程"建模成**状态图**,所有上述能力都内建。

---

## 3.2 核心概念三件套

```
State(状态) ←——节点修改——→ Node(节点) ←—边连接—→ Node
   │                                                  │
   └─────────────── Checkpointer ──────────────────────┘
                  (持久化每一步快照)
```

### State
- 全局状态对象(TypedDict / pydantic)
- 所有节点共享 + 修改
- 字段可定义 reducer(多个节点同时写时如何合并)

### Node
- 一个 Python 函数:`(state) -> partial_state`
- 只返回**变化**的字段,框架自动 merge

### Edge
- 静态边:`A → B`
- 条件边:根据 state 决定下一步去哪

### Checkpointer
- 每个节点执行后自动保存 state 到 SQLite/Postgres
- 支持回放和分叉

---

## 3.3 State 设计:TypedDict + Reducer

### 基本写法
```python
from typing import TypedDict

class ResearchState(TypedDict, total=False):
    raw_question: str
    pico: dict
    papers: list[Paper]
```

`total=False` 表示所有字段可选,节点可以只返回部分字段。

### Reducer:并行节点合并
默认行为:后写的覆盖先写的。
但有时多个节点并行,各自往同一字段追加 → 需要 reducer。

```python
import operator
from typing import Annotated

class ResearchState(TypedDict, total=False):
    papers: Annotated[list[Paper], operator.add]   # 多节点写时,列表相加
    error_log: Annotated[list[str], operator.add]
```

`Annotated[T, fn]` 告诉 LangGraph:看到这个字段,用 `fn(old, new)` 合并而不是覆盖。

### 内置 reducer
- `operator.add`:列表 / 字符串拼接
- 自定义函数:`def merge_dict(a, b): return {**a, **b}`

📌 **项目对应**:`backend/co_scientist/state/research_state.py`

---

## 3.4 节点函数

### 基本形式
```python
def refine_question_node(state: ResearchState) -> dict:
    question = state["raw_question"]
    pico = build_pico(question)
    return {"pico": pico}  # 只返回新字段,框架自动 merge
```

### 失败兜底:safe_node 包装
```python
def safe_node(name, fn):
    def wrapped(state):
        try:
            return fn(state) or {}
        except Exception as e:
            logger.exception(f"[{name}] 失败")
            return {"error_log": [f"[{name}] {e}"]}
    return wrapped

g.add_node("m1", safe_node("m1", refine_question_node))
```
单节点失败不会中断整个 pipeline(配合 reducer,error_log 自动累积)。

### 异步节点
LangGraph 支持 `async def`,更适合 I/O 密集场景。
本项目大部分节点是同步函数 + 内部 `asyncio.run`,因为 LangGraph SQLite Checkpointer 用同步更简单。

---

## 3.5 构建图

```python
from langgraph.graph import END, START, StateGraph

g = StateGraph(ResearchState)

# 添加节点
g.add_node("m1_refine", refine_node)
g.add_node("m2_retrieve", retrieve_node)
g.add_node("m4_critique", critique_node)

# 静态边
g.add_edge(START, "m1_refine")
g.add_edge("m1_refine", "m2_retrieve")
g.add_edge("m2_retrieve", "m4_critique")
g.add_edge("m4_critique", END)

graph = g.compile()
```

### 条件边(根据 state 决定下一步)
```python
def route_after_critique(state) -> str:
    if state["meta_decision"]["decision"] == "reject":
        return END  # 拒了,不写论文
    return "m7_writer"

g.add_conditional_edges("m4_critique", route_after_critique, {
    "m7_writer": "m7_writer",
    END: END,
})
```

### 并行节点(同一节点的多次执行)
通过 `Send` API 给同一节点发多个任务,框架自动并行执行,结果用 reducer 合并。
本项目里"并行检索 3 个数据源"用的是 asyncio,没用 Send;但 Send 适合"动态并行"场景。

---

## 3.6 Checkpointer:可中断 + 可回放

### SQLite(轻量,推荐起步)
```python
from langgraph.checkpoint.sqlite import SqliteSaver
import sqlite3

conn = sqlite3.connect("checkpoints.db", check_same_thread=False)
checkpointer = SqliteSaver(conn)

graph = g.compile(checkpointer=checkpointer)
```

### Postgres(生产)
```python
from langgraph.checkpoint.postgres import PostgresSaver
checkpointer = PostgresSaver.from_conn_string(POSTGRES_URL)
checkpointer.setup()
```

### Memory(测试)
```python
from langgraph.checkpoint.memory import MemorySaver
checkpointer = MemorySaver()  # 进程退出丢失
```

### thread_id:多任务隔离
```python
config = {"configurable": {"thread_id": "user_abc_research_1"}}
graph.invoke(initial_state, config=config)
```
LangGraph 用 `thread_id` 区分不同会话/任务的状态历史。
本项目用 `fork_id` 充当 thread_id。

---

## 3.7 interrupt_before:暂停 + 人工介入

### 场景
模块 6 代码执行有三档(`generate_only` / `dry_run` / `full_execute`)。
要让用户看到模块 5 结果后再选择档位。

### 实现
```python
graph = g.compile(
    checkpointer=checkpointer,
    interrupt_before=["m6_execute"],  # 这个节点之前暂停
)

# 第一次跑
result = graph.invoke(initial, config={"configurable": {"thread_id": "x"}})
# → 跑到 m6_execute 前停下,返回中间 state

# 用户在前端选了 "full_execute" 后
graph.update_state(config, {"execution_mode": "full_execute"})
result2 = graph.invoke(None, config=config)  # 传 None 表示从断点续跑
# → 继续跑完
```

### 替代方案:`interrupt()` 调用
LangGraph 0.2.30+ 支持节点内部调用 `interrupt(prompt)`,
更灵活但需要前端配套实现 resume 逻辑。

---

## 3.8 流式输出(`stream` / `astream`)

### 同步版
```python
for chunk in graph.stream(initial, config=cfg):
    # chunk 是 {"node_name": partial_state}
    print(chunk)
```
每个节点完成后吐一个 chunk,前端可以实时显示进度。

### 异步版
```python
async for chunk in graph.astream(initial, config=cfg):
    await ws.send_json(chunk)
```

### Token 级流式
LangGraph 0.2.30+ 支持 `stream_mode="messages"`,
逐 token 流出节点内的 LLM 输出(用于聊天机器人式场景)。

---

## 3.9 状态历史 + 回放

```python
# 拿当前 state
current = graph.get_state(config)

# 拿历史(Checkpointer 自动存的所有快照)
history = list(graph.get_state_history(config))

# 回到某个历史点
target = history[3]  # 第 4 步的快照
graph.invoke(None, config=target.config)  # 从那个点重新跑
```

### 分叉
基于历史快照创建新 thread_id,即"分叉"。
本项目 `m8_replay/fork_manager.py` 在此基础上加了元数据管理。

---

## 3.10 调试技巧

### 1. 可视化图结构
```python
from langgraph.graph import StateGraph
print(graph.get_graph().draw_mermaid())  # 输出 Mermaid 图
```

### 2. 单节点测试
节点是普通函数,直接调:
```python
state = make_initial_state("test")
patch = refine_question_node(state)
assert "pico" in patch
```

### 3. dry-run
用 `MemorySaver` + 小 max_iter 跑通流程,再切真实 Checkpointer。

---

## 📝 面试常见问题

1. **LangGraph 和 LangChain 区别?**
   - LangChain 是组件库(LLM/工具/链);LangGraph 是状态图编排器,适合复杂 Agent

2. **State 的 reducer 是什么?**
   - 多节点并行写同一字段时的合并函数,如 `operator.add` 让 list 累加

3. **如何实现"暂停等用户"?**
   - `compile(interrupt_before=["node"])` + Checkpointer + `update_state` 后续跑

4. **Checkpointer 选哪个?**
   - 起步 SQLite,生产 Postgres,测试 Memory

5. **如何分叉?**
   - 同一 graph + 不同 thread_id;或基于历史快照新建 thread_id

6. **节点失败如何处理?**
   - 包 try/except 写 error_log;或用条件边路由到 fallback 节点

---

## 🎯 练手题

1. 把 `m4_critique` 的输出加条件边:`reject` 时跳过 m5/m6/m7 直接 END
2. 实现一个"分叉演示":对同一问题构造 3 个不同 PICO,并行跑 3 条 pipeline,横向对比 Meta 决定
3. 加一个 token 级流式接口:`graph.astream(..., stream_mode="messages")`

---

## ✅ 练手题参考答案

### 答案 1:reject 分支直达 END

LangGraph 的条件边用 `add_conditional_edges`:
```python
def _after_critique(state: ResearchState) -> str:
    decision = (state.get("meta_decision") or {}).get("decision", "")
    if decision == "reject":
        return "appendix_reflect"  # 直接跳到末尾反思,仍然写记忆库
    return "m5_experiment"

# 在 build_graph 里,把原来的 add_edge("m4_critique", "m5_experiment") 删掉,改成:
g.add_conditional_edges(
    "m4_critique",
    _after_critique,
    {"m5_experiment": "m5_experiment", "appendix_reflect": "appendix_reflect"},
)
```

要点:
- **reject 后仍然走 `appendix_reflect`**,这样"被拒"本身就能沉淀到记忆里("这类问题不好做")
- **条件函数返回字符串 key**,第三个参数是 key → node 的映射,别漏任何分支否则图就卡住了

### 答案 2:并行 3 条 pipeline 分叉

```python
import asyncio, hashlib
from co_scientist.graph import build_graph
from co_scientist.state import make_initial_state
from co_scientist.modules.m8_replay import ForkManager

async def fork_run(question: str, picos: list[dict]) -> list[dict]:
    fm = ForkManager()
    graph = build_graph()

    async def _one(pico_hint: dict) -> dict:
        fid = hashlib.md5(f"{question}{pico_hint}".encode()).hexdigest()[:12]
        fm.create_fork(parent_fork_id="", branch_node="root", description=str(pico_hint))
        state = make_initial_state(question, fork_id=fid)
        state["pico"] = pico_hint  # 预置 PICO,m1 会检测到已存在直接跳过
        final = await graph.ainvoke(state, config={"configurable": {"thread_id": fid}})
        fm.update_status(fid, "done", float((final.get("meta_decision") or {}).get("final_rating", 0)))
        return {"fork_id": fid, "pico": pico_hint, "meta": final.get("meta_decision")}

    return await asyncio.gather(*[_one(p) for p in picos])

# 用法
picos = [
    {"intervention": "RAG", "outcome": "factuality"},
    {"intervention": "RLHF", "outcome": "factuality"},
    {"intervention": "self-consistency", "outcome": "factuality"},
]
results = asyncio.run(fork_run("减少 LLM 幻觉", picos))
for r in sorted(results, key=lambda x: -x["meta"]["final_rating"]):
    print(r["fork_id"], r["pico"], r["meta"]["final_rating"])
```

要点:
- **不同 fork_id = 不同 thread_id**,Checkpointer 天然隔离
- **预置 `state["pico"]`** 是跳过 m1 反问的最干净办法(m1 的 node 会看到 `pico.refined_question` 已存在直接返回)
- m4/m5 等内部用 SQLite + asyncio.gather 注意:多线程写同一个 checkpoint.sqlite 偶尔会锁,小规模(≤5 分叉)实测没问题,大了就切 Postgres

### 答案 3:token 级流式

LangGraph 的 `astream_events` API:
```python
async def stream_graph(question: str):
    graph = build_graph()
    state = make_initial_state(question)
    config = {"configurable": {"thread_id": "stream-demo"}}
    async for event in graph.astream_events(state, config=config, version="v2"):
        kind = event["event"]
        if kind == "on_chat_model_stream":
            # LLM 逐 token 的 delta
            chunk = event["data"]["chunk"]
            delta = getattr(chunk, "content", "") or ""
            if delta:
                yield {"node": event["metadata"].get("langgraph_node"), "delta": delta}
        elif kind == "on_chain_end":
            yield {"node": event["name"], "done": True}
```

前端用 SSE 接:
```python
# FastAPI 路由
from sse_starlette.sse import EventSourceResponse
@app.get("/stream")
async def stream(q: str):
    return EventSourceResponse(stream_graph(q))
```

要点:
- **用 `astream_events` 而不是 `astream`**:后者按节点粒度推状态,前者能拿到 LLM 内部的 token delta
- **过滤 kind**:`on_chat_model_stream` 是 token delta,`on_chain_end` 是节点结束,其他事件不用全送
- 本项目 LLM 客户端已经实现 `chat_stream`,但 LangGraph 的流式回调绕不开底层 SDK 的 stream=True,要让 `chat_stream` 路径真的跑起来,还需要在节点函数里改成 `async def` + 调 `chat_stream` 而不是 `chat`
