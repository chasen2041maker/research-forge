# 10 - Web 集成

> 把后端 LangGraph pipeline 暴露为 HTTP/WebSocket,前端 Next.js 接入。
> 涵盖:FastAPI 路由、WebSocket 流式、CORS、BackgroundTasks vs Celery、前端轮询。

---

## 10.1 为什么 Agent 项目要 Web 化

### CLI 局限
- 长任务(20-30 分钟)阻塞终端
- 无法流式展示中间结果
- 不能多任务并行 + 分叉对比
- 无法做"研究树"可视化

### Web 优势
- 异步:启动后立即返回 ID
- 可视化:研究树 + 知识图谱 + 对话气泡
- 协作:多人同时跑、对比

---

## 10.2 FastAPI 速通

### 最小例子
```python
from fastapi import FastAPI
app = FastAPI()

@app.get("/")
def root():
    return {"hello": "world"}
```

启动:
```bash
uvicorn main:app --reload --port 8000
```

### 路由 + Pydantic 入参校验
```python
from pydantic import BaseModel

class StartRequest(BaseModel):
    question: str
    execution_mode: str = "generate_only"

@app.post("/api/research/start")
def start(req: StartRequest):
    return {"fork_id": "..."}
```
入参非法时 FastAPI 自动返回 422 + 详细错误,不用手写校验。

### 自动文档
访问 `http://localhost:8000/docs` 看 Swagger UI。

---

## 10.3 BackgroundTasks 跑长任务

### 同步阻塞 ❌
```python
@app.post("/api/start")
def start(req):
    result = run_pipeline(req.question)  # 阻塞 20 分钟
    return result
```
HTTP 请求超时,前端连接断。

### BackgroundTasks ✅(简单方案)
```python
from fastapi import BackgroundTasks

@app.post("/api/start")
def start(req, bg: BackgroundTasks):
    fork_id = create_fork(...)

    def _run():
        run_pipeline(req.question)

    bg.add_task(_run)
    return {"fork_id": fork_id, "status": "running"}
```
立即返回,任务在后台跑。

### 局限
- 单进程内跑,重启丢任务
- 无法分布式扩展
- 无重试机制

### Celery ✅✅(生产方案)
```python
from celery import Celery
app_celery = Celery("co_scientist", broker="redis://localhost:6379/0")

@app_celery.task
def run_research_task(question: str):
    return run_pipeline(question)

# FastAPI 里
@app.post("/api/start")
def start(req):
    task = run_research_task.delay(req.question)
    return {"task_id": task.id}
```
Celery 跑独立 worker 进程,可扩到多机。

---

## 10.4 WebSocket:流式状态推送

### 服务端
```python
from fastapi import WebSocket, WebSocketDisconnect

@app.websocket("/ws/research/{fork_id}")
async def ws_research(ws: WebSocket, fork_id: str):
    await ws.accept()
    try:
        while True:
            run = _runs.get(fork_id)
            if run is None:
                await ws.send_json({"error": "fork_id 不存在"})
                break
            await ws.send_json({"status": run["status"]})
            if run["status"] in ("done", "error"):
                await ws.send_json({"snapshot": run["state"]})
                break
            await asyncio.sleep(1.0)
    except WebSocketDisconnect:
        pass
```

### 客户端(React)
```tsx
useEffect(() => {
  const ws = new WebSocket(`ws://localhost:8000/ws/research/${forkId}`);
  ws.onmessage = (ev) => {
    const msg = JSON.parse(ev.data);
    setStatus(msg.status);
    if (msg.snapshot) setSnapshot(msg.snapshot);
  };
  return () => ws.close();
}, [forkId]);
```

### 进阶:pub/sub
真正生产级用 Redis Pub/Sub 而非每秒轮询内存:
```python
# 任务进度更新时
redis.publish(f"research:{fork_id}", json.dumps(progress))

# WebSocket handler 订阅
pubsub = redis.pubsub()
pubsub.subscribe(f"research:{fork_id}")
async for msg in pubsub.listen():
    await ws.send_text(msg["data"])
```

---

## 10.5 CORS

### 跨域问题
前端 `localhost:3000` → 后端 `localhost:8000`,浏览器默认拦截。

### FastAPI 中间件
```python
from fastapi.middleware.cors import CORSMiddleware

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],  # 生产改成具体域名
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
```

### Next.js rewrites(更优雅)
```js
// next.config.js
async rewrites() {
  return [{ source: "/api/:path*", destination: "http://localhost:8000/api/:path*" }];
}
```
前端调用 `/api/xxx`,Next 自己代理到后端,绕过 CORS。

⚠️ rewrites **不代理 WebSocket**,WS 仍要直连后端 8000。

---

## 10.6 Next.js 15 + App Router

### 目录结构
```
src/
└── app/
    ├── layout.tsx     # 根布局(全站共用)
    ├── page.tsx       # / 路由
    ├── globals.css    # 全局样式
    └── research/
        └── [id]/
            └── page.tsx  # /research/abc 动态路由
```

### Client Component 标记
```tsx
"use client";  // ← 顶部加这行,才能用 useState/useEffect
```
默认是 Server Component,不能用 hook。

### 数据获取
```tsx
async function startResearch() {
  const resp = await fetch("/api/research/start", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ question }),
  });
  return resp.json();
}
```

---

## 10.7 状态管理

### 简单页面:`useState`
```tsx
const [forkId, setForkId] = useState<string | null>(null);
const [snapshot, setSnapshot] = useState(null);
```

### 复杂跨页:Zustand(推荐)
```tsx
import { create } from "zustand";

const useStore = create((set) => ({
  forks: [],
  addFork: (f) => set((s) => ({ forks: [...s.forks, f] })),
}));
```
比 Redux 简单 10 倍,够用。

---

## 10.8 可视化组件选型

### 知识图谱
| 库 | 适用 |
|----|------|
| **Cytoscape.js** | 大图,交互丰富 |
| **React Flow** | React 原生,中等图,自定义节点容易 |
| **D3.js** | 小图 + 完全自定义 |

### 研究树
- 节点 < 100:React Flow 最方便
- 节点 > 1000:Cytoscape.js + virtualized 渲染

### 代码高亮
- `react-syntax-highlighter`:简单
- `monaco-editor`:全功能(VSCode 同款),但体积大

### Markdown
- `react-markdown` + `remark-gfm`

---

## 10.9 流式渲染对话气泡

### 服务端
```python
@app.websocket("/ws/critique/{fork_id}")
async def stream_critique(ws, fork_id):
    await ws.accept()
    for token in llm.chat_stream(...):
        await ws.send_json({"role": "novelty", "delta": token})
```

### 客户端
```tsx
const [messages, setMessages] = useState<Message[]>([]);

ws.onmessage = (ev) => {
  const { role, delta } = JSON.parse(ev.data);
  setMessages((prev) => {
    const last = prev[prev.length - 1];
    if (last && last.role === role) {
      // 追加到当前气泡
      return [...prev.slice(0, -1), { ...last, content: last.content + delta }];
    }
    // 新气泡
    return [...prev, { role, content: delta }];
  });
};
```

---

## 10.10 部署

### 开发
```bash
# 后端
uvicorn co_scientist.api.main:app --reload --port 8000

# 前端
cd frontend && pnpm dev
```

### 生产(Docker Compose)
```yaml
services:
  backend:
    build: ./backend
    command: uvicorn co_scientist.api.main:app --host 0.0.0.0 --port 8000 --workers 4
    env_file: .env
    depends_on: [postgres, redis]

  frontend:
    build: ./frontend
    command: pnpm start
    ports: ["3000:3000"]

  worker:
    build: ./backend
    command: celery -A co_scientist.tasks worker -l info
    env_file: .env

  postgres: ...
  redis: ...
```

### 反向代理(Nginx)
```nginx
server {
  listen 80;
  location /api { proxy_pass http://backend:8000; }
  location /ws { proxy_pass http://backend:8000; proxy_http_version 1.1; proxy_set_header Upgrade $http_upgrade; proxy_set_header Connection "upgrade"; }
  location /   { proxy_pass http://frontend:3000; }
}
```

---

## 📝 面试常见问题

1. **FastAPI vs Flask?**
   - FastAPI:async 原生、自动文档、Pydantic 校验、性能更好
   - Flask:简单成熟,生态广

2. **长任务如何处理?**
   - BackgroundTasks 简单,Celery 分布式

3. **WebSocket 何时用?**
   - 实时双向通信:聊天、流式 token、进度推送

4. **CORS 怎么解决?**
   - 后端 CORSMiddleware 或前端 rewrites 代理

5. **Next.js Server vs Client Component?**
   - Server 默认,无 state/effect;Client 加 "use client" 才能用 hook

6. **Celery 用什么 broker?**
   - Redis 简单,RabbitMQ 复杂场景更可靠

---

## 🎯 练手题

1. 把 BackgroundTasks 换成 Celery + Redis
2. 用 React Flow 画研究分叉树
3. 实现 token 级流式批判圆桌(WebSocket 推送每个 reviewer 的逐 token 输出)
4. 加一个 Cytoscape.js 组件,渲染 `data/outputs/knowledge_graph.graphml`

---

## ✅ 练手题参考答案

### 答案 1:Celery + Redis

`backend/co_scientist/tasks.py`:
```python
from celery import Celery
from co_scientist.graph import run_pipeline
from co_scientist.config import settings

celery_app = Celery("co_scientist", broker=settings.REDIS_URL, backend=settings.REDIS_URL)

@celery_app.task(bind=True)
def run_pipeline_task(self, question: str, execution_mode: str = "generate_only"):
    self.update_state(state="PROGRESS", meta={"stage": "starting"})
    state = run_pipeline(raw_question=question, execution_mode=execution_mode)
    return {
        "pico": state.get("pico"),
        "meta_decision": state.get("meta_decision"),
        "latex_path": state.get("paper_draft", {}).get("latex_path"),
    }
```

API 层换成异步投递:
```python
# api/main.py
from co_scientist.tasks import run_pipeline_task

@app.post("/runs")
async def create_run(req: RunRequest):
    async_result = run_pipeline_task.delay(req.question, req.execution_mode)
    return {"task_id": async_result.id}

@app.get("/runs/{task_id}")
async def get_run(task_id: str):
    r = run_pipeline_task.AsyncResult(task_id)
    return {"status": r.status, "result": r.result if r.ready() else r.info}
```

启动 worker:`celery -A co_scientist.tasks worker -l info -c 2`

要点:
- **`-c 2` 并发 2 个 worker**:LangGraph 跑一次主流程约 5-15 分钟,并发过高会让 LLM token 账单爆
- **结果存 Redis 而不是 DB**:大 state 太重,只存关键字段
- BackgroundTasks 挂在 FastAPI 进程里,重启就丢;Celery + Redis 能跨进程恢复

### 答案 2:React Flow 分叉树

```tsx
// frontend/src/app/forks/page.tsx
"use client";
import ReactFlow, { Background } from "reactflow";
import "reactflow/dist/style.css";
import useSWR from "swr";

const fetcher = (u: string) => fetch(u).then(r => r.json());

export default function ForksPage() {
  const { data } = useSWR("/api/forks", fetcher);
  if (!data) return <div>Loading...</div>;

  // 后端 /api/forks 返回 list_forks() 结果;这里用 Dagre 布局或简单纵向排布
  const nodes = data.map((f: any, i: number) => ({
    id: f.fork_id,
    data: { label: `${f.fork_id.slice(0,6)} (${f.final_rating.toFixed(1)})` },
    position: { x: (i % 5) * 180, y: Math.floor(i / 5) * 120 },
    style: { background: f.status === "done" ? "#d4f7d4" : "#fff6d4" },
  }));
  const edges = data
    .filter((f: any) => f.parent_fork_id)
    .map((f: any) => ({ id: `${f.parent_fork_id}->${f.fork_id}`, source: f.parent_fork_id, target: f.fork_id }));

  return (
    <div style={{ height: "80vh" }}>
      <ReactFlow nodes={nodes} edges={edges} fitView><Background /></ReactFlow>
    </div>
  );
}
```

后端加路由:
```python
@app.get("/api/forks")
async def list_forks_api():
    from co_scientist.modules.m8_replay import ForkManager
    return ForkManager().list_forks()
```

要点:
- **React Flow 适合"节点固定、需要拖拽/缩放"的树**,比 D3 易上手
- 节点布局建议上 `dagre` 自动排版,别手写 x/y 坐标
- 状态用 color-coding 一眼能看出哪些分支还在跑

### 答案 3:token 流式批判圆桌

后端 WebSocket:
```python
# api/main.py
import asyncio, json
from fastapi import WebSocket
from co_scientist.modules.m4_critique import ALL_REVIEWERS, review_proposal_stream

@app.websocket("/ws/critique")
async def ws_critique(ws: WebSocket):
    await ws.accept()
    req = await ws.receive_json()
    question = req["question"]

    async def run_one(persona):
        async for chunk in review_proposal_stream(persona, question, ""):
            await ws.send_json({"reviewer": persona.name, "delta": chunk})
        await ws.send_json({"reviewer": persona.name, "done": True})

    await asyncio.gather(*[run_one(p) for p in ALL_REVIEWERS])
    await ws.close()
```

要给 `review_proposal` 做流式版(内部改调 `chat_stream`):
```python
async def review_proposal_stream(persona, q, method_summary):
    llm = get_llm(persona.model_role)
    buf = ""
    for delta in llm.chat_stream(messages=[...], purpose=f"m4_stream_{persona.name}"):
        buf += delta
        yield delta
    # 结束后可做 JSON 解析存卡
```

前端:
```tsx
const ws = new WebSocket("ws://localhost:8000/ws/critique");
const boards = { novelty: "", methodology: "", ... };
ws.onmessage = (e) => {
  const { reviewer, delta, done } = JSON.parse(e.data);
  if (delta) { boards[reviewer] += delta; render(); }
  if (done)  console.log(reviewer, "完成");
};
ws.send(JSON.stringify({ question: "..." }));
```

要点:
- **并发 5 个 reviewer 同时流式**,前端要用 5 个独立气泡,否则内容会错乱
- WebSocket 单连接足够,用 `reviewer` 字段区分,没必要开 5 个连接
- `chat_stream` 里要 `stream_options={"include_usage": True}` 才能在最后一个 chunk 拿到 tokens 计费

### 答案 4:Cytoscape.js 渲染知识图谱

后端加个 GraphML → JSON 的路由(Cytoscape 要 json 格式):
```python
import networkx as nx
@app.get("/api/kg")
async def kg():
    G = nx.read_graphml(settings.OUTPUT_DIR / "knowledge_graph.graphml")
    elements = []
    for n, d in G.nodes(data=True):
        elements.append({"data": {"id": n, "label": d.get("label", n)}})
    for u, v, d in G.edges(data=True):
        elements.append({"data": {"id": f"{u}-{v}", "source": u, "target": v, "label": d.get("relation", "")}})
    return {"elements": elements}
```

前端:
```tsx
"use client";
import { useEffect, useRef } from "react";
import cytoscape from "cytoscape";
import coseBilkent from "cytoscape-cose-bilkent";
cytoscape.use(coseBilkent);

export default function KG() {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    (async () => {
      const data = await fetch("/api/kg").then(r => r.json());
      cytoscape({
        container: ref.current,
        elements: data.elements,
        layout: { name: "cose-bilkent", animate: false },
        style: [
          { selector: "node", style: { label: "data(label)", "background-color": "#3b82f6" } },
          { selector: "edge", style: { label: "data(label)", "curve-style": "bezier",
            "target-arrow-shape": "triangle", "font-size": 9 } },
        ],
      });
    })();
  }, []);
  return <div ref={ref} style={{ width: "100%", height: "80vh" }} />;
}
```

要点:
- **cose-bilkent 布局**比默认 cose 好看很多,推荐学术图谱
- 节点 >500 时建议加 `headless: true` 预计算布局后再渲染,避免卡
- 关系 label 太多会挤,可以只在 hover 时显示
