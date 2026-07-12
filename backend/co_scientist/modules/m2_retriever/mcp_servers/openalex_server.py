"""
============================================================
 OpenAlex MCP Server(mcp_servers/openalex_server.py)
============================================================

🎓 教学目标
    同构复刻 arxiv_server / semantic_scholar_server,把 OpenAlex 搜索
    通过 MCP 协议对外暴露。

💡 OpenAlex 的特点
    - 覆盖面最广(跨学科、包含书籍/专利/机构等)
    - 不要 API Key(对匿名 IP 有速率限制但宽松)
    - 返回**倒排摘要**(inverted index 格式),底层 search_openalex
      已经在 openalex_src._decode_inverted_abstract 里解码了,我们不用管

💡 为什么 3 个文件结构几乎一样
    这就是 MCP 的价值 —— 协议标准化让"新加一个数据源"的工作被压缩到:
      1. 复制一份 server 文件
      2. 改 name
      3. 改 import 的底层实现
      4. 改工具描述
    不用写新的客户端代码,上游不感知任何变化。

    如果你未来想接 Google Scholar / PubMed / CORE,就照这个模板再写一个。

------------------------------------------------------------
"""

from __future__ import annotations

import sys
from pathlib import Path

_BACKEND_DIR = Path(__file__).resolve().parents[4]
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from mcp.server.fastmcp import FastMCP  # noqa: E402

from co_scientist.modules.m2_retriever.mcp_servers._common import (  # noqa: E402
    make_search_tool_description,
    paper_to_json_safe,
)
from co_scientist.modules.m2_retriever.sources.openalex_src import (  # noqa: E402
    search_openalex as _search_openalex_impl,
)


mcp = FastMCP(name="co-scientist-openalex")


@mcp.tool(description=make_search_tool_description(
    "OpenAlex",
    rate_limit_hint="10 req/sec anonymous, 100 req/sec with polite email in User-Agent",
    extra="Broadest coverage across disciplines; includes cited_by_count and concepts.",
))
async def search_openalex(query: str, max_results: int = 20) -> list[dict]:
    """
    Search OpenAlex (cross-disciplinary scholarly database) for papers.

    Args:
        query: Natural language search query.
        max_results: Maximum number of papers to return (default 20, max 50).

    Returns:
        List of paper dicts with fields normalized to our Paper schema.
        Note: OpenAlex's abstract is decoded from inverted-index format
        by the underlying source function; we receive plain text here.
    """
    capped = max(1, min(int(max_results), 50))
    papers = await _search_openalex_impl(query, max_results=capped)
    return [paper_to_json_safe(dict(p)) for p in papers]


def main() -> None:
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
