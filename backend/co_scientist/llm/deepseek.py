"""
============================================================
 DeepSeek 客户端(llm/deepseek.py)
============================================================

🎓 教学目标
    DeepSeek 用 OpenAI 兼容协议,所以我们直接用 openai SDK,
    只改 base_url 即可。这是 LLM 应用工程里最常见的"国产替代"做法。

📌 关键点
    1. base_url = "https://api.deepseek.com",其余 API 参数和 OpenAI 一致
    2. DeepSeek 的 cache 是平台侧自动启用的:
         response.usage.prompt_cache_hit_tokens 字段告诉你命中了多少
    3. deepseek-reasoner 会返回 reasoning_content(思考链),我们也透传出去

------------------------------------------------------------
"""

from __future__ import annotations

from typing import Any, Iterator

from openai import OpenAI
from openai import APIError, AuthenticationError, RateLimitError
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from co_scientist.config import settings
from co_scientist.llm.base import (
    LLMAuthError,
    LLMClient,
    LLMError,
    LLMRateLimitError,
    LLMResponse,
    Message,
)
from co_scientist.utils import get_tracker, logger


class DeepSeekClient(LLMClient):
    """
    DeepSeek 客户端封装。

    使用:
        client = DeepSeekClient()
        resp = client.chat([{"role": "user", "content": "你好"}])
        print(resp["content"])
    """

    model_family = "deepseek"
    default_model = settings.MODEL_CHAT  # 默认走 deepseek-chat

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
            演示"OpenAI 兼容客户端如何同时服务多个供应商":只参数化 3 个差异点
            (base_url / api_key / family),业务模块拿同一个 LLMClient 接口,
            不知道背后是 DeepSeek 还是 GPT 中转站。

        📌 设计决策
            1. 不重写一个新类:
               DeepSeek 走 OpenAI 兼容协议,GPT 中转站也走 OpenAI 兼容协议,
               两者请求格式完全一致,差别仅在 base_url / 模型名。新建类是重复代码。
            2. family 参数与 cost_tracker 解耦:
               - cost_tracker.calc_cost() 只看 model 名查价格表(gpt-5.5 / deepseek-chat)
               - 业务日志想区分"中转站还是官方"看 model_family
               family 参数允许 factory 显式标 "gpt-relay" 给日志识别,
               不污染 cost_tracker 的价格表逻辑
            3. * 之后的关键字参数:
               base_url / api_key / family 都是关键字参数,避免位置传参误用
               (老调用方传一个位置 model 仍然兼容)

        ▍调用示例对比
            原始用法(DeepSeek 官方):
                DeepSeekClient(model="deepseek-chat")          → 走 settings.DEEPSEEK_*
            中转站用法(GPT 中转站):
                DeepSeekClient(model="gpt-5.5",
                               base_url="https://right.codes/codex/v1",
                               api_key="...",
                               family="gpt-relay")             → 走中转站
        """
        self._sdk = OpenAI(
            api_key=api_key or settings.DEEPSEEK_API_KEY.get_secret_value(),
            base_url=base_url or settings.DEEPSEEK_BASE_URL,
        )
        if model:
            self.default_model = model
        if family:
            self.model_family = family

    # ----------------------------------------------------
    # 核心方法:同步 chat
    # ----------------------------------------------------
    # 用 tenacity 自动重试。策略:
    #   - 只在限流(RateLimitError)或服务端错误时重试
    #   - 指数退避:1s → 2s → 4s,最多 4 次
    #   - 鉴权失败立即抛错,重试也没用
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
        **kwargs: Any,
    ) -> LLMResponse:
        model = model or self.default_model
        tracker = get_tracker()

        # 用 tracker 上下文管理器,自动记录耗时和成本
        with tracker.track(model=model, purpose=purpose) as record:
            try:
                # 调用 OpenAI 兼容 API。所有 kwargs 直接透传,方便高级用户用
                completion = self._sdk.chat.completions.create(
                    model=model,
                    messages=messages,  # type: ignore[arg-type]
                    temperature=temperature,
                    max_tokens=max_tokens,
                    **kwargs,
                )
            except AuthenticationError as e:
                raise LLMAuthError(f"{self.model_family} 鉴权失败: {e}") from e
            except RateLimitError as e:
                # 让 tenacity 抓到这个去重试
                logger.warning("{} 限流,准备重试: {}", self.model_family, e)
                raise
            except APIError as e:
                raise LLMError(f"{self.model_family} API 错误: {e}") from e

            # ---- 拆响应 ----
            choice = completion.choices[0]
            content = choice.message.content or ""

            # DeepSeek-Reasoner 会附带 reasoning_content(模型思考链)
            reasoning = getattr(choice.message, "reasoning_content", "") or ""

            usage = completion.usage
            input_tokens = usage.prompt_tokens if usage else 0
            output_tokens = usage.completion_tokens if usage else 0

            # cache 命中 token 数(DeepSeek 特有字段)
            cache_hit = 0
            if usage and hasattr(usage, "prompt_cache_hit_tokens"):
                cache_hit = usage.prompt_cache_hit_tokens or 0

            # 写回 record,track() 退出时会持久化
            record.input_tokens = input_tokens
            record.output_tokens = output_tokens
            record.cache_hit_tokens = cache_hit

            return LLMResponse(
                content=content,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                cache_hit_tokens=cache_hit,
                cost_usd=tracker.calc_cost(model, input_tokens, output_tokens, cache_hit),
                raw=completion,
                reasoning=reasoning,
            )

    # ----------------------------------------------------
    # 流式输出
    # ----------------------------------------------------
    def chat_stream(
        self,
        messages: list[Message],
        *,
        model: str | None = None,
        temperature: float = 0.7,
        max_tokens: int = 4096,
        purpose: str = "",
        **kwargs: Any,
    ) -> Iterator[str]:
        """
        SSE 流式输出。
        DeepSeek 在流模式下,usage 只在最后一个 chunk 返回,
        我们在循环结束后统一记成本。
        """
        model = model or self.default_model
        tracker = get_tracker()
        with tracker.track(model=model, purpose=purpose or "stream") as record:
            stream = self._sdk.chat.completions.create(
                model=model,
                messages=messages,  # type: ignore[arg-type]
                temperature=temperature,
                max_tokens=max_tokens,
                stream=True,
                stream_options={"include_usage": True},  # 关键:让最后一个 chunk 带 usage
                **kwargs,
            )
            for chunk in stream:
                # usage 信息在最后一个 chunk
                if chunk.usage:
                    record.input_tokens = chunk.usage.prompt_tokens
                    record.output_tokens = chunk.usage.completion_tokens
                    record.cache_hit_tokens = (
                        getattr(chunk.usage, "prompt_cache_hit_tokens", 0) or 0
                    )

                if not chunk.choices:
                    continue
                delta = chunk.choices[0].delta.content or ""
                if delta:
                    yield delta

    # ----------------------------------------------------
    # 向量化(embedding)
    # ----------------------------------------------------
    # 🎓 教学点:为什么 embedding 要放在 LLM 客户端下而不是独立包
    #   和 chat 一样,embedding 也是"发请求 + 记成本 + 用缓存"的范式。
    #   复用同一个 OpenAI SDK 实例(连接池复用),避免每次建新 HTTP 连接。
    #   cost_tracker 的 PRICING 表里已经列了 deepseek-embedding 的单价,
    #   这里 purpose="m2_embed_rerank" 之类就能自动分摊到正确的模块标签。
    #
    # 📌 设计决策
    #   1. 支持批量:embeddings.create 的 input 字段可以直接吃 list[str]
    #   2. 失败时向上抛 LLMError,上层决定是否降级回纯 RRF 结果(而不是
    #      静默返回 [],那样会让调用方以为向量是全 0)
    #   3. 不做跨调用缓存:文本通常动态变化(查询/论文摘要),diskcache 命中率
    #      极低。真要省钱在调用方做 per-doc 记忆化
    def embed(
        self,
        texts: list[str],
        *,
        model: str | None = None,
        purpose: str = "embed",
    ) -> list[list[float]]:
        """
        把文本列表向量化。

        Args:
            texts: 待向量化文本,长度 N
            model: 覆盖默认 embedding 模型名
            purpose: 写入 cost_tracker 的 tag,如 "m2_embed_query"

        Returns:
            长度 N 的 list[list[float]],每条是一条向量。
        """
        if not texts:
            return []

        if model is None and self.model_family == "gpt-relay":
            model = settings.RELAY_MODEL_EMBEDDING
        model = model or settings.MODEL_EMBEDDING
        tracker = get_tracker()

        with tracker.track(model=model, purpose=purpose) as record:
            try:
                resp = self._sdk.embeddings.create(model=model, input=texts)
            except AuthenticationError as e:
                raise LLMAuthError(f"{self.model_family} embedding 鉴权失败: {e}") from e
            except RateLimitError as e:
                logger.warning("{} embedding 限流: {}", self.model_family, e)
                raise
            except APIError as e:
                raise LLMError(f"{self.model_family} embedding API 错误: {e}") from e

            # embeddings API 只计费 input_tokens,output=0
            usage = resp.usage
            record.input_tokens = usage.prompt_tokens if usage else 0
            record.output_tokens = 0
            record.cache_hit_tokens = 0

            # 按请求顺序返回向量
            return [item.embedding for item in resp.data]
