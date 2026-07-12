"""
============================================================
 MCP Server 共用工具(_common.py)
============================================================

🎓 教学目标
    3 个 MCP Server 有大量重复的"样板":把内部 Paper TypedDict
    转成 MCP 协议可序列化的 JSON、统一错误返回、规范工具描述。
    这里集中处理,避免每个 server 文件 copy-paste 同样的代码。

💡 为什么单独抽 _common 而不是都写进 __init__.py
    - __init__.py 的 docstring 已经被用来讲"包整体定位",
      再塞工具函数会让那份文档变噪音。
    - 下划线前缀告诉读者:这是**包内部工具**,不对外导出。
      外部项目只应该 import 3 个 server,不关心本文件。

------------------------------------------------------------
"""

from __future__ import annotations

from typing import Any

# 为什么把 Paper 转成 dict 的逻辑放在这里而不是 state/research_state.py:
#   Paper 本身就是 TypedDict(运行时就是 dict),但里面有些字段不是 JSON 安全的
#   (比如 raw 可能是一个 SDK 对象),MCP 协议要求严格可 JSON 化,所以需要
#   一层"安全序列化",确保跨进程时不翻车。
def paper_to_json_safe(paper: dict[str, Any]) -> dict[str, Any]:
    """
    把一篇 Paper 转成"100% JSON 可序列化"的 dict,供 MCP 协议传输。

    ▍为什么要做这一步
        MCP 的所有响应会被 json.dumps 序列化穿过 stdio,如果某个字段里躺着
        SDK 原生对象(如 arxiv.Result 的 lazy property)会抛
        TypeError: Object of type X is not JSON serializable。

    ▍保留字段的选择
        只保留下游 RRF 融合和展示需要的字段,raw 一律丢弃,减小传输体积。
        这也顺便实现了"Paper 的对外契约":跨进程时暴露哪些字段是规范化的。
    """
    keep = (
        "id", "title", "abstract", "authors", "year", "venue",
        "arxiv_id", "doi", "url", "source", "cited_by_count", "score",
    )
    out: dict[str, Any] = {}
    for k in keep:
        v = paper.get(k)
        if v is None:
            continue
        # 兜底:authors 如果偶发不是 list,转成 list 避免下游崩
        if k == "authors" and not isinstance(v, list):
            v = [str(v)]
        out[k] = v
    return out


# 工具描述模板(每个 server 的 search tool 长得几乎一样,只有 source 名字和限流说明不同)
# 拉到 _common 是为了让 3 个 server 文件只关心"自己特有的逻辑",
# 比如 arXiv 的重试、OpenAlex 的倒排摘要解码等。
def make_search_tool_description(
    source_name: str,
    *,
    rate_limit_hint: str = "",
    extra: str = "",
) -> str:
    """
    构造 search 工具的 description(MCP 客户端看到的工具说明)。

    description 写得好坏直接影响 LLM 能不能"想到"该调这个工具。
    建议:
      - 明确本工具要做什么(搜 XX 数据库)
      - 输入输出格式(关键字符串 → Paper 列表)
      - 限制(最大 N 篇、限流说明)

    这三条是 Anthropic 官方 MCP 最佳实践文档里强调的"工具描述三要素"。
    """
    lines = [
        f"Search academic papers from {source_name}.",
        "Input: a natural language query string (English recommended).",
        "Output: a JSON list of papers with fields: id, title, abstract, authors, year, url, doi.",
    ]
    if rate_limit_hint:
        lines.append(f"Rate limit: {rate_limit_hint}")
    if extra:
        lines.append(extra)
    return "\n".join(lines)
