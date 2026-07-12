"""
OpenAI-compatible relay client.

The active chat/reasoner path uses this client with the GPT relay endpoint.
It does not read DeepSeek settings and does not fall back to any legacy provider.
"""

from __future__ import annotations

from typing import Any, Iterator

from openai import APIError, AuthenticationError, OpenAI, RateLimitError
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


class OpenAICompatibleClient(LLMClient):
    """OpenAI-compatible client used by the GPT relay."""

    model_family = "gpt-relay"
    default_model = settings.RELAY_MODEL_CHAT

    def __init__(
        self,
        model: str | None = None,
        *,
        base_url: str | None = None,
        api_key: str | None = None,
        family: str | None = None,
    ) -> None:
        self._sdk = OpenAI(
            api_key=api_key or settings.RELAY_GPT_API_KEY.get_secret_value(),
            base_url=base_url or settings.RELAY_GPT_BASE_URL,
        )
        if model:
            self.default_model = model
        if family:
            self.model_family = family

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

        with tracker.track(model=model, purpose=purpose) as record:
            try:
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
                logger.warning("{} 限流,准备重试: {}", self.model_family, e)
                raise
            except APIError as e:
                raise LLMError(f"{self.model_family} API 错误: {e}") from e

            choice = completion.choices[0]
            content = choice.message.content or ""
            reasoning = getattr(choice.message, "reasoning_content", "") or ""

            usage = completion.usage
            input_tokens = usage.prompt_tokens if usage else 0
            output_tokens = usage.completion_tokens if usage else 0
            cache_hit = 0
            if usage and hasattr(usage, "prompt_cache_hit_tokens"):
                cache_hit = usage.prompt_cache_hit_tokens or 0

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
        model = model or self.default_model
        tracker = get_tracker()
        with tracker.track(model=model, purpose=purpose or "stream") as record:
            stream = self._sdk.chat.completions.create(
                model=model,
                messages=messages,  # type: ignore[arg-type]
                temperature=temperature,
                max_tokens=max_tokens,
                stream=True,
                stream_options={"include_usage": True},
                **kwargs,
            )
            for chunk in stream:
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

    def embed(
        self,
        texts: list[str],
        *,
        model: str | None = None,
        purpose: str = "embed",
    ) -> list[list[float]]:
        if not texts:
            return []

        model = model or settings.RELAY_MODEL_EMBEDDING
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

            usage = resp.usage
            record.input_tokens = usage.prompt_tokens if usage else 0
            record.output_tokens = 0
            record.cache_hit_tokens = 0

            return [item.embedding for item in resp.data]
