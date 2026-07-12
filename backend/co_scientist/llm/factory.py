"""
============================================================
 LLM 工厂(llm/factory.py)
============================================================

🎓 教学目标
    上层代码不应该关心"这次用哪个具体模型",它只说
    "给我一个适合 reasoning 的 client"或"给我关键节点的 client"。
    这就是**工厂模式**。

    学会这个模式,你可以随时:
      - 把主力换成别的模型(改一处即可)
      - 做 A/B 测试(根据配置返回不同 client)
      - Mock 掉整个 LLM 层(测试用)

------------------------------------------------------------
"""

from __future__ import annotations

from typing import Literal

from co_scientist.config import settings
from co_scientist.llm.base import LLMAuthError, LLMClient
from co_scientist.llm.claude import ClaudeClient
from co_scientist.llm.openai_compat import OpenAICompatibleClient

# 模型角色标签 —— 语义化的"用途",而不是具体模型名
# 整理版 §3 强调:业务模块只依赖这三个语义角色,模型替换/降级集中在本文件。
ModelRole = Literal[
    "chat",      # 日常生成、写作、抽取
    "reasoner",  # 推理、评审、决策
    "critical",  # 关键节点(Meta-Reviewer 终裁等)
]


# 进程级客户端缓存,避免反复创建 HTTP 连接池
_clients: dict[ModelRole, LLMClient] = {}


def _require_key(value: str, name: str) -> str:
    """Fail fast when the relay key is missing instead of falling back to another provider."""
    if not value.strip():
        raise LLMAuthError(f"{name} 未配置,请在 .env 中填写中转站密钥")
    return value


def _build_relay(role: ModelRole) -> LLMClient:
    """
    固定中转站路径:chat/reasoner 路由到 GPT 中转站,
    critical 路由到 Claude 中转站。

    🎓 教学目标
        演示"模型供应商替换"如何收敛在一个工厂函数里。业务模块只调
        get_llm("chat"),完全不知道背后具体模型名。

    📌 设计决策
        1. GPT 中转站走 OpenAI 兼容协议,OpenAICompatibleClient 基于 openai SDK
           只需要 base_url/api_key/model 三个参数。
           - Claude 中转站走 Anthropic 原生协议,ClaudeClient 同样参数化即可
        2. family 字段标记中转 vs 官方:
           - cost_tracker 用 model 名查价(gpt-5.5 / claude-opus-4-7),不依赖 family
           - 但业务日志想知道"这次调用走的是中转站还是官方"用于排查时,family 是关键标记
           - "gpt-relay" / "claude-relay" 让日志一眼看出渠道
        3. 不再做 DeepSeek/Anthropic 官方回落:
           - RELAY_GPT_API_KEY 为空就直接报配置错误
           - 避免 .env 里占位 DEEPSEEK_API_KEY 被误用导致 401
        4. chat / reasoner 共用 GPT 中转站类:
           - 整理版 §3.2 推荐 chat/reasoner 都走 gpt-5.5
           - 差异在 system prompt 和 temperature(由调用方控制),不需要不同 client
        5. critical 单独走 Claude:
           - 关键裁决要 Claude 的细颗粒指令遵循 + Extended Thinking
           - OpenAI 兼容协议无法暴露 thinking 参数,所以必须走 Anthropic 原生协议
    """
    gpt_key = _require_key(
        settings.RELAY_GPT_API_KEY.get_secret_value(),
        "RELAY_GPT_API_KEY",
    )
    claude_key = _require_key(
        settings.RELAY_CLAUDE_API_KEY.get_secret_value(),
        "RELAY_CLAUDE_API_KEY",
    )

    # ◍ chat / reasoner 共享 GPT 中转站(OpenAI 兼容协议)
    if role == "chat":
        return OpenAICompatibleClient(
            model=settings.RELAY_MODEL_CHAT,
            base_url=settings.RELAY_GPT_BASE_URL,
            api_key=gpt_key,
            family="gpt-relay",
        )
    if role == "reasoner":
        return OpenAICompatibleClient(
            model=settings.RELAY_MODEL_REASONER,
            base_url=settings.RELAY_GPT_BASE_URL,
            api_key=gpt_key,
            family="gpt-relay",
        )
    # ◍ critical 走 Claude 中转站(Anthropic 协议)以保留 Extended Thinking
    if role == "critical":
        return ClaudeClient(
            model=settings.RELAY_MODEL_CRITICAL,
            base_url=settings.RELAY_CLAUDE_BASE_URL,
            api_key=claude_key,
            family="claude-relay",
        )
    raise ValueError(f"未知 LLM 角色: {role}")


def get_llm(role: ModelRole = "chat") -> LLMClient:
    """
    获取 LLM 客户端。

    使用:
        llm = get_llm("reasoner")
        resp = llm.chat([...], purpose="m4_novelty_review")

    好处:
        - 业务代码只说"我要 reasoner",不关心具体模型
        - 换模型时只改本文件的映射
        - chat/reasoner 固定走 GPT 中转站,critical 固定走 Claude 中转站
        - settings.USE_RELAY 已废弃,即使 .env 写 false 也不会切回 DeepSeek
    """
    if role in _clients:
        return _clients[role]

    client = _build_relay(role)
    _clients[role] = client
    return client


def reset_clients() -> None:
    """清空客户端缓存,用于测试或密钥轮转后。"""
    _clients.clear()
