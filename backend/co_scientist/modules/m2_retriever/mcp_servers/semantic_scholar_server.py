"""
============================================================
 Semantic Scholar MCP Server(mcp_servers/semantic_scholar_server.py)
============================================================

🎓 教学目标
    和 arxiv_server 同构,这个 Server 暴露 Semantic Scholar 的搜索能力。

    本文件几乎是 arxiv_server.py 的镜像,刻意写得**结构一致**,
    读者只需要对比两者就能理解:"新加一个数据源 = 复制一份 + 改 3 行"。

💡 三个 Server 为什么不用"动态工厂"合并成一个文件
    技术上可以写成:
        def make_server(source_name, search_fn): ...
    但每个 Server 独立文件的好处:
      1. 可以独立启动(python -m ...xxx_server 可以一键起某一个)
      2. 未来如果某个源要加自己特有的 tool(比如 openalex 的"按 DOI 精确查"),
         直接在那个 server 文件里加 @mcp.tool 即可,不用动别人
      3. Claude Desktop 的配置里每个 MCP Server 是独立条目,拆文件刚好匹配

💡 与 arxiv_server 的差异
    - source name 不同("co-scientist-semantic-scholar")
    - 调用的底层实现不同(search_semantic_scholar)
    - 工具描述中的速率限制/特性说明不同(S2 有更丰富的引用网络字段)
    其余协议适配层完全一致。

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
from co_scientist.modules.m2_retriever.sources.semantic_src import (  # noqa: E402
    search_semantic_scholar as _search_semantic_impl,
)


mcp = FastMCP(name="co-scientist-semantic-scholar")


@mcp.tool(description=make_search_tool_description(
    "Semantic Scholar",
    rate_limit_hint="100 requests/5min without API key, 1 request/sec with key",
    extra="Good for citation graph queries; provides cited_by_count.",
))
async def search_semantic_scholar(query: str, max_results: int = 20) -> list[dict]:
    """
    Search Semantic Scholar for papers matching the query.

    Args:
        query: Natural language search query.
        max_results: Maximum number of papers to return (default 20, max 50).

    Returns:
        List of paper dicts with Semantic Scholar's richer fields
        (cited_by_count is usually non-zero here, unlike arXiv).

    ▍和 arXiv Server 的"职责等价"说明
        两个 Server 对外契约完全一致:输入 (query, max_results) → 输出 list[paper_dict]。
        这样上游 mcp_client 可以用同一份代码调所有 3 个 Server,不用按源写特殊逻辑。
    """
    capped = max(1, min(int(max_results), 50))
    papers = await _search_semantic_impl(query, max_results=capped)
    return [paper_to_json_safe(dict(p)) for p in papers]


def main() -> None:
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
