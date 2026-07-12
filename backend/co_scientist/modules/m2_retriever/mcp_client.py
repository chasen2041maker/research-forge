"""
============================================================
 MCP 客户端(m2_retriever/mcp_client.py)
============================================================

🎓 教学目标
    让主程序(retriever.py)能"像调用本地函数一样"调用独立的 MCP Server。
    本文件封装了 MCP Client 的生命周期:启动子进程 → 握手 → 调用工具
    → 关闭,对外暴露和 `sources/` 下同签名的异步搜索函数。

💡 为什么要写这一层
    MCP 官方 Client API 是"会话级别"的:你要拿到一个 session,
    在 session 里 list_tools、call_tool,最后 close。直接让业务代码
    处理会话会污染主流程。我们包一层:
      - 对外暴露 search_arxiv_via_mcp(query, max_results) 这种同步友好接口
      - 内部用 async context manager 管好 session 生命周期
      - 调用方几乎感觉不到"这其实是跨进程 RPC"

💡 三种 session 管理策略
    1. **每次调用新建 session**:最简单,但每次起子进程 + initialize 很慢(~1s)
    2. **全局单例长连接**:快,但要处理进程崩溃重连、并发冲突
    3. **connection pool**:最像生产数据库连接,写起来复杂
    教学版选 1,代价是"MCP 模式比直调慢 1-2 秒",但代码简单清晰。
    生产版可升级到 2 或 3,接口不变。

📌 容错:MCP 服务起不来怎么办
    - Server 子进程启动失败 → 把错误包装成 list[Paper]=[] 返回,上游按"单源失败"处理
    - Server 启动成功但 call_tool 超时 → 同上
    这等价于原 retriever.py 里 `return_exceptions=True` 的兜底策略,
    保证"某个源挂了,整条检索流程仍能继续"。

------------------------------------------------------------
"""

from __future__ import annotations

import json
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncIterator

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from co_scientist.state import Paper
from co_scientist.utils import logger


# ------------------------------------------------------------
# 3 个 MCP Server 的启动方式
# 为什么硬编码这三个:
#   这层是"当前项目对 MCP 的接入适配",不是通用 MCP Client 库。
#   未来加新源就在这里加一条 entry,一行代码。
#   如果要做成通用,抽到 settings 的 dict 配置即可。
# ------------------------------------------------------------
def _server_params_for(source: str) -> StdioServerParameters:
    """
    按 source 名字拿到对应 MCP Server 的启动参数。

    命令形如:python -m co_scientist.modules.m2_retriever.mcp_servers.arxiv_server
    子进程继承当前 Python 解释器路径(sys.executable),避免环境隔离时
    找错 Python 版本导致 import 失败。
    """
    mapping = {
        "arxiv": "co_scientist.modules.m2_retriever.mcp_servers.arxiv_server",
        "semantic_scholar": "co_scientist.modules.m2_retriever.mcp_servers.semantic_scholar_server",
        "openalex": "co_scientist.modules.m2_retriever.mcp_servers.openalex_server",
    }
    if source not in mapping:
        raise ValueError(f"未知 MCP source: {source}(合法值: {list(mapping)})")
    return StdioServerParameters(
        command=sys.executable,  # 用当前解释器,避免跨 venv 版本不一致
        args=["-m", mapping[source]],
        # 让子进程能找到 backend/ 作为 Python 根路径
        env={"PYTHONPATH": str(Path(__file__).resolve().parents[3])},
    )


# ------------------------------------------------------------
# 会话生命周期(async context manager)
# ------------------------------------------------------------
@asynccontextmanager
async def _mcp_session(source: str) -> AsyncIterator[ClientSession]:
    """
    启动对应 MCP Server 子进程,建立 session,yield 给调用方用。
    退出 with 时自动 close,子进程被清理。

    ▍为什么要 asynccontextmanager
        - 保证即使调用方抛异常,session 和子进程都能被干净关闭
        - 调用方用 `async with _mcp_session("arxiv") as session:` 读起来像常规资源管理
    """
    params = _server_params_for(source)
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            # initialize 是 MCP 协议的第一步握手,交换能力/版本信息
            # 必须调,否则后续 call_tool 会被 Server 拒绝
            await session.initialize()
            yield session


# ------------------------------------------------------------
# 核心调用:对 MCP Server 发起一次 tool 调用
# ------------------------------------------------------------
def _tool_name_for(source: str) -> str:
    """每个 Server 暴露的 tool 名字,和 @mcp.tool 装饰的函数同名。"""
    return {
        "arxiv": "search_arxiv",
        "semantic_scholar": "search_semantic_scholar",
        "openalex": "search_openalex",
    }[source]


async def _call_source_via_mcp(
    source: str,
    query: str,
    max_results: int,
) -> list[Paper]:
    """
    启动对应 MCP Server 子进程,调用 search 工具,解析返回。

    返回和 sources/*.py 下的同名函数一致(list[Paper]),方便无缝替换。

    ▍错误降级策略
        任何异常(子进程启动失败、超时、协议错误)都被吞掉,返回空列表。
        理由:m2 上层用 asyncio.gather(return_exceptions=True) 已经做了
        "单源失败 = 跳过这一源"的兜底,我们保持同样的"失败=空列表"语义。
    """
    tool_name = _tool_name_for(source)
    try:
        async with _mcp_session(source) as session:
            # call_tool 返回一个 CallToolResult,其内容在 .content(list of ContentBlock)
            # 每个 ContentBlock 可能是 text / image / resource,我们的 Server 只会返回
            # text 类型(JSON 序列化后的 list[dict])
            result = await session.call_tool(
                tool_name,
                arguments={"query": query, "max_results": max_results},
            )
            # 解析 content 拿到 JSON 字符串
            texts: list[str] = []
            for block in result.content:
                # ContentBlock 的 text 字段在 TextContent 类型里
                text = getattr(block, "text", None)
                if text:
                    texts.append(text)
            if not texts:
                logger.warning("[mcp-{}] 返回空 content", source)
                return []
            # Server 返回的是 list[dict],被 MCP 序列化成一个大字符串
            # 这里反序列化回来,再按 Paper TypedDict 的形状构造
            papers_raw = json.loads(texts[0])
            return [Paper(**p) for p in papers_raw]  # type: ignore[typeddict-item]
    except Exception as e:
        logger.error("[mcp-{}] 调用失败 q={}: {}", source, query[:40], e)
        return []


# ------------------------------------------------------------
# 对外 API:和 sources/*.py 同签名的异步函数
#
# 这是本文件最重要的契约:
#   retriever.py 只要把 `from ...sources import search_arxiv` 换成
#   `from ...mcp_client import search_arxiv`,其他代码一行都不用动。
# ------------------------------------------------------------
async def search_arxiv(query: str, max_results: int = 20) -> list[Paper]:
    """通过 MCP 调 arxiv_server。同签名于 sources.arxiv_src.search_arxiv。"""
    return await _call_source_via_mcp("arxiv", query, max_results)


async def search_semantic_scholar(query: str, max_results: int = 20) -> list[Paper]:
    """通过 MCP 调 semantic_scholar_server。"""
    return await _call_source_via_mcp("semantic_scholar", query, max_results)


async def search_openalex(query: str, max_results: int = 20) -> list[Paper]:
    """通过 MCP 调 openalex_server。"""
    return await _call_source_via_mcp("openalex", query, max_results)


# ------------------------------------------------------------
# 工具函数:列出所有 MCP Server 的工具(调试用)
# ------------------------------------------------------------
async def list_mcp_tools(source: str) -> list[dict[str, Any]]:
    """
    连上指定 Server,列出它暴露的工具。

    用途:
      - 单元测试:断言 tool 名字和我们期望的一致
      - 调试:怀疑 Server 没正确注册工具时手动查一下
      - 面试演示:把这个函数接进 CLI,直接 `cli mcp-list arxiv` 能看到
    """
    try:
        async with _mcp_session(source) as session:
            resp = await session.list_tools()
            return [
                {
                    "name": t.name,
                    "description": t.description,
                    "inputSchema": t.inputSchema,
                }
                for t in resp.tools
            ]
    except Exception as e:
        logger.error("[mcp-{}] list_tools 失败: {}", source, e)
        return []
