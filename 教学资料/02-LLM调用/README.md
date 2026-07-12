# 02 - LLM 调用

> 本章教你工业级封装 LLM 调用:统一抽象、两个 SDK、Prompt Cache、流式、JSON 解析、错误处理。

---

## 2.1 为什么要做 LLM 抽象层

### 痛点
直接在业务代码里写 `openai.chat.completions.create(...)`:
- 换供应商要改所有调用处
- 成本跟踪散落各处
- 重试/缓存无法统一加
- 单元测试很难 mock

### 方案:抽象基类 + 适配器模式
```
┌──────────────┐
│ 业务代码     │
│ llm.chat([…])│
└──────┬───────┘
       │
┌──────▼───────┐
│ LLMClient    │  ← 统一接口(ABC)
│  - chat()    │
│  - chat_json()│
│  - chat_stream()│
└──────┬───────┘
       │
   ┌───┴────┐
   │        │
 ┌─▼──┐  ┌─▼──┐
 │DSK │  │CLD │  ← 两个实现(DeepSeek / Claude)
 └────┘  └────┘
```

业务代码只依赖 `LLMClient`,底层换什么不知道。

📌 **项目对应**:`backend/co_scientist/llm/base.py`

---

## 2.2 消息格式:TypedDict vs BaseModel

### 选择
```python
class Message(TypedDict):
    role: Literal["system", "user", "assistant", "tool"]
    content: str
```
**不用 pydantic BaseModel**,因为:
- 运行时就是 dict,与 OpenAI/Anthropic SDK 天然兼容
- 零序列化开销
- IDE 类型检查(mypy / pyright)照样工作

### 什么时候用 BaseModel?
- 要校验用户输入(API 入参)
- 要自动生成 JSON schema(function calling)
- 要序列化持久化

简单的类型约束 → `TypedDict`;复杂校验 → `BaseModel`。

---

## 2.3 DeepSeek:OpenAI 兼容协议

### 关键点:只改 `base_url`
```python
from openai import OpenAI

client = OpenAI(
    api_key="sk-xxx",
    base_url="https://api.deepseek.com",   # ← 唯一改动
)

resp = client.chat.completions.create(
    model="deepseek-chat",                  # 或 deepseek-reasoner
    messages=[{"role": "user", "content": "你好"}],
)
```
其余参数与 OpenAI 一致:`temperature`、`max_tokens`、`top_p`、`stream`、`tools`...

### DeepSeek 特有字段
```python
# 1. Reasoner 思考链
reasoning = choice.message.reasoning_content  # R1 的 CoT

# 2. Cache 命中 token 数
cache_hit = response.usage.prompt_cache_hit_tokens  # 特有字段
```
cache 命中价格是正价的 1/10,是省钱关键。

### Prompt Cache 自动启用规则
DeepSeek 服务端自动缓存 system + user 前缀。
要提高命中率:
- **system prompt 放最前且保持稳定**
- 变化部分放 messages 末尾

```python
messages = [
    {"role": "system", "content": LONG_STABLE_PROMPT},  # 被 cache
    {"role": "user", "content": variable_query},         # 付全价
]
```

📌 **项目对应**:`backend/co_scientist/llm/deepseek.py`

---

## 2.4 Claude:Anthropic 原生协议

### 与 OpenAI 的差异

| 维度 | OpenAI | Anthropic |
|------|--------|-----------|
| system 消息 | `messages[0]` | 顶层 `system=` 参数 |
| 角色 | system/user/assistant/tool | 只有 user/assistant |
| 返回内容 | `choices[0].message.content` | `content[]` blocks(支持多模态) |
| 流式 | `stream=True` | `client.messages.stream(...)` 上下文管理器 |
| Prompt Cache | 自动 | 显式 `cache_control: ephemeral` |

### 把 OpenAI 风格转成 Anthropic
```python
def split_system(messages):
    system_parts, chat = [], []
    for m in messages:
        if m["role"] == "system":
            system_parts.append(m["content"])
        else:
            chat.append(m)
    return "\n\n".join(system_parts), chat
```

### Claude Prompt Caching(省 10x)
```python
system_payload = [{
    "type": "text",
    "text": long_system_prompt,
    "cache_control": {"type": "ephemeral"}  # ← 打 cache 标记
}]

resp = client.messages.create(
    model="claude-opus-4-7",
    system=system_payload,
    messages=chat_msgs,
)

cache_hit = resp.usage.cache_read_input_tokens  # 命中的 token 数
```
cache TTL 约 5 分钟。连续调用同一 system 时命中率高。

📌 **项目对应**:`backend/co_scientist/llm/claude.py`

---

## 2.5 强制 JSON 输出 + 自动重试

### 痛点
- LLM 经常包 ```json ... ``` 代码块
- 偶尔输出非法 JSON
- 上层要 `json.loads` + try/except 处处重复

### 解决:基类里提供 `chat_json`
```python
def chat_json(self, messages, max_retries=2, ...):
    msgs = list(messages)
    msgs.append({"role": "user", "content": "请严格以 JSON 格式输出,不要任何额外文字。"})

    for attempt in range(max_retries + 1):
        resp = self.chat(msgs, temperature=0.2, ...)
        raw = resp["content"].strip()

        # 剥掉 ```json ... ```
        if raw.startswith("```"):
            raw = raw.strip("`")
            if raw.lower().startswith("json"):
                raw = raw[4:]
            raw = raw.strip()

        try:
            return json.loads(raw)
        except json.JSONDecodeError as e:
            # 把错误反馈给模型,让它自我修正
            msgs.append({"role": "assistant", "content": raw})
            msgs.append({"role": "user",
                "content": f"上面不是合法 JSON,错误: {e}。请重新输出。"})

    raise ValueError("多次重试仍失败")
```

### 关键技巧:错误回喂
让模型看到自己的错误输出,修正概率 90%+。

---

## 2.6 流式输出(SSE)

### DeepSeek(OpenAI 风格)
```python
stream = client.chat.completions.create(
    model="deepseek-chat",
    messages=[...],
    stream=True,
    stream_options={"include_usage": True},  # 关键:最后 chunk 带 usage
)

for chunk in stream:
    if chunk.usage:  # 最后一个 chunk
        record_cost(chunk.usage)
    if chunk.choices:
        delta = chunk.choices[0].delta.content
        if delta:
            yield delta
```

### Claude
```python
with client.messages.stream(model="claude-opus-4-7", ...) as stream:
    for text in stream.text_stream:
        yield text
    final = stream.get_final_message()
    record_cost(final.usage)
```

### 前端接入(FastAPI + WebSocket)
```python
@app.websocket("/ws/stream")
async def ws_stream(ws: WebSocket):
    await ws.accept()
    for token in llm.chat_stream(...):
        await ws.send_text(token)
```

---

## 2.7 重试策略:tenacity

```python
@retry(
    retry=retry_if_exception_type(RateLimitError),
    stop=stop_after_attempt(4),
    wait=wait_exponential(multiplier=1, min=1, max=8),
    reraise=True,
)
def chat(self, ...): ...
```

### 不同错误的处理
```python
try:
    resp = sdk.chat.completions.create(...)
except AuthenticationError as e:
    raise LLMAuthError(...)  # 密钥错,立即失败,不重试
except RateLimitError as e:
    raise  # 让 @retry 接住
except APIError as e:
    raise LLMError(...)  # 其他错误包装
```

---

## 2.8 工厂模式:`get_llm(role)`

### 痛点
业务代码写 `DeepSeekClient(model="deepseek-reasoner")` 太具体。
换模型要改几十处。

### 方案:语义化角色
```python
ModelRole = Literal["chat", "reasoner", "critical"]

def get_llm(role: ModelRole = "chat") -> LLMClient:
    if role == "chat":       return DeepSeekClient(model=settings.MODEL_CHAT)
    if role == "reasoner":   return DeepSeekClient(model=settings.MODEL_REASONER)
    if role == "critical":   return ClaudeClient(model=settings.MODEL_CRITICAL)
```

业务代码:
```python
llm = get_llm("reasoner")  # 只说"我要推理模型",不管具体哪个
```

### 好处
- 换模型改一处
- A/B 测试容易(根据配置切实现)
- Mock 测试容易

📌 **项目对应**:`backend/co_scientist/llm/factory.py`

---

## 2.9 降本三板斧

### 1. 模型分层
95% 日常调用 → DeepSeek($0.27/M)
5% 关键决策 → Claude Opus 4.7($15/M)
按调用量算,成本主要由 5% 关键决策决定,不用无脑全 Claude。

### 2. Prompt Cache
- DeepSeek 自动,system 稳定则命中率 80%+
- Claude 显式 `cache_control`
- 命中价约为正价 10%

### 3. 温度与 max_tokens
- JSON 任务 temperature=0.2(减少格式错误重试)
- 写作 temperature=0.6-0.8
- **max_tokens 一定设合理上限**,防失控

---

## 📝 面试常见问题

1. **DeepSeek 如何兼容 OpenAI SDK?**
   - 改 base_url 即可,协议完全一致

2. **Claude 和 OpenAI 消息格式差异?**
   - Claude system 是顶层字段,messages 只有 user/assistant;返回是 content blocks

3. **如何让 LLM 稳定输出 JSON?**
   - 低温度 + system 强约束 + 剥 markdown + 错误回喂重试

4. **Prompt Caching 如何工作?**
   - 前缀稳定时命中,cache token 便宜 10x。system 放最前且不变是关键

5. **流式输出为什么快?**
   - 首 token 延迟低,用户体感秒回。不是总时长缩短

6. **LLM 调用怎么做成本跟踪?**
   - 每次调用记 `input_tokens * price + output_tokens * price`,按 purpose 分组

---

## 🎯 练手题

1. 给 `DeepSeekClient` 加 `chat_with_tools(tools=[...])` 支持 function calling
2. 实现一个 `fallback` 装饰器:Claude 挂了自动降级到 `reasoner`
3. 统计一周内 cache 命中率,导出一张折线图

---

## ✅ 练手题参考答案

### 答案 1:`chat_with_tools`

DeepSeek 兼容 OpenAI tools 协议,直接透传:
```python
def chat_with_tools(
    self,
    messages: list[Message],
    tools: list[dict],
    *,
    tool_choice: str | dict = "auto",
    model: str | None = None,
    temperature: float = 0.3,
    **kwargs,
) -> LLMResponse:
    model = model or self.default_model
    with get_tracker().track(model=model, purpose="tool_call") as record:
        completion = self._sdk.chat.completions.create(
            model=model,
            messages=messages,
            tools=tools,
            tool_choice=tool_choice,
            temperature=temperature,
            **kwargs,
        )
        msg = completion.choices[0].message
        record.input_tokens = completion.usage.prompt_tokens
        record.output_tokens = completion.usage.completion_tokens
        return LLMResponse(
            content=msg.content or "",
            tool_calls=[tc.model_dump() for tc in (msg.tool_calls or [])],
            input_tokens=record.input_tokens,
            output_tokens=record.output_tokens,
        )
```

调用方:
```python
tools = [{"type": "function", "function": {"name": "search_arxiv", "parameters": {...}}}]
resp = client.chat_with_tools(messages, tools=tools)
for tc in resp["tool_calls"]:
    name = tc["function"]["name"]; args = json.loads(tc["function"]["arguments"])
    result = dispatch(name, args)
    messages.append({"role": "tool", "tool_call_id": tc["id"], "content": result})
```

要点:**tool_calls 要单独放进 LLMResponse**,不要和 content 混。后续轮次把结果以 `role=tool` 加回 messages。

### 答案 2:`fallback` 装饰器

```python
from functools import wraps

def fallback_to(backup_role: str):
    """
    装饰器:被包的方法调用失败时,自动用 backup_role 的 client 重试一次。
    用法:@fallback_to("reasoner")  def meta_decide(...): ...
    """
    def deco(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            try:
                return fn(*args, **kwargs)
            except Exception as e:
                logger.warning("[fallback] {} 失败,降级 {}: {}", fn.__name__, backup_role, e)
                # 在 kwargs 里强制指定 role,让内部重新 get_llm
                kwargs["_forced_role"] = backup_role
                return fn(*args, **kwargs)
        return wrapper
    return deco
```

或者做一个 LLMClient 级别的包装类:
```python
class FallbackClient(LLMClient):
    def __init__(self, primary: LLMClient, backup: LLMClient):
        self.primary, self.backup = primary, backup
    def chat(self, *a, **kw):
        try:
            return self.primary.chat(*a, **kw)
        except (LLMError, Exception) as e:
            logger.warning("primary 挂,降级: {}", e)
            return self.backup.chat(*a, **kw)
```

要点:**降级只在"非业务错误"时做**。如果是 prompt 本身格式错,降级到另一个模型也救不了,别无限重试。区分方式:只对 `LLMRateLimitError` / 网络异常触发降级,`LLMAuthError` 直接抛。

### 答案 3:cache 命中率周报

`utils/cache.py` 的 `cache_llm` 装饰器里已经有命中/未命中的分支,加一个计数器:
```python
import sqlite3, time
CACHE_STATS_DB = settings.DATA_DIR / "cache_stats.db"

def _log_cache_event(hit: bool, model: str) -> None:
    with sqlite3.connect(CACHE_STATS_DB) as conn:
        conn.execute("CREATE TABLE IF NOT EXISTS events (ts REAL, hit INT, model TEXT)")
        conn.execute("INSERT INTO events VALUES (?, ?, ?)", (time.time(), int(hit), model))
```

在 `cache_llm` 内部命中分支调 `_log_cache_event(True, ...)`,未命中分支调 `(False, ...)`。

画图脚本:
```python
import sqlite3, pandas as pd, matplotlib.pyplot as plt
df = pd.read_sql("SELECT * FROM events WHERE ts >= ?",
                 sqlite3.connect(CACHE_STATS_DB), params=[time.time() - 7*86400])
df["day"] = pd.to_datetime(df["ts"], unit="s").dt.date
daily = df.groupby("day")["hit"].agg(["sum", "count"])
daily["hit_rate"] = daily["sum"] / daily["count"]
daily["hit_rate"].plot(kind="line", title="7 天 LLM Cache 命中率")
plt.savefig("cache_hit_rate.png")
```

要点:**事件表比直接算命中率更灵活**,想按 model / purpose 切片都能加分组。别直接存"每天一行"的汇总,聚合得不够灵活。
