"""
============================================================
 日志模块(utils/logger.py)
============================================================

🎓 教学目标
    - 教你为什么不用 print() 调试 Agent 项目
    - 怎么用 loguru 做结构化 + 带颜色 + 自动分文件的日志
    - Agent 项目里"状态流转 + 多 Agent 对话"太复杂,日志必须清晰

📌 设计决策
    1. 用 loguru 替代标准 logging(少 90% 样板代码)
    2. 日志分 3 个 sink(输出去向):
         - 终端:带颜色、简洁
         - app.log:全量日志,DEBUG 级别,用来事后复盘
         - errors.log:只记 ERROR+,方便一眼看到问题
    3. 提供一个 log_llm_call() 辅助函数,统一记录模型调用(token、耗时、成本)

------------------------------------------------------------
"""

from __future__ import annotations

import sys
from pathlib import Path

from loguru import logger  # loguru 的 logger 是全局单例,import 即可用

from co_scientist.config import settings


def _add_file_sink(path: Path, *, level: str, rotation: str, retention: str, compression: str | None = None) -> None:
    """
    Windows 受限环境下 multiprocessing.SimpleQueue 可能无权限创建 pipe。
    先尝试 enqueue=True,失败后降级到普通文件 sink,保证 API 服务能启动。
    """
    kwargs = {
        "level": level,
        "rotation": rotation,
        "retention": retention,
        "encoding": "utf-8",
        "enqueue": True,
    }
    if compression:
        kwargs["compression"] = compression
    try:
        logger.add(path, **kwargs)
    except PermissionError:
        kwargs["enqueue"] = False
        logger.add(path, **kwargs)


def setup_logger() -> None:
    """
    配置 loguru 日志。程序启动时调用一次即可。

    loguru 默认会有一个输出到 stderr 的 sink,我们先 remove() 干净,
    再按需添加,避免日志重复。
    """
    logger.remove()  # 清掉默认 sink

    # ---- Sink 1:终端输出 ----
    # format 字符串里的 <green>、<level> 是 loguru 的富文本标签,
    # 终端不支持颜色时会自动降级为纯文本。
    logger.add(
        sys.stderr,
        level=settings.LOG_LEVEL,
        format=(
            "<green>{time:HH:mm:ss}</green> "
            "<level>{level: <8}</level> "
            "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> "
            "<level>{message}</level>"
        ),
        colorize=True,
        backtrace=True,  # 报错时打印完整堆栈
        diagnose=True,  # 堆栈里显示变量值(开发期方便,生产可关)
    )

    # ---- Sink 2:全量日志文件 ----
    log_dir = Path(settings.DATA_DIR) / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)

    _add_file_sink(
        log_dir / "app.log",
        level="DEBUG",  # 文件里记全量,终端只显示 INFO+
        rotation="50 MB",  # 单文件超 50MB 自动切分
        retention="14 days",  # 保留 14 天
        compression="zip",  # 归档文件压缩
    )

    # ---- Sink 3:错误日志文件 ----
    _add_file_sink(
        log_dir / "errors.log",
        level="ERROR",
        rotation="10 MB",
        retention="30 days",
    )

    logger.info("✅ 日志系统已初始化 | log_dir={}", log_dir)


def log_llm_call(
    model: str,
    input_tokens: int,
    output_tokens: int,
    cost_usd: float,
    latency_s: float,
    purpose: str = "",
) -> None:
    """
    统一记录一次 LLM 调用。

    为什么专门写这个辅助函数?
      - Agent 项目最大的隐性成本是 LLM 调用,必须可观测
      - 事后用脚本 grep "LLM_CALL" 就能提取所有调用做分析
      - 统一格式,方便后续接 Grafana / Langfuse 等观测平台
    """
    logger.info(
        "LLM_CALL model={} in={} out={} cost=${:.4f} latency={:.2f}s purpose={}",
        model,
        input_tokens,
        output_tokens,
        cost_usd,
        latency_s,
        purpose,
    )


# 导出 logger,方便其他模块 `from co_scientist.utils.logger import logger`
__all__ = ["logger", "setup_logger", "log_llm_call"]
