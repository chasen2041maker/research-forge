"""
============================================================
 观测性初始化(utils/observability.py)
============================================================

🎓 教学目标
    一个 Agent 系统要"生产就绪",必须能回答:
      - 昨天那条失败的 run 是哪一步挂的?→ tracing
      - 这个月 LLM 花了多少钱?→ cost metrics(我们有 cost_tracker.py)
      - Meta 评分一致性在下降吗?→ eval metrics(我们有 tests/evals/)
    tracing 这一层我们接 LangSmith 来解决。

💡 为什么选 LangSmith
    - 和 LangGraph **天然集成**:不用改一行代码,只要 env var 打开就能看到
      每个节点的 trajectory、每次 LLM 调用的 input/output/latency/cost
    - 支持 **thread_id 聚合**:一条 run 里的所有调用被串起来可回放
    - 免费额度:5000 trace / 月,个人项目 demo 足够
    - 替代品:OpenTelemetry + Langfuse(自建)、Arize Phoenix(开源自建),
      权衡下来 demo/面试 场景 LangSmith 最轻量

💡 为什么不在 __init__ 里强制初始化
    LangSmith 依赖 langchain 包的全局环境变量,import 时机很敏感。
    写成 `setup_langsmith()` 显式函数,由上层(CLI/API/graph.build_graph)
    按需调用,避免模块 import 顺序带来的诡异 bug。

📌 面试讲点
    "我接了 LangSmith 做 trace,每条 run 都能在网页上回放 —— 节点级 trajectory、
     LLM I/O、成本、latency 一应俱全。面试 demo 时能直接分享链接。"

------------------------------------------------------------
"""

from __future__ import annotations

import os

from co_scientist.config import settings
from co_scientist.utils import logger


_LANGSMITH_INITIALIZED = False


def setup_langsmith() -> bool:
    """
    按 settings 启用 LangSmith tracing。

    Returns:
        True 表示成功启用,False 表示跳过(未开 / 没 Key)。

    ▍实现机制
        LangChain / LangGraph 在每次 LLM 调用内部,会检查 LANGCHAIN_TRACING_V2
        这个环境变量(是的,变量名历史包袱,底层仍叫 LANGCHAIN_*),
        开启就上报到 LangSmith。我们只负责把 settings.* 翻译成对应 env。

    ▍为什么要 idempotent
        同进程多次调用本函数应是安全的(比如 CLI 和 API 各调一次)。
        用模块级标志位防重复 export,避免日志刷屏。

    ▍为什么没 Key 时直接跳过
        demo / 教学场景很多人不会注册 LangSmith,直接跑 CLI 也要能成功。
        跳过 + 日志提示 > 抛异常,体验更顺。
    """
    global _LANGSMITH_INITIALIZED
    if _LANGSMITH_INITIALIZED:
        return True

    if not settings.LANGSMITH_TRACING:
        return False

    api_key = settings.LANGSMITH_API_KEY.get_secret_value() if settings.LANGSMITH_API_KEY else ""
    if not api_key:
        logger.warning("[observability] 开了 LANGSMITH_TRACING 但没设 LANGSMITH_API_KEY,跳过")
        return False

    # LangChain 读的是这几个老变量名(LANGSMITH_ 是新名字,老代码底层
    # 仍看 LANGCHAIN_ 开头,SDK 兼容期两个都要 export 最稳)
    os.environ["LANGCHAIN_TRACING_V2"] = "true"
    os.environ["LANGCHAIN_ENDPOINT"] = settings.LANGSMITH_ENDPOINT
    os.environ["LANGCHAIN_API_KEY"] = api_key
    os.environ["LANGCHAIN_PROJECT"] = settings.LANGSMITH_PROJECT
    # 新名字也 export 一份,兼容 langsmith SDK 1.x+
    os.environ["LANGSMITH_TRACING"] = "true"
    os.environ["LANGSMITH_API_KEY"] = api_key
    os.environ["LANGSMITH_PROJECT"] = settings.LANGSMITH_PROJECT

    _LANGSMITH_INITIALIZED = True
    logger.info(
        "[observability] LangSmith tracing 已启用,project={}",
        settings.LANGSMITH_PROJECT,
    )
    return True


def reset_for_test() -> None:
    """仅供测试:重置初始化标志,让 setup 可以再跑一次。"""
    global _LANGSMITH_INITIALIZED
    _LANGSMITH_INITIALIZED = False
    # 清掉已设的环境变量,避免污染后续测试
    for k in (
        "LANGCHAIN_TRACING_V2",
        "LANGCHAIN_ENDPOINT",
        "LANGCHAIN_API_KEY",
        "LANGCHAIN_PROJECT",
        "LANGSMITH_TRACING",
        "LANGSMITH_API_KEY",
        "LANGSMITH_PROJECT",
    ):
        os.environ.pop(k, None)
