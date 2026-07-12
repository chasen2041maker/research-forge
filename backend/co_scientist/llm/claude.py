"""
============================================================
 Claude Opus 4.7 客户端(llm/claude.py)
============================================================

🎓 教学目标
    Claude 有自己的 SDK 和消息格式(和 OpenAI 不完全一样),
    主要差异:
      1. system 参数是顶层字段,不是 messages[0]
      2. 消息角色只有 user/assistant,没有 system
      3. Prompt Caching 需要显式声明 cache_control

    本文件教你如何"把 Anthropic 原生格式适配成统一接口"。

📌 关键点
    - 只在关键节点使用(占 5%),比如 Meta-Reviewer 终裁、论文 Editor 润色
    - prompt caching 会给长 system prompt 带来 10x 降本,务必开启

------------------------------------------------------------
"""

from __future__ import annotations

from typing import Any, Iterator

import anthropic
from anthropic import APIError, AuthenticationError, RateLimitError
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from co_scientist.config import settings
from co_scientist.llm.base import (
    LLMAuthError,
    LLMClient,
    LLMError,
    LLMResponse,
    Message,
)
from co_scientist.utils import get_tracker, logger


class ClaudeClient(LLMClient):
    """Claude Opus 4.7 客户端,只负责高价值关键节点调用。"""

    model_family = "claude"
    default_model = settings.MODEL_CRITICAL  # claude-opus-4-7

    def __init__(
        self,
        model: str | None = None,
        *,
        base_url: str | None = None,
        api_key: str | None = None,
        family: str | None = None,
    ) -> None:
        """
        🎓 教学目标
            与 OpenAICompatibleClient 同款思路:参数化 3 个差异点让 factory 在中转站模式
            下复用本类。不同的是 Anthropic 协议保留了 /v1/messages + thinking 字段,
            所以中转站也必须走 Anthropic 协议(不能用 OpenAI 兼容)。

        📌 设计决策
            1. 不写两份:
               Claude 中转站对外仍走 Anthropic 协议(/v1/messages),
               anthropic SDK 设计就支持自定义 base_url,直接复用。
            2. 为什么 critical 必须走 Anthropic 协议而不是 OpenAI 兼容:
               - Anthropic 原生协议暴露 thinking 字段(Extended Thinking 推理预算)
               - OpenAI 兼容协议无法传 thinking,会丢失关键裁决能力
               - 整理版 §3.2 推荐 critical = claude-opus-4-7,正是为了用这个能力
            3. family="claude-relay" vs family="claude":
               - 默认 family="claude"(legacy 路径)
               - 中转站模式下 factory 显式传 "claude-relay",日志一眼区分

        ▍调用示例对比
            原始用法(Anthropic 官方):
                ClaudeClient()                                  → 走 settings.ANTHROPIC_*
            中转站用法(Claude 中转站):
                ClaudeClient(base_url="https://www.right.codes",
                             api_key="...",
                             family="claude-relay")             → 走中转站
        """
        self._sdk = anthropic.Anthropic(
            api_key=api_key or settings.ANTHROPIC_API_KEY.get_secret_value(),
            base_url=base_url or settings.ANTHROPIC_BASE_URL,
        )
        if model:
            self.default_model = model
        if family:
            self.model_family = family

    # ----------------------------------------------------
    # 工具:把 OpenAI 风格消息转成 Anthropic 风格
    # ----------------------------------------------------
    @staticmethod
    def _split_system(messages: list[Message]) -> tuple[str, list[dict]]:
        """
        Anthropic API 要求 system 单独传,messages 只能有 user/assistant。
        我们把 [system, user, ...] 拆分成 (system_str, user_messages)。

        多条 system 会被拼接(虽然我们一般只写一条)。
        """
        system_parts: list[str] = []
        chat_msgs: list[dict] = []
        for m in messages:
            if m["role"] == "system":
                system_parts.append(m["content"])
            else:
                chat_msgs.append({"role": m["role"], "content": m["content"]})
        return "\n\n".join(system_parts), chat_msgs

    # ----------------------------------------------------
    # 同步 chat
    # ----------------------------------------------------
    @retry(
        retry=retry_if_exception_type(RateLimitError),
        stop=stop_after_attempt(4),
        wait=wait_exponential(multiplier=1, min=1, max=8),
        reraise=True,
    )
    def chat(
        self,
        messages: list[Message],
        *,
        model: str | None = None,
        temperature: float = 0.7,
        max_tokens: int = 4096,
        purpose: str = "",
        enable_cache: bool = True,  # Claude 特有:是否给 system 打 cache_control
        thinking_budget: int | None = None,  # Extended Thinking 推理预算(token 数)
        **kwargs: Any,
    ) -> LLMResponse:
        """
        Args 新增说明:
            thinking_budget: Claude 4+ 的 Extended Thinking 预算。
                None(默认)→ 按 purpose 智能选择(meta 节点自动给更高预算)
                0           → 明确关闭
                > 0         → 用指定预算,模型会在回答前做这么多 token 的推理
                对应 Anthropic 2025 发布的 Extended Thinking API,
                类似 OpenAI o1/o3 的 reasoning tokens。

        ▍为什么默认按 purpose 智能选择
            调用方不应每次都想"我这里需不需要深度思考":
              - Meta 终裁(purpose 含 "meta") → 自动开大预算(settings.CLAUDE_THINKING_BUDGET_META)
              - 其他调用 → 默认关闭(快 + 省)
            让 settings 一处控制"哪些场景开推理、给多少预算",业务代码零感知。
        """
        model = model or self.default_model
        system_text, chat_msgs = self._split_system(messages)
        tracker = get_tracker()

        # ---- 构造 system(可选 cache) ----
        # Anthropic 的 prompt caching:把 system 切成 block,最后一个 block 打上
        # cache_control,命中时便宜 10x。系统 prompt 越长,省的钱越多。
        if enable_cache and system_text:
            system_payload: Any = [
                {
                    "type": "text",
                    "text": system_text,
                    "cache_control": {"type": "ephemeral"},
                }
            ]
        else:
            system_payload = system_text  # 直接传字符串也可以

        # ---- Extended Thinking(推理预算)----
        # 为什么按 purpose 判定而不是按 model:
        #   同一个 Claude Opus 模型,跑 m4_meta 时应深思熟虑,跑普通检索润色时没必要开。
        #   把策略集中在这里,业务代码调 chat() 不用关心。
        budget = thinking_budget
        if budget is None:
            if "meta" in (purpose or "").lower():
                budget = settings.CLAUDE_THINKING_BUDGET_META
            else:
                budget = settings.CLAUDE_THINKING_BUDGET_DEFAULT

        extra_kwargs: dict[str, Any] = {}
        if budget and budget > 0:
            # 按 Anthropic API 规范,budget_tokens 必须小于 max_tokens
            # 若用户传的 max_tokens 不够,自动调高(推理本身需要 token 输出空间)
            effective_max = max(max_tokens, budget + 1024)
            extra_kwargs["thinking"] = {
                "type": "enabled",
                "budget_tokens": budget,
            }
            # Extended Thinking 要求 temperature=1,不能改(Anthropic API 硬约束)
            temperature = 1.0
            logger.info(
                "[claude] 启用 Extended Thinking budget={} purpose={}",
                budget, purpose,
            )
            max_tokens = effective_max

        with tracker.track(model=model, purpose=purpose) as record:
            try:
                resp = self._sdk.messages.create(
                    model=model,
                    system=system_payload,
                    messages=chat_msgs,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    **extra_kwargs,
                    **kwargs,
                )
            except AuthenticationError as e:
                raise LLMAuthError(f"Anthropic 密钥无效: {e}") from e
            except RateLimitError as e:
                logger.warning("Claude 限流,准备重试: {}", e)
                raise
            except APIError as e:
                raise LLMError(f"Claude API 错误: {e}") from e

            # Claude 的返回是 content blocks(为支持多模态),取第一个 text block
            content = ""
            for block in resp.content:
                if block.type == "text":
                    content += block.text

            usage = resp.usage
            input_tokens = usage.input_tokens
            output_tokens = usage.output_tokens
            # cache 命中 token:cache_read_input_tokens
            cache_hit = getattr(usage, "cache_read_input_tokens", 0) or 0

            record.input_tokens = input_tokens
            record.output_tokens = output_tokens
            record.cache_hit_tokens = cache_hit

            return LLMResponse(
                content=content,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                cache_hit_tokens=cache_hit,
                cost_usd=tracker.calc_cost(model, input_tokens, output_tokens, cache_hit),
                raw=resp,
            )

    # ----------------------------------------------------
    # 流式输出
    # ----------------------------------------------------
    # 🎓 教学点:为什么 chat_stream 不能直接加 @retry 装饰器
    #   tenacity 的 @retry 在遇到异常时会重跑整个函数。对普通函数这没问题,
    #   但 chat_stream 是个**生成器**:如果已经 yield 了几个 chunk 后才挂,
    #   重跑会让下游读到"前半段两次"。所以必须手写"只在开流前重试"的逻辑,
    #   一旦开始吐字就不再重试,直接往外抛。
    #
    # 📌 与 OpenAI-compatible 客户端的对称性
    #   上次审计发现 claude.chat 有 tenacity 重试、chat_stream 没有,不对称。
    #   现在手写的重试语义和 chat 的装饰器一致:只对 RateLimitError 退避。
    def chat_stream(
        self,
        messages: list[Message],
        *,
        model: str | None = None,
        temperature: float = 0.7,
        max_tokens: int = 4096,
        purpose: str = "",
        enable_cache: bool = True,
        **kwargs: Any,
    ) -> Iterator[str]:
        import time

        model = model or self.default_model
        system_text, chat_msgs = self._split_system(messages)
        tracker = get_tracker()

        if enable_cache and system_text:
            system_payload: Any = [
                {
                    "type": "text",
                    "text": system_text,
                    "cache_control": {"type": "ephemeral"},
                }
            ]
        else:
            system_payload = system_text

        max_attempts = 4
        for attempt in range(max_attempts):
            yielded_any = False
            try:
                with tracker.track(model=model, purpose=purpose or "stream") as record:
                    # Anthropic 的 stream 用上下文管理器
                    with self._sdk.messages.stream(
                        model=model,
                        system=system_payload,
                        messages=chat_msgs,
                        temperature=temperature,
                        max_tokens=max_tokens,
                        **kwargs,
                    ) as stream:
                        for text in stream.text_stream:
                            yielded_any = True
                            yield text

                        # 流结束后,final_message 里有 usage
                        final = stream.get_final_message()
                        record.input_tokens = final.usage.input_tokens
                        record.output_tokens = final.usage.output_tokens
                        record.cache_hit_tokens = (
                            getattr(final.usage, "cache_read_input_tokens", 0) or 0
                        )
                return  # 成功完整消费,退出重试循环
            except AuthenticationError as e:
                # 密钥无效,重试也没用,直接抛
                raise LLMAuthError(f"Anthropic 密钥无效: {e}") from e
            except RateLimitError as e:
                # 已经开始吐 chunk 就不能重试了(下游已经收到前半段)
                if yielded_any or attempt == max_attempts - 1:
                    raise
                wait = min(8.0, 2 ** attempt)  # 1s → 2s → 4s → 8s 指数退避
                logger.warning(
                    "Claude stream 限流(第 {} 次),{}s 后重试",
                    attempt + 1,
                    wait,
                )
                time.sleep(wait)
            except APIError as e:
                raise LLMError(f"Claude API 错误: {e}") from e
