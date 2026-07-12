"""
============================================================
 arXiv MCP Server(mcp_servers/arxiv_server.py)
============================================================

🎓 教学目标
    把 `sources/arxiv_src.py` 的异步搜索函数包装成一个独立的 MCP Server,
    通过 **stdio transport** 对外暴露一个 `search_arxiv` 工具。

    这是一个"最小可用、可独立启动"的 MCP Server,它做的事其实就一件:
      - 收到 MCP 客户端的 tool call → 调底层 search_arxiv 函数 → 返回 JSON

📌 为什么用 FastMCP(来自 mcp 官方 SDK)
    mcp 官方 Python SDK 提供两套 API:
      1. `Server` 类:底层协议级别,要自己处理 list_tools / call_tool 请求
      2. `FastMCP` 类:装饰器式,一行 @mcp.tool() 就能注册工具,适合快速上手

    教学版当然选 FastMCP,用最少的代码体现 MCP 的核心概念。
    生产级需要更细粒度的控制(比如自定义 capabilities、资源管理),再换 Server。

💡 为什么 stdio 而不是 SSE / HTTP
    MCP 定义了 3 种 transport:
      - stdio:子进程通过标准输入/输出通信,最简单,适合本机 Agent
      - SSE:单向流,适合 Web 场景
      - HTTP(MCP 2025 新版):双向,适合远程部署
    本教学项目默认 stdio:零端口管理、零网络配置,Claude Desktop / Cursor 原生支持。

📌 怎么独立启动本 Server
    # 方式 1:Python 模块方式(推荐)
    python -m co_scientist.modules.m2_retriever.mcp_servers.arxiv_server

    # 方式 2:直接跑文件
    cd backend && python co_scientist/modules/m2_retriever/mcp_servers/arxiv_server.py

    启动后程序会等待 stdin 的 MCP 协议消息(JSON-RPC over stdio),
    不会有常规"服务启动成功"的输出 —— 这是 stdio 模式的正常表现。

📌 怎么验证它真的是 MCP Server
    用官方 MCP Inspector(一个调试工具):
        npx @modelcontextprotocol/inspector python -m co_scientist.modules.m2_retriever.mcp_servers.arxiv_server
    打开浏览器能看到 list_tools / call_tool 的交互界面。

💡 本文件几乎是"骨架代码" —— 真正的业务逻辑在 sources/arxiv_src.py
    MCP Server 本身只是**协议适配层**,一旦理解了它的作用,
    semantic_scholar_server / openalex_server 就是照葫芦画瓢。

------------------------------------------------------------
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

# 关键:让本文件被 `python arxiv_server.py` 直接跑时也能 import 到 co_scientist 包
# (因为 MCP Server 常以子进程方式被启动,PYTHONPATH 不一定被继承)
# 这是教学项目对"能跑起来"的强制要求 —— 面试演示时如果 import 错了太尴尬
_BACKEND_DIR = Path(__file__).resolve().parents[4]  # .../agent3/backend
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from mcp.server.fastmcp import FastMCP  # noqa: E402

from co_scientist.modules.m2_retriever.mcp_servers._common import (  # noqa: E402
    make_search_tool_description,
    paper_to_json_safe,
)
from co_scientist.modules.m2_retriever.sources.arxiv_src import (  # noqa: E402
    search_arxiv as _search_arxiv_impl,
)


# ------------------------------------------------------------
# 创建 FastMCP 实例
# name 会作为 Server 的标识,MCP 客户端看到的就是这个字符串
# ------------------------------------------------------------
mcp = FastMCP(name="co-scientist-arxiv")


# ------------------------------------------------------------
# 注册工具:search_arxiv
#
# @mcp.tool() 装饰器做三件事:
#   1. 自动从函数签名抽取输入 schema(query: str, max_results: int)
#   2. 自动从函数 docstring 抽取描述
#   3. 当 MCP 客户端调用本工具时,自动路由到这个 Python 函数
#
# 💡 FastMCP 默认用 Pydantic 做参数校验,所以类型注解必须严格:
#    str / int / bool / list[str] / 等基础类型是安全的,
#    自定义 class 要写 pydantic BaseModel。
# ------------------------------------------------------------
@mcp.tool(description=make_search_tool_description(
    "arXiv",
    rate_limit_hint="~3 seconds between requests (arXiv 软限流)",
    extra="For AI/ML/CS research topics. Does not provide citation counts.",
))
async def search_arxiv(query: str, max_results: int = 20) -> list[dict]:
    """
    Search arXiv for papers matching the query.

    Args:
        query: Natural language search query (English recommended).
        max_results: Maximum number of papers to return (default 20, max 50).

    Returns:
        List of paper dicts with fields: id, title, abstract, authors, year, url, doi.

    ▍教学说明:这里是 MCP 工具的"门面函数"
        - 真正的业务逻辑在 sources.arxiv_src.search_arxiv(用 _search_arxiv_impl 别名)
        - 本函数只做三件事:参数校验、调用实现、序列化返回
        - 这样分层是 MCP 官方推荐的做法:Server 层只管协议,业务层可以被其他
          人以任何方式调用(不一定非要走 MCP)
    """
    # 对外输入做一次上界保护,防止客户端传个 max_results=9999 把 arXiv 服务拖垮
    capped = max(1, min(int(max_results), 50))

    papers = await _search_arxiv_impl(query, max_results=capped)

    # 序列化:Paper 虽然本身是 dict,但里面可能混入不 JSON 安全的字段
    # 统一过一遍 paper_to_json_safe,只保留协议契约里的字段
    return [paper_to_json_safe(dict(p)) for p in papers]


# ------------------------------------------------------------
# 入口:stdio 模式启动
#
# mcp.run() 内部:
#   1. 建立 stdin/stdout 双向通道
#   2. 监听 MCP 协议消息(initialize / list_tools / call_tool / ...)
#   3. 路由到对应的 @mcp.tool 函数
#   4. 把返回值序列化成 MCP Response 写回 stdout
#
# 这个循环一直跑,直到 stdin 关闭(父进程退出或人为 Ctrl+C)
# ------------------------------------------------------------
def main() -> None:
    """本 Server 的 stdio 入口。"""
    # ▍为什么不 asyncio.run(mcp.run())
    #   FastMCP.run() 本身是同步函数,内部会建 event loop 并跑异步主循环。
    #   如果外层再套 asyncio.run,会变成"event loop 里套 event loop",
    #   Python 会直接抛 RuntimeError: asyncio.run() cannot be called from a running event loop。
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
