"""
============================================================
 LLM 客户端基类(llm/base.py)
============================================================

🎓 教学目标
    你即将看到本项目最重要的抽象:LLMClient。
    为什么要做这个抽象?
      - 我们用 GPT 中转(OpenAI 兼容协议)和 Claude 中转(Anthropic 协议)
      - 两边的请求格式、token 字段名、cache 机制不完全一样
      - 上层代码不想知道这些细节,只想说"给我调一次 LLM"
    所以我们定义一个通用接口 + 两份具体实现。
    这是经典的"策略模式 / 适配器模式"。

📌 设计决策
    1. 抽象基类暴露三个核心方法:
         - chat():最常用,返回字符串
         - chat_json():要求返回 JSON,带自动解析 + 重试
         - chat_stream():流式输出(前端气泡展示用)
    2. 所有实现都要:
         - 记录成本(通过 CostTracker)
         - 支持缓存(通过 @cache_llm)
         - 自动重试(通过 tenacity)
    3. Message 用 TypedDict 表示,不引入 LangChain 的类型,减少依赖耦合

------------------------------------------------------------
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, AsyncIterator, Iterator, Literal, TypedDict


# ------------------------------------------------------------
# 💡 为什么用 TypedDict 而不是 pydantic BaseModel?
# ------------------------------------------------------------
# TypedDict 是"看起来像 dict 的类型注解":
#   - 运行时就是普通 dict,无序列化开销
#   - 能被 IDE 类型检查(mypy / pyright)
#   - 与 OpenAI SDK / Anthropic SDK 的参数格式天然兼容
# pydantic BaseModel 更重(校验、序列化),这里用不上。
# ------------------------------------------------------------
class Message(TypedDict):
    """LLM 对话中的一条消息,字段与 OpenAI 标准一致。"""

    role: Literal["system", "user", "assistant", "tool"]
    content: str


class LLMResponse(TypedDict, total=False):
    """
    LLM 返回结果的统一格式。
    total=False 表示所有字段都是可选的(不同供应商/不同调用返回字段不同)。
    """

    content: str  # 主要的文本回答
    input_tokens: int  # 输入 token 数
    output_tokens: int  # 输出 token 数
    cache_hit_tokens: int  # 其中命中 prompt cache 的 token 数
    cost_usd: float  # 本次花费(美元)
    raw: Any  # 原始 SDK 响应对象(调试用)
    reasoning: str  # 部分 OpenAI-compatible reasoner 模型的 reasoning 内容(可选)


class LLMClient(ABC):
    """
    LLM 客户端抽象基类。

    子类要实现:
      - _chat_raw():对接具体 SDK,返回 LLMResponse
      - model_family:'gpt-relay' 或 'claude-relay'
    """

    # 子类覆盖:用于成本计算和日志标识
    model_family: str = "unknown"
    default_model: str = ""

    # ----------------------------------------------------
    # 公开 API:同步
    # ----------------------------------------------------
    @abstractmethod
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
        """
        发送对话,返回单轮结果。

        Args:
            messages: 标准 OpenAI 格式消息列表
            model: 覆盖默认模型名
            temperature: 采样温度,0=确定性,1+=创造性
            max_tokens: 最大输出 token 数,防失控
            purpose: 业务标签,写入成本跟踪,如 "m4_meta_review"
            **kwargs: 供应商特有参数(如 top_p、presence_penalty)

        Returns:
            LLMResponse 字典,content 字段是文本答案
        """

    @abstractmethod
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
        流式对话,逐 token 吐出(前端气泡展示用)。

        使用:
            for delta in client.chat_stream([...]):
                print(delta, end="", flush=True)

        Note:
            成本跟踪会在流结束时统一记录一次。
        """

    # ----------------------------------------------------
    # 公开 API:强制 JSON 输出
    # ----------------------------------------------------
    def chat_json(
        self,
        messages: list[Message],
        *,
        model: str | None = None,
        temperature: float = 0.2,  # JSON 场景默认低温度,减少格式错误
        max_tokens: int = 4096,
        purpose: str = "",
        max_retries: int = 2,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """
        要求模型返回 JSON,自动解析。

        为什么不直接让调用方自己 json.loads?
          - LLM 经常输出 ```json ... ``` 包裹,要手动剥
          - 偶尔输出非法 JSON,要重试
          - 我们做一层封装,调用方拿到的就是 dict

        实现思路:
          1. 在 system prompt 里强调"只输出 JSON"
          2. 解析失败 → 把错误回喂给模型让它自我修正
          3. 重试次数用完仍失败 → 抛异常

        之所以写成同步默认实现而不是抽象方法,是因为
        "解析 + 重试" 的逻辑不因供应商而变,基类写一次即可。

        这段代码其实体现了一个很实用的工程判断:
          - `chat()` 解决的是"把请求发出去"
          - `chat_json()` 解决的是"把不稳定的自然语言输出收敛成稳定结构"
        后者才是真正让上层业务代码舒服的关键。因为模块 1/3/4/5/7
        都希望拿到 dict/list 后直接进业务分支,而不是每个模块都重复写
        markdown 去壳、JSON 解析、失败重试这些样板代码。

        你可以把它看成一种"弱结构化输出适配层":
          模型本身并不真的理解 Python dict,但我们通过提示词 + 解析 + 纠错回路,
          把它的文本回答尽量稳定地收敛到程序可消费的数据结构上。
        这也是很多 Agent 项目从 demo 走向稳定产品时最先该抽象出来的一层。
        """
        import json  # 延迟导入,只在真正调用时才需要

        # 不改原 messages,拷贝一份
        msgs = list(messages)

        # 在最后追加一条强提示,而不是改 system,便于调用方自己定义角色
        msgs.append(
            {
                "role": "user",
                "content": "请严格以 JSON 格式输出,不要任何额外文字或 markdown 代码块。",
            }
        )

        last_error: Exception | None = None
        for attempt in range(max_retries + 1):
            resp = self.chat(
                msgs,
                model=model,
                temperature=temperature,
                max_tokens=max_tokens,
                purpose=purpose or "chat_json",
                **kwargs,
            )
            raw = resp.get("content", "").strip()

            # 去掉常见的 ```json ``` 包裹
            if raw.startswith("```"):
                # 处理 ```json\n...\n``` 或 ```\n...\n```
                raw = raw.strip("`")
                if raw.lower().startswith("json"):
                    raw = raw[4:]
                raw = raw.strip()

            try:
                return json.loads(raw)
            except json.JSONDecodeError as e:
                last_error = e
                # 把错误反馈给模型,让它重新输出
                msgs.append({"role": "assistant", "content": raw})
                msgs.append(
                    {
                        "role": "user",
                        "content": (
                            f"上面的输出不是合法 JSON,错误:{e}。"
                            "请重新输出,只包含合法 JSON,不要任何其他内容。"
                        ),
                    }
                )
                continue

        # 兜底:重试仍失败,抛异常给上层
        raise ValueError(f"chat_json 解析失败 {max_retries + 1} 次: {last_error}")


class LLMError(Exception):
    """所有 LLM 客户端错误的基类,方便上层统一 catch。"""


class LLMRateLimitError(LLMError):
    """被限流,上层可以选择等待或降级。"""


class LLMAuthError(LLMError):
    """密钥无效/过期,上层应立即终止。"""
