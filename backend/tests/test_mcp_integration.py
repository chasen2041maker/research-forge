"""
============================================================
 MCP 集成测试(tests/test_mcp_integration.py)
============================================================

🎓 教学目标
    测试 MCP Server + Client 的最小闭环。

📌 测试策略(分三档)

    档 1. **Server 内部逻辑**(不启子进程,不联网)
        直接 import server 的 tool 函数,把底层 search_* 打桩掉,
        验证 MCP 层的"参数校验 + 序列化"代码是对的。
        → 默认跑,CI 必过

    档 2. **Client + Server 真 stdio 握手**(启子进程,不联网)
        真的启动子进程,走完 initialize + list_tools,验证协议层通。
        底层 search 打桩成返回固定 Paper,避免联网不稳定。
        → 默认跑

    档 3. **端到端真实检索**(启子进程 + 联网)
        真调 arXiv API,验证完整链路。有网络才通过。
        → 默认 skip,需要 pytest --run-net 才跑(避免 CI 被 arXiv 限流卡住)

💡 为什么不合并成"统一的一个 e2e 测试"
    分档能快速定位问题:
      - 档 1 失败 → 纯 Python 逻辑 bug
      - 档 2 失败 → MCP 协议/子进程有问题
      - 档 3 失败 → 网络或 arXiv API 有问题
    如果只有档 3,每次红都要猜问题出在哪层。
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest


# ============================================================
# 档 1:Server 内部逻辑(不启子进程)
# ============================================================


def test_paper_to_json_safe_strips_raw() -> None:
    """paper_to_json_safe 应该丢掉 raw 字段,避免跨进程序列化炸掉。"""
    from co_scientist.modules.m2_retriever.mcp_servers._common import paper_to_json_safe

    inp = {
        "id": "2305.12345v1",
        "title": "Test",
        "authors": ["Alice"],
        "year": 2024,
        "raw": object(),  # ← 不可 JSON 化的对象
    }
    out = paper_to_json_safe(inp)
    assert "raw" not in out
    assert out["title"] == "Test"
    assert out["authors"] == ["Alice"]


def test_paper_to_json_safe_authors_fallback() -> None:
    """authors 偶发不是 list 时(如单字符串)也要兜底成 list。"""
    from co_scientist.modules.m2_retriever.mcp_servers._common import paper_to_json_safe

    out = paper_to_json_safe({"id": "x", "title": "t", "authors": "Alice"})
    assert out["authors"] == ["Alice"]


def test_make_search_tool_description_format() -> None:
    """工具描述应包含 source 名 + 输入输出说明。"""
    from co_scientist.modules.m2_retriever.mcp_servers._common import (
        make_search_tool_description,
    )

    desc = make_search_tool_description("arXiv", rate_limit_hint="3s/req")
    assert "arXiv" in desc
    assert "query" in desc.lower()
    assert "3s/req" in desc


def test_arxiv_server_tool_invokable(monkeypatch: pytest.MonkeyPatch) -> None:
    """
    直接调用 server 里 @mcp.tool 装饰的函数,验证参数 cap + 序列化正确。
    这一档不启子进程,相当于测 server 代码的"业务逻辑"。
    """
    from co_scientist.modules.m2_retriever.mcp_servers import arxiv_server

    async def fake_search(query: str, max_results: int = 20) -> list[dict]:
        return [
            {"id": "1", "title": query, "authors": ["A"], "raw": object()}
        ] * 5

    monkeypatch.setattr(arxiv_server, "_search_arxiv_impl", fake_search)

    # mcp 1.27 的 @mcp.tool() 装饰器直接返回原函数(只是在 mcp 实例里登记),
    # 所以可以像普通 async 函数那样直接调
    result = asyncio.run(arxiv_server.search_arxiv(query="rag hallucination", max_results=999))
    assert isinstance(result, list)
    assert len(result) == 5
    assert result[0]["title"] == "rag hallucination"
    assert "raw" not in result[0]  # 被 paper_to_json_safe 过滤


def test_arxiv_server_caps_max_results(monkeypatch: pytest.MonkeyPatch) -> None:
    """外部传 max_results=9999 应被 cap 到 50。"""
    from co_scientist.modules.m2_retriever.mcp_servers import arxiv_server

    seen: dict[str, Any] = {}

    async def fake_search(query: str, max_results: int = 20) -> list[dict]:
        seen["max_results"] = max_results
        return []

    monkeypatch.setattr(arxiv_server, "_search_arxiv_impl", fake_search)
    asyncio.run(arxiv_server.search_arxiv(query="x", max_results=9999))
    assert seen["max_results"] == 50


# ============================================================
# 档 2:Client + Server 真 stdio 握手(启子进程,不联网)
# ============================================================
#
# 这些测试真的起一个 MCP Server 子进程,走完 MCP 握手协议。
# 但底层 search 被 monkeypatch,不会真正联网。
#
# 注意:子进程里的代码是另一个进程,你在测试进程里 monkeypatch
# 是不会影响子进程的。所以档 2 的做法是:
#   让 Server 调用一个"特殊的测试底层函数",这个函数本身返回固定 mock 数据。
# 为了不破坏生产代码,这里改用一个**不依赖 monkeypatch** 的策略:
#   跳过真正的数据源,只验证 list_tools 这种纯协议调用。
# ============================================================


@pytest.mark.asyncio
async def test_mcp_client_can_list_tools_of_arxiv_server() -> None:
    """
    启真子进程 arxiv_server,调用 list_tools,应该看到 search_arxiv 工具。

    这是"协议层握手通不通"的金丝雀测试 —— 如果它过,说明:
      - MCP SDK 安装对
      - Server 能启
      - stdio 通信可用
      - @mcp.tool 装饰器注册成功
      - Client 能 initialize + list_tools
    """
    pytest.importorskip("mcp")
    from co_scientist.modules.m2_retriever.mcp_client import list_mcp_tools

    tools = await list_mcp_tools("arxiv")
    assert tools, "arxiv MCP Server 没返回任何工具 —— 协议层握手失败"

    names = [t["name"] for t in tools]
    assert "search_arxiv" in names, f"期望 search_arxiv,实际 {names}"


@pytest.mark.asyncio
async def test_mcp_client_list_tools_works_for_all_three() -> None:
    """三个 Server 都应该能正常 list_tools。"""
    pytest.importorskip("mcp")
    from co_scientist.modules.m2_retriever.mcp_client import list_mcp_tools

    for source, expected_tool in [
        ("arxiv", "search_arxiv"),
        ("semantic_scholar", "search_semantic_scholar"),
        ("openalex", "search_openalex"),
    ]:
        tools = await list_mcp_tools(source)
        names = [t["name"] for t in tools]
        assert expected_tool in names, f"{source} 缺少 {expected_tool},实际 {names}"


@pytest.mark.asyncio
async def test_mcp_client_unknown_source_raises() -> None:
    """未知 source 应该抛清晰异常,不要静默返回空。"""
    from co_scientist.modules.m2_retriever.mcp_client import _server_params_for

    with pytest.raises(ValueError, match="未知 MCP source"):
        _server_params_for("google_scholar")  # 还没实现


# ============================================================
# 档 3:端到端真实检索(需要联网)
# ============================================================
#
# 用 pytest --run-net 打开,默认 skip。
# 成本:每次真调 arXiv API 一次(免费但可能被限流)。
# ============================================================


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line("markers", "net: 需要外网访问的测试")


def pytest_addoption(parser: pytest.Parser) -> None:
    try:
        parser.addoption(
            "--run-net",
            action="store_true",
            default=False,
            help="真的访问外网 API 的集成测试",
        )
    except ValueError:
        # 避免和 evals 的 --run-evals 选项注册重复时报错
        pass


@pytest.mark.net
@pytest.mark.asyncio
async def test_mcp_arxiv_real_search(request: pytest.FixtureRequest) -> None:
    """真正调 arXiv API,端到端验证一次。"""
    if not request.config.getoption("--run-net", default=False):
        pytest.skip("默认 skip,加 --run-net 打开真实网络测试")

    from co_scientist.modules.m2_retriever.mcp_client import search_arxiv

    papers = await search_arxiv("retrieval augmented generation", max_results=3)
    assert len(papers) > 0, "arXiv MCP 路径真实检索为 0,可能限流或协议断了"
    assert all("title" in p for p in papers)
