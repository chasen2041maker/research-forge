# 01 - 基础工程

> 配置管理、日志、缓存、成本跟踪、SQLite。这一章是后面所有章节的地基。

---

## 1.1 配置管理:`pydantic-settings`

### 痛点
新手常用 `os.getenv("KEY")` 读环境变量,问题:
- 永远返回字符串,布尔/数字要手动 `int(...)` `bool(...)`
- 没默认值要写 `or "default"`,代码重复
- IDE 没补全,改个 key 名字就 grep 满项目
- API key 泄露到日志里

### 解决:声明式配置类

```python
from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", case_sensitive=True)

    DEEPSEEK_API_KEY: SecretStr = SecretStr("")  # 自动脱敏
    MONTHLY_BUDGET_USD: float = 15.0             # 自动 float 转换
    ENABLE_PROMPT_CACHE: bool = True             # "true"/"false" 自动 bool

settings = Settings()
print(settings.MONTHLY_BUDGET_USD * 1.5)  # 已经是 float
print(settings.DEEPSEEK_API_KEY)  # SecretStr('**********')
```

### 优先级
代码参数 > 环境变量 > `.env` 文件 > 类默认值

### 单例 + lru_cache
```python
@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
```
为什么不直接 `settings = Settings()`?
- **延迟加载**:import 时不读 .env,方便测试 mock
- **显式调用**:读代码时知道"这里在拿配置"

### SecretStr 防泄露
```python
api_key: SecretStr = SecretStr("sk-xxx")
print(api_key)                    # SecretStr('**********')  ← 日志安全
client = OpenAI(api_key=api_key.get_secret_value())  # 真正用时取明文
```

📌 **项目对应**:`backend/co_scientist/config/settings.py`

---

## 1.2 日志:`loguru` 替代标准 `logging`

### 为什么不用 print
Agent 项目状态流转 + 多 Agent 并发,print 完全无法回溯。
日志必须有:**时间戳、模块、级别、可分文件、可旋转**。

### 为什么不用 `logging`
标准库要写 ~30 行配置才能有彩色输出 + 文件 + 旋转。
loguru 一行搞定:

```python
from loguru import logger
logger.add("app.log", rotation="50 MB", retention="14 days", encoding="utf-8")
logger.info("hello {}", "world")  # 自动彩色 + 写文件
```

### 多 sink 模式(本项目用法)
```python
logger.remove()  # 清掉默认
logger.add(sys.stderr, level="INFO", colorize=True)             # 终端
logger.add("app.log", level="DEBUG", rotation="50 MB")          # 全量
logger.add("errors.log", level="ERROR")                         # 仅错误
```

### 关键参数
| 参数 | 说明 |
|------|------|
| `rotation="50 MB"` | 单文件超过 50MB 自动切分 |
| `retention="14 days"` | 14 天前的归档自动删 |
| `compression="zip"` | 旧文件压缩节省磁盘 |
| `enqueue=True` | 多进程安全(Celery worker 必加) |
| `encoding="utf-8"` | **Windows 必加**,否则中文乱码 |

### 结构化日志技巧
统一格式方便事后 grep:
```python
logger.info("LLM_CALL model={} in={} out={} cost=${:.4f}",
            model, in_tok, out_tok, cost)
```
之后 `grep "LLM_CALL" app.log | awk '{print $5}'` 就能提取所有调用。

📌 **项目对应**:`backend/co_scientist/utils/logger.py`

---

## 1.3 本地缓存:`diskcache`

### 痛点
LLM 调用贵且慢。开发期反复跑同一 prompt 应该秒回。

### 选择
- 内存 dict:进程重启就丢
- Redis:要部署
- **diskcache**:基于 SQLite,纯本地,API 像 dict

```python
from diskcache import Cache
cache = Cache("./data/cache", size_limit=int(2e9))  # 2GB,LRU 淘汰

cache.set("key", value, expire=3600)  # TTL 1 小时
cache["key"]  # 像 dict 一样读
"key" in cache  # 检查存在
```

### 给函数加缓存(装饰器模式)
```python
def cache_llm(ttl=7*24*3600):
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            key = make_key(*args, **kwargs)
            if key in cache:
                return cache[key]
            result = func(*args, **kwargs)
            cache.set(key, result, expire=ttl)
            return result
        return wrapper
    return decorator

@cache_llm()
def call_llm(prompt: str): ...
```

### 稳定 key:JSON + SHA-256
```python
def make_key(*args, **kwargs):
    payload = json.dumps({"args": args, "kwargs": kwargs},
                         sort_keys=True, default=str)  # sort_keys 关键!
    return hashlib.sha256(payload.encode()).hexdigest()
```
为什么不用 `(args, kwargs)` 做 dict key?
- kwargs 顺序变化产生不同 key
- pydantic 对象不 hashable
- prompt 太长当 dict key 性能差

📌 **项目对应**:`backend/co_scientist/utils/cache.py`

---

## 1.4 成本跟踪:SQLite + 上下文管理器

### 痛点
Agent 项目最大隐性成本是 LLM 调用。失控的话一天烧几百刀。

### 设计要点
1. **持久化**:用 SQLite,跨进程/重启不丢
2. **价格表硬编码**:供应商价格变化只改一处
3. **两种 API**:同步 add() + 上下文管理器 track()

### 价格表
```python
PRICING = {
    "deepseek-chat":     {"input": 0.27, "output": 1.10, "cache_hit": 0.028},
    "deepseek-reasoner": {"input": 0.55, "output": 2.19, "cache_hit": 0.055},
    "claude-opus-4-7":   {"input": 15.00, "output": 75.00, "cache_hit": 1.50},
}
```
单位:**$/1M tokens**。最后除以 1_000_000。

### 上下文管理器风格(推荐)
```python
@contextmanager
def track(self, model, purpose=""):
    start = time.time()
    record = CallRecord(model=model, purpose=purpose, ...)
    try:
        yield record  # 把 record 交给 with 块
    finally:
        record.latency_s = time.time() - start
        self.add(...)  # 自动落库

# 使用
with tracker.track("deepseek-chat", purpose="m1") as rec:
    resp = llm.chat(...)
    rec.input_tokens = resp.usage.prompt_tokens
    rec.output_tokens = resp.usage.completion_tokens
# 退出 with 时自动计时 + 落库
```

为什么用 `try/finally`?即使 LLM 抛异常,也要记录已用 token。

### 预算告警
```python
def _check_budget(self):
    spent = self.month_total_usd()
    if spent / budget > 0.8:
        logger.warning("⚠️ 已用 80% 月预算")
```

📌 **项目对应**:`backend/co_scientist/utils/cost_tracker.py`

---

## 1.5 SQLite 实战要点

### 线程安全
```python
sqlite3.connect(path, check_same_thread=False)  # 允许跨线程
self._lock = Lock()  # 自己加锁保护
```
SQLite 自身写不是线程安全,要么单线程要么加锁。

### 索引
高频查询字段必加索引:
```sql
CREATE INDEX IF NOT EXISTS idx_llm_ts ON llm_calls(ts);
CREATE INDEX IF NOT EXISTS idx_llm_model ON llm_calls(model);
```
不加索引,数据多了 `WHERE` 查询会慢 100 倍。

### 当月聚合
```sql
SELECT SUM(cost_usd) FROM llm_calls
WHERE strftime('%Y-%m', ts, 'unixepoch') = strftime('%Y-%m', 'now')
```
`unixepoch` 把 float 时间戳转成日期。

---

## 1.6 异常重试:`tenacity`

```python
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

@retry(
    retry=retry_if_exception_type(RateLimitError),  # 只对限流重试
    stop=stop_after_attempt(4),                     # 最多 4 次
    wait=wait_exponential(multiplier=1, min=1, max=8),  # 1s, 2s, 4s, 8s
    reraise=True,                                    # 失败抛原始异常
)
def call_api(): ...
```

### 哪些错误该重试?
| 错误 | 重试? | 原因 |
|------|-------|------|
| RateLimitError | ✅ | 退避后通常成功 |
| 5xx 服务端错误 | ✅ | 临时故障 |
| 网络超时 | ✅ | |
| AuthenticationError | ❌ | 密钥错重试也错 |
| InvalidRequest | ❌ | 参数错 |

### 异步版
```python
from tenacity import AsyncRetrying

async for attempt in AsyncRetrying(stop=stop_after_attempt(3), reraise=True):
    with attempt:
        result = await async_call()
        return result
```

---

## 📝 面试常见问题

1. **为什么用 pydantic-settings 不直接 os.getenv?**
   - 类型安全、默认值、IDE 补全、SecretStr 脱敏

2. **loguru vs logging?**
   - loguru 配置量少 90%、彩色、自动旋转、enqueue 多进程安全

3. **如何给一个慢函数加缓存?**
   - diskcache + 装饰器 + sha256(JSON sort_keys=True) 做 key

4. **如何在 Python 里实现单例?**
   - `@lru_cache(maxsize=1)` 装饰工厂函数

5. **SQLite 线程安全怎么办?**
   - `check_same_thread=False` + 自加 Lock

6. **tenacity 哪些异常该重试,哪些不该?**
   - 临时性(限流、5xx、超时)重试;永久性(鉴权、参数)不重试

---

## 🎯 练手题

1. 给 `Settings` 加一个 `MAX_PARALLEL_SEARCHES: int = 5` 配置,在模块 2 用上
2. 给 `logger` 加第 4 个 sink:把所有 `LLM_CALL` 单独写到 `llm_calls.log`
3. 写一个 CLI 子命令 `cost report`,按 purpose 分组输出本周 token 消耗 top 10

---

## ✅ 练手题参考答案

### 答案 1:新增 `MAX_PARALLEL_SEARCHES` 配置

在 `config/settings.py` 的 `Settings` 类里加一行(放在"运行时"区域):
```python
MAX_PARALLEL_SEARCHES: int = Field(
    default=5,
    description="模块 2 并行检索源数量上限,防止对单一源造成压力",
)
```

在 `modules/m2_retriever/retriever.py` 里用 `asyncio.Semaphore` 限流:
```python
from co_scientist.config import settings

sem = asyncio.Semaphore(settings.MAX_PARALLEL_SEARCHES)

async def _guarded(coro):
    async with sem:
        return await coro

tasks = [_guarded(search_arxiv(q)) for q in queries] + \
        [_guarded(search_openalex(q)) for q in queries] + \
        [_guarded(search_semantic_scholar(q)) for q in queries]
results = await asyncio.gather(*tasks, return_exceptions=True)
```

要点:**Semaphore 在 coroutine 未被 gather 调度前不占配额**,所以把它包在一个 helper 里等真正进到协程体才 acquire。

### 答案 2:LLM_CALL 独立 sink

改 `utils/logger.py` 的 `setup_logger`:
```python
from loguru import logger as _logger

def setup_logger() -> None:
    _logger.remove()
    _logger.add(sys.stderr, level=settings.LOG_LEVEL)
    _logger.add(settings.DATA_DIR / "app.log", rotation="10 MB", retention="7 days")
    _logger.add(settings.DATA_DIR / "error.log", level="ERROR", rotation="10 MB")
    # 第 4 个 sink:只收 LLM_CALL
    _logger.add(
        settings.DATA_DIR / "llm_calls.log",
        filter=lambda rec: rec["extra"].get("tag") == "LLM_CALL",
        rotation="50 MB",
        retention="30 days",
        format="{time:YYYY-MM-DD HH:mm:ss} | {extra[model]} | {extra[purpose]} | {message}",
    )
```

调用处(`llm/base.py` 或 `cost_tracker.py`):
```python
logger.bind(tag="LLM_CALL", model=model, purpose=purpose).info(
    f"tokens in={input_tokens} out={output_tokens} cost=${cost:.4f}"
)
```

要点:**loguru 的 `filter` 回调决定本条消息是否落到这个 sink**,配合 `bind` 加 tag 就能精确过滤,不影响其他 sink。

### 答案 3:`cost report` CLI

先在 `utils/cost_tracker.py` 补一个聚合方法:
```python
def top_purposes(self, days: int = 7, limit: int = 10) -> list[tuple[str, int, float]]:
    """返回 [(purpose, total_tokens, total_usd), ...] 按 tokens 降序。"""
    since = time.time() - days * 86400
    with sqlite3.connect(self.db_path) as conn:
        rows = conn.execute(
            """
            SELECT purpose,
                   SUM(input_tokens + output_tokens) AS tokens,
                   SUM(cost_usd) AS usd
            FROM llm_calls WHERE ts >= ?
            GROUP BY purpose ORDER BY tokens DESC LIMIT ?
            """,
            (since, limit),
        ).fetchall()
    return rows
```

在 `cli.py` 里加子命令:
```python
@app.command("cost-report")
def cost_report(days: int = 7) -> None:
    rows = get_tracker().top_purposes(days=days, limit=10)
    table = Table(title=f"最近 {days} 天 token top 10")
    table.add_column("purpose"); table.add_column("tokens", justify="right"); table.add_column("USD", justify="right")
    for p, tks, usd in rows:
        table.add_row(p, f"{tks:,}", f"${usd:.4f}")
    console.print(table)
```

要点:**聚合逻辑写在 tracker 里(SQL 一次聚合),CLI 只负责展示**。别把聚合搬到 Python 循环,SQLite 做 GROUP BY 比 Python 快 10 倍起。
