"""
============================================================
 m2 检索源的 MCP Server 包(mcp_servers/)
============================================================

🎓 这是什么
    把原来散在 `sources/` 下的 3 个异步函数(arxiv / semantic / openalex)
    独立成 3 个 **MCP Server 子进程**,符合 Anthropic 2024 年 11 月发布的
    Model Context Protocol 标准。

📌 为什么要做这一层
    1. **跨项目复用**:任何支持 MCP 的客户端(Claude Desktop、Cursor、Zed、
       你团队同事的其他 Agent)都能直接挂我们的检索源,无需改任何代码。
    2. **进程隔离**:某个源(比如 arXiv 服务挂了)导致进程卡死,
       不会影响主程序的其他节点。
    3. **工具层 vs 业务层解耦**:工具可以独立部署、独立扩容、独立升级;
       业务层只跟"MCP 协议"这个稳定接口打交道。
    4. **对齐 2026 前沿架构**:MCP 已经是业界事实标准(OpenAI、Google 均跟进),
       会用 MCP 是 Agent 工程师 2026 简历的加分项。

💡 两种模式并存的设计决策
    - 默认模式:主程序直接 import `sources/arxiv_src.py` 的异步函数
      → 零额外进程,适合本地开发和低门槛教学
    - MCP 模式(settings.USE_MCP=True):主程序通过 `mcp_client.py`
      启动子进程,按 MCP 协议对话
      → 体现前沿架构,但需要额外依赖(`mcp` SDK)
    两种模式通过 feature flag 切换,保证向后兼容。

📂 目录结构
    mcp_servers/
    ├── __init__.py              ← 本文件(总说明)
    ├── _common.py               ← 共用的 paper→dict 序列化和错误处理
    ├── arxiv_server.py          ← 独立可启动的 arXiv MCP Server
    ├── semantic_scholar_server.py
    └── openalex_server.py

📌 怎么单独启动一个 Server 测试
    python -m co_scientist.modules.m2_retriever.mcp_servers.arxiv_server
    (然后用 MCP Inspector 工具连接,或者从 Claude Desktop 的 settings 里挂上)

📌 怎么在 Claude Desktop 里挂这个 Server
    编辑 ~/.config/Claude/claude_desktop_config.json(Mac/Linux)
    或 %APPDATA%\\Claude\\claude_desktop_config.json(Windows):
    {
      "mcpServers": {
        "co-scientist-arxiv": {
          "command": "python",
          "args": ["-m", "co_scientist.modules.m2_retriever.mcp_servers.arxiv_server"]
        }
      }
    }

------------------------------------------------------------
"""
