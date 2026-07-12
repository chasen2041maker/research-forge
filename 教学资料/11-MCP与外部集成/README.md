# 11. MCP(Model Context Protocol)与外部集成

> **本章学什么**:2024.11 Anthropic 发布的工具/上下文互操作标准 MCP,以及本项目如何把 m2 检索源接成 MCP Server,实现"工具可被任何 MCP 兼容 Agent 复用"。

## 11.1 MCP 是什么,为什么火

### 一句话定义
**MCP** 就是"给 Agent 和工具之间定一个标准协议",类似"LSP 之于编辑器"、"USB 之于硬件"。

### 为什么会火
2023-2024 年 Agent 生态的痛点:
- **工具包装重复造轮子**:同一个 arXiv API 要为 LangChain 写一版、AutoGen 写一版、自家代码里再写一版
- **Agent 之间不互通**:你写的"查股价"工具,别人家的 Agent 用不了
- **Claude Desktop / Cursor / Zed 想"接个工具"**:得每家都做一遍适配

2024 年 11 月 Anthropic 发布 MCP,**一次性解决**这三个问题:
- 工具只写一次 MCP Server
- 任何 MCP 兼容的客户端直接用
- 官方 SDK(Python / TypeScript 等)开源

### 2025 年发生了什么
- OpenAI / Google 跟进支持 MCP
- GitHub / Slack / Linear / Notion / Postgres 都出了官方 MCP Server
- Cursor / Zed / Windsurf / Claude Desktop 都原生支持
- **已经是业界事实标准**(类比 2010 年的 REST API)

---

## 11.2 MCP 协议核心概念

### 三种角色

| 角色 | 做什么 | 本项目对应 |
|---|---|---|
| **MCP Server** | 暴露工具 / 资源 / prompt 给客户端 | `arxiv_server.py` 等 3 个 |
| **MCP Client** | 连接 Server,发起调用 | `mcp_client.py` |
| **MCP Host** | 承载 Client 的应用(如 Claude Desktop) | 我们的 m2 retriever 节点 |

### 三种 Transport(传输方式)

| Transport | 特点 | 适合 |
|---|---|---|
| **stdio** | 子进程 + 标准输入/输出通信 | 本地工具,零网络配置 |
| **SSE** | Server-Sent Events 单向流 | 旧版远程 Server(渐渐淘汰) |
| **Streamable HTTP** | 2025 新版,双向流 | 远程部署的生产 Server |

**本项目选 stdio**:
- 最简单、零端口管理
- Claude Desktop / Cursor 原生支持
- 测试时启子进程就跑,无需部署

### 三类可暴露的资源

| 类别 | 是什么 | 示例 |
|---|---|---|
| **Tools** | LLM 可主动调用的函数 | `search_arxiv(query)` |
| **Resources** | LLM 可按需读取的数据 | 一份 PDF、一个 DB schema |
| **Prompts** | 可复用的 prompt 模板 | "用审稿人视角评审这段文字" |

**本项目只用 Tools**(检索源本质就是函数),Resources / Prompts 是进阶话题。

---

## 11.3 本项目的 MCP 架构

### 目录结构
```
backend/co_scientist/modules/m2_retriever/
├── sources/                          ← 原有:进程内异步函数
│   ├── arxiv_src.py
│   ├── semantic_src.py
│   └── openalex_src.py
├── mcp_servers/                      ← 新增:独立 MCP Server
│   ├── __init__.py
│   ├── _common.py                    ← paper 序列化 + 工具描述模板
│   ├── arxiv_server.py
│   ├── semantic_scholar_server.py
│   └── openalex_server.py
├── mcp_client.py                     ← 新增:主程序用的 MCP Client
└── retriever.py                      ← 改造:按 settings.USE_MCP 选源
```

### 两种模式并存(feature flag 切换)

```
┌────────────────────────────────────────────────────────────────┐
│                    settings.USE_MCP = False(默认)              │
├────────────────────────────────────────────────────────────────┤
│   retriever.py                                                 │
│        └─ from sources import search_arxiv                     │
│             └─ (同进程直接调,~50ms)                           │
└────────────────────────────────────────────────────────────────┘

┌────────────────────────────────────────────────────────────────┐
│                    settings.USE_MCP = True                     │
├────────────────────────────────────────────────────────────────┤
│   retriever.py                                                 │
│        └─ from mcp_client import search_arxiv                  │
│             └─ stdio_client(StdioServerParameters)             │
│                  └─ 启动子进程: python -m ...arxiv_server       │
│                       └─ initialize + call_tool(search_arxiv)  │
│                            └─ ~1-2s(有子进程启动开销)          │
└────────────────────────────────────────────────────────────────┘
```

### 为什么做成 feature flag 而不是直接替换

三条理由:
1. **向后兼容**:原来的 `sources/` 代码一字不动,老单元测试全部继续通过
2. **性能可选**:本地开发不需要 MCP 的进程隔离,默认走直调更快
3. **渐进演进**:可以先开一个源走 MCP 验证稳定性,再全量切换

---

## 11.4 代码详解

### Server 端:一行装饰器注册工具

`arxiv_server.py` 的核心就 10 行:

```python
from mcp.server.fastmcp import FastMCP

mcp = FastMCP(name="co-scientist-arxiv")

@mcp.tool(description="Search arXiv for papers matching the query")
async def search_arxiv(query: str, max_results: int = 20) -> list[dict]:
    capped = max(1, min(int(max_results), 50))
    papers = await _search_arxiv_impl(query, max_results=capped)
    return [paper_to_json_safe(dict(p)) for p in papers]

if __name__ == "__main__":
    mcp.run(transport="stdio")
```

**FastMCP 自动做三件事**:
1. 从函数签名抽输入 schema(query: str, max_results: int)
2. 从 docstring / description 参数抽工具说明
3. 建立 stdio 双向通道,把协议消息路由到装饰过的函数

### Client 端:会话生命周期管理

`mcp_client.py` 的核心用 async context manager 管 session:

```python
@asynccontextmanager
async def _mcp_session(source: str):
    params = _server_params_for(source)
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()  # ← MCP 协议握手
            yield session

async def search_arxiv(query: str, max_results: int = 20):
    async with _mcp_session("arxiv") as session:
        result = await session.call_tool(
            "search_arxiv",
            arguments={"query": query, "max_results": max_results},
        )
        # 解析 content(通常是单个 TextContent,内含 JSON 字符串)
        papers_raw = json.loads(result.content[0].text)
        return [Paper(**p) for p in papers_raw]
```

**生命周期关键点**:
- `stdio_client` 启子进程,返回双向管道
- `ClientSession.initialize()` 是 **MCP 握手**,交换协议版本和能力
- `call_tool` 发起工具调用,等 Server 返回
- 退出 `async with` 时自动关 session + kill 子进程

### Retriever 端:feature flag 分流

`retriever.py` 顶部:
```python
from co_scientist.config import settings

if settings.USE_MCP:
    from co_scientist.modules.m2_retriever.mcp_client import (
        search_arxiv, search_openalex, search_semantic_scholar,
    )
else:
    from co_scientist.modules.m2_retriever.sources import (
        search_arxiv, search_openalex, search_semantic_scholar,
    )
```

下游 `asyncio.gather(search_arxiv(q), ...)` 一字不改。**flag 入口分流,路径透明**。

---

## 11.5 怎么测试

### 档 1:纯 Python 逻辑(不启子进程)
```bash
pytest tests/test_mcp_integration.py::test_paper_to_json_safe_strips_raw
pytest tests/test_mcp_integration.py::test_arxiv_server_tool_invokable
```
→ 验证 Server 函数本身的业务逻辑(序列化、参数 cap)

### 档 2:协议层握手(启子进程,不联网)
```bash
pytest tests/test_mcp_integration.py::test_mcp_client_list_tools_works_for_all_three
```
→ 验证 3 个 Server 都能被 Client 发现、协议握手通
→ 这一档过,说明 MCP 基础设施是对的

### 档 3:端到端真实检索(需联网)
```bash
pytest tests/test_mcp_integration.py --run-net
```
→ 真调 arXiv API,验证完整链路

---

## 11.6 怎么接到 Claude Desktop(面试 demo 杀手锏)

1. 在你的 `claude_desktop_config.json` 加一段:

    **Mac/Linux**:`~/.config/Claude/claude_desktop_config.json`
    **Windows**:`%APPDATA%\\Claude\\claude_desktop_config.json`

    ```json
    {
      "mcpServers": {
        "co-scientist-arxiv": {
          "command": "python",
          "args": [
            "-m",
            "co_scientist.modules.m2_retriever.mcp_servers.arxiv_server"
          ],
          "env": {
            "PYTHONPATH": "/path/to/agent3/backend"
          }
        }
      }
    }
    ```

2. 重启 Claude Desktop

3. 在对话框里直接说:"帮我用 arXiv 搜一下 RAG 相关论文",Claude 会自动发现并调用你的 `search_arxiv` 工具

**面试讲法**:
> "我把检索源独立成了 MCP Server,你可以把它挂到你自己的 Claude Desktop 里用 —— 这不是 Demo 里固定的演示,是真正能跨项目复用的工具。"

---

## 11.7 常见问题

### Q1:MCP 和 LangChain 的 Tool 有什么区别?
- **LangChain Tool**:LangChain 生态内部的工具抽象,只能被 LangChain Agent 用
- **MCP**:跨框架、跨语言的**协议标准**。你的 MCP Server 可以被 LangChain、AutoGen、Claude Desktop、Cursor 全部使用

### Q2:每次调用都启动子进程,不慢吗?
教学版确实慢(1-2 秒开销)。生产级有三种优化:
1. **Session 池**:复用子进程,类似数据库连接池
2. **Streamable HTTP**:远程部署一次,客户端直接 HTTP 调
3. **多工具合一**:把 3 个源合成一个 Server,只启一个子进程

### Q3:MCP Server 能做认证/权限吗?
- stdio 模式:谁能启子进程谁就有权限(本地信任)
- HTTP 模式:标准 HTTP Auth(Bearer Token / OAuth)
- **面试讲点**:MCP Gateway 模式(在多 Server 前加一个权限网关)是 C 方向的开源机会

### Q4:和 OpenAI Function Calling 什么区别?
- **Function Calling**:只是 OpenAI API 的一个**调用参数**,工具定义和 LLM 绑死
- **MCP**:**协议层**标准,工具独立部署。Function Calling 可以作为 MCP Client 的底层实现

### Q5:MCP 支持异步 / 流式?
- Tool 结果:支持(Server 端用 async def 即可)
- 流式响应:2025 版 Streamable HTTP transport 原生支持

---

## 11.8 面试讲点速查

| 面试官问 | 你答 |
|---|---|
| MCP 是什么? | 2024.11 Anthropic 发布的 Agent 工具/上下文互操作协议,2025 业界事实标准 |
| 为什么接这个? | 让我的检索源可以被 Claude Desktop / Cursor / 其他团队 Agent 直接复用,避免重复造轮子 |
| 接了几层? | 3 个 MCP Server(arxiv / S2 / OpenAlex)+ 1 个 Client + feature flag 切换 |
| 怎么保证向后兼容? | settings.USE_MCP 默认 false,原 sources 代码一字未改 |
| 测试怎么做? | 三档:纯 Python 逻辑 / 协议握手(启子进程)/ 端到端联网 |
| 能演示吗? | 可以,我把 arxiv_server 挂到我自己的 Claude Desktop 里就能用 |
| 性能怎么样? | stdio 模式每次调用多 1-2 秒子进程启动开销,生产版可以用 session pool 或 HTTP transport |

---

## 11.9 进阶练手

1. **Session pool**:改写 mcp_client,保持每个 source 的子进程常驻,并发调用共享 session
2. **加一个新源**:CORE / PubMed / Google Scholar,照 arxiv_server 模板写,5 分钟搞定
3. **自己写一个 MCP Gateway**:聚合 3 个 Server 成单入口,加权限和限流(对应新项目方向 C1)
4. **Resources**:把论文 PDF 暴露成 MCP Resource,让客户端按 URI 拉
5. **Prompts**:把 m4 的 Reviewer prompt 做成 MCP Prompt,让客户端一键复用"审稿人视角"

---

## 11.10 参考资料

- [MCP 官方文档](https://modelcontextprotocol.io/)
- [MCP Python SDK](https://github.com/modelcontextprotocol/python-sdk)
- [Anthropic 介绍博客 (2024.11)](https://www.anthropic.com/news/model-context-protocol)
- [Awesome MCP Servers](https://github.com/modelcontextprotocol/servers)
- 本项目的 MCP Inspector 调试命令:
  ```bash
  npx @modelcontextprotocol/inspector python -m co_scientist.modules.m2_retriever.mcp_servers.arxiv_server
  ```
