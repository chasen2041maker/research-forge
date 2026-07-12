"""
============================================================
 本地磁盘缓存(utils/cache.py)
============================================================

🎓 教学目标
    LLM 调用贵且慢。开发时反复跑同一个 prompt,应该命中缓存而不是再花钱。
    这个模块教你:
      - 用 diskcache 做基于哈希键的本地缓存(进程重启不丢)
      - 如何把"prompt + 参数"哈希成稳定 key
      - 用装饰器让任意函数自动带缓存

📌 设计决策
    1. 存盘缓存(diskcache)而不是内存缓存:重启后还能命中,适合长研究流程
    2. key 用 SHA-256 哈希,避免 "prompt 太长做 dict key 性能问题"
    3. cache_llm() 做成装饰器,应用到 LLM 客户端方法上

------------------------------------------------------------
"""

from __future__ import annotations

import hashlib
import json
from functools import wraps
from typing import Any, Callable

from diskcache import Cache  # pip install diskcache

from co_scientist.config import settings
from co_scientist.utils.logger import logger


# 全局缓存实例。size_limit=2GB,超过会自动淘汰最旧条目(LRU)
_cache: Cache | None = None


def get_cache() -> Cache:
    global _cache
    if _cache is None:
        _cache = Cache(
            directory=str(settings.CACHE_DIR),
            size_limit=int(2e9),  # 2 GB
        )
    return _cache


def make_key(*args: Any, **kwargs: Any) -> str:
    """
    把任意参数组合打成稳定 cache key。

    为什么不直接用 (args, kwargs) 做 dict key?
      - kwargs 顺序可能不同,得到不同 key
      - dict 里可能混 pydantic 对象,不好 hash
    做法:
      - 先序列化为 JSON(sort_keys=True 保证顺序稳定)
      - 再用 SHA-256 哈希(固定长度 64 字符)
    """
    # default=str:pydantic / dataclass 等遇到就 str() 掉,保证可序列化
    payload = json.dumps(
        {"args": args, "kwargs": kwargs},
        sort_keys=True,
        ensure_ascii=False,
        default=str,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def cache_llm(ttl_seconds: int = 7 * 24 * 3600) -> Callable:
    """
    LLM 调用装饰器:命中缓存时跳过真正的 API 请求。

    Args:
        ttl_seconds: 缓存有效期,默认 7 天。
                     对研究性任务,LLM 输出变化不大,7 天够用。

    使用:
        @cache_llm(ttl_seconds=3600)
        def call_deepseek(prompt: str, ...) -> str:
            ...
    """

    def decorator(func: Callable) -> Callable:
        @wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            if not settings.ENABLE_PROMPT_CACHE:
                # 被显式关闭(如产线场景),直接走原函数
                return func(*args, **kwargs)

            cache = get_cache()
            # 注意:第一个参数一般是 self,不参与 key(否则换个实例就 miss)
            # 这里简单处理:如果第一个参数是对象,跳过它
            key_args = args[1:] if args and hasattr(args[0], "__class__") else args
            key = f"{func.__module__}.{func.__name__}:{make_key(*key_args, **kwargs)}"

            if key in cache:
                logger.debug("🗃️ 缓存命中 {}", key[:40])
                return cache[key]

            result = func(*args, **kwargs)
            cache.set(key, result, expire=ttl_seconds)
            return result

        return wrapper

    return decorator
