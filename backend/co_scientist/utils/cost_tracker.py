"""
============================================================
 成本跟踪(utils/cost_tracker.py)
============================================================

🎓 教学目标
    Agent 项目失控的最大风险是"跑一次几十刀"。本模块教你如何:
      - 在每次 LLM 调用后实时记录 token 与花费
      - 持久化到 SQLite,跨进程/重启不丢
      - 超预算时抛警告,避免后台任务无声烧钱

📌 设计决策
    1. 价格表在代码里硬编码,后续如果模型涨价改一处即可
    2. 用 SQLite 而不是内存字典:多个 worker 进程都能写
    3. 提供 add() 同步 API + 上下文管理器风格的 track() 二选一

------------------------------------------------------------
"""

from __future__ import annotations

import sqlite3
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from threading import Lock
from typing import Iterator

from co_scientist.config import settings
from co_scientist.utils.logger import logger


# ------------------------------------------------------------
# 价格表($/1M tokens),只列本项目当前运行时会用到的中转站模型。
# deepseek-* 旧条目保留给历史日志和老测试计算,运行时不再路由到 DeepSeek。
# 如果实际价格变化,改这一处即可
# ------------------------------------------------------------
PRICING: dict[str, dict[str, float]] = {
    "deepseek-chat": {
        "input": 0.27,
        "output": 1.10,
        "cache_hit": 0.028,  # Prompt Cache 命中价(便宜约 10x)
    },
    "deepseek-reasoner": {
        "input": 0.55,
        "output": 2.19,
        "cache_hit": 0.055,
    },
    "claude-opus-4-7": {
        "input": 15.00,
        "output": 75.00,
        "cache_hit": 1.50,  # Anthropic prompt caching 大约便宜 10x
    },
    "deepseek-embedding": {
        # Embedding 通常按 input token 计价,output=0
        "input": 0.02,
        "output": 0.0,
        "cache_hit": 0.02,
    },
    # ---------------- 中转站模型(USE_RELAY=true 路径)----------------
    # 价格按 OpenAI 同档位估算,中转站实际计费可能不同;真实价以中转站账单为准。
    # 这里只用来给 cost_tracker 做近似估算 + 月度预算告警,不参与计费结算。
    "gpt-5.5": {
        "input": 5.00,
        "output": 15.00,
        "cache_hit": 0.50,
    },
    "text-embedding-3-small": {
        "input": 0.02,
        "output": 0.0,
        "cache_hit": 0.02,
    },
}


@dataclass
class CallRecord:
    """单次调用记录,字段对齐数据库 schema。"""

    model: str
    input_tokens: int
    output_tokens: int
    cache_hit_tokens: int
    cost_usd: float
    latency_s: float
    purpose: str  # 业务用途标签,如 "m4_critique" / "m7_writer"
    ts: float  # Unix 时间戳


class CostTracker:
    """
    成本跟踪器(进程级单例)。

    用法:
        tracker = CostTracker()
        tracker.add("deepseek-chat", in_tok=1200, out_tok=400, purpose="m1_refiner")

        # 或上下文管理器,自动计时
        with tracker.track("deepseek-chat", purpose="m1_refiner") as record:
            resp = client.chat(...)
            record.input_tokens = resp.usage.prompt_tokens
            record.output_tokens = resp.usage.completion_tokens
    """

    def __init__(self, db_path: Path | None = None) -> None:
        self.db_path = Path(db_path or settings.DATA_DIR / "cost_tracker.db")
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = Lock()  # SQLite 自身线程不安全,加锁保护
        self._init_db()

    # ----------------------------------------------------
    # 内部:初始化表结构
    # ----------------------------------------------------
    def _init_db(self) -> None:
        with self._conn() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS llm_calls (
                    id              INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts              REAL    NOT NULL,
                    model           TEXT    NOT NULL,
                    input_tokens    INTEGER NOT NULL,
                    output_tokens   INTEGER NOT NULL,
                    cache_hit_tokens INTEGER NOT NULL DEFAULT 0,
                    cost_usd        REAL    NOT NULL,
                    latency_s       REAL    NOT NULL,
                    purpose         TEXT    NOT NULL DEFAULT ''
                );
                """
            )
            # 索引:按时间和模型查询是高频操作
            conn.execute("CREATE INDEX IF NOT EXISTS idx_llm_ts ON llm_calls(ts);")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_llm_model ON llm_calls(model);")

    def _conn(self) -> sqlite3.Connection:
        # check_same_thread=False:允许跨线程使用同一连接(我们用 Lock 保护)
        return sqlite3.connect(self.db_path, check_same_thread=False)

    # ----------------------------------------------------
    # 计算单次成本
    # ----------------------------------------------------
    @staticmethod
    def calc_cost(
        model: str,
        input_tokens: int,
        output_tokens: int,
        cache_hit_tokens: int = 0,
    ) -> float:
        """
        按价格表算出本次调用美元成本。

        计算公式:
          cost = (input - cache_hit) * input_price
               + cache_hit          * cache_price
               + output             * output_price

        所有价格按 1M tokens 计,所以最后除以 1_000_000。
        """
        if model not in PRICING:
            logger.warning("未知模型 {},按 gpt-5.5 价格估算", model)
            p = PRICING["gpt-5.5"]
        else:
            p = PRICING[model]

        billable_input = max(0, input_tokens - cache_hit_tokens)
        cost = (
            billable_input * p["input"]
            + cache_hit_tokens * p["cache_hit"]
            + output_tokens * p["output"]
        ) / 1_000_000
        return round(cost, 6)

    # ----------------------------------------------------
    # 记录一次调用(直接 API)
    # ----------------------------------------------------
    def add(
        self,
        model: str,
        input_tokens: int,
        output_tokens: int,
        cache_hit_tokens: int = 0,
        latency_s: float = 0.0,
        purpose: str = "",
    ) -> CallRecord:
        cost = self.calc_cost(model, input_tokens, output_tokens, cache_hit_tokens)
        record = CallRecord(
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cache_hit_tokens=cache_hit_tokens,
            cost_usd=cost,
            latency_s=latency_s,
            purpose=purpose,
            ts=time.time(),
        )
        with self._lock, self._conn() as conn:
            conn.execute(
                """INSERT INTO llm_calls
                   (ts, model, input_tokens, output_tokens, cache_hit_tokens,
                    cost_usd, latency_s, purpose)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    record.ts,
                    record.model,
                    record.input_tokens,
                    record.output_tokens,
                    record.cache_hit_tokens,
                    record.cost_usd,
                    record.latency_s,
                    record.purpose,
                ),
            )
        # 超预算告警:每次调用后检查月累计
        self._check_budget()

        # run 级成本护栏:如果当前在 budget_guard(...) 上下文里,
        # 累加本次花费,超限则抛 BudgetExceeded 中断后续调用。
        # 延迟 import 防环:cost_tracker 被大量模块 import,
        # budget_guard 反过来只依赖 utils.logger,挪到函数内部导入最稳。
        from co_scientist.utils.budget_guard import charge as _bg_charge
        _bg_charge(cost, purpose=purpose)

        return record

    # ----------------------------------------------------
    # 上下文管理器风格,自动计时
    # ----------------------------------------------------
    @contextmanager
    def track(self, model: str, purpose: str = "") -> Iterator[CallRecord]:
        """
        with 块内可以修改 input_tokens / output_tokens,
        退出时自动计算耗时和成本并落库。
        """
        start = time.time()
        record = CallRecord(
            model=model,
            input_tokens=0,
            output_tokens=0,
            cache_hit_tokens=0,
            cost_usd=0.0,
            latency_s=0.0,
            purpose=purpose,
            ts=start,
        )
        try:
            yield record  # 把 record 交给 with 块用
        finally:
            record.latency_s = time.time() - start
            self.add(
                model=record.model,
                input_tokens=record.input_tokens,
                output_tokens=record.output_tokens,
                cache_hit_tokens=record.cache_hit_tokens,
                latency_s=record.latency_s,
                purpose=record.purpose,
            )

    # ----------------------------------------------------
    # 月累计 / 预算检查
    # ----------------------------------------------------
    def month_total_usd(self) -> float:
        """当前自然月的累计花费。"""
        # SQLite 用 strftime 取年月,简单粗暴
        with self._conn() as conn:
            row = conn.execute(
                """SELECT COALESCE(SUM(cost_usd), 0)
                   FROM llm_calls
                   WHERE strftime('%Y-%m', ts, 'unixepoch') = strftime('%Y-%m', 'now')"""
            ).fetchone()
            return float(row[0])

    def _check_budget(self) -> None:
        spent = self.month_total_usd()
        budget = settings.MONTHLY_BUDGET_USD
        if budget <= 0:
            return
        ratio = spent / budget
        if ratio >= 1.0:
            logger.error("💸 预算已超支!{:.2f}/{:.2f} USD ({:.0%})", spent, budget, ratio)
        elif ratio >= 0.8:
            logger.warning("⚠️ 已用 {:.0%} 月预算 ({:.2f}/{:.2f} USD)", ratio, spent, budget)


# 进程级单例,避免每个模块都 new 一个
_tracker: CostTracker | None = None


def get_tracker() -> CostTracker:
    global _tracker
    if _tracker is None:
        _tracker = CostTracker()
    return _tracker
