"""
============================================================
 Budget Guard(utils/budget_guard.py)
============================================================

🎓 教学目标
    Agent 长跑时最大的运维风险:**LLM 在循环里出 bug,反复调用,一次跑把
    API 账户打爆**。本模块给"一次 run"设一个硬性成本上限,超了直接抛异常
    中断后续调用。

💡 为什么放在 utils 而不是 LLM client 里做
    - 一个 run 会穿过 m1/m2/.../m7 多个模块,还夹着 Orchestrator、Reviewer、
      Judge 等子调用。"成本上限"是**贯穿整条 run**的横切关注点,不属于任何
      单个模块。
    - 用 contextvars 做 run 级别的上下文绑定,不污染业务接口。cost_tracker.add()
      只要在内部调 budget_guard.charge(cost),其他代码零感知。

💡 为什么不用装饰器、不用全局单例
    - 装饰器需要每个业务函数都装饰一遍,冗余
    - 全局单例无法区分"并发跑多个 run"(API 场景会有这个需求)
    - contextvars 是 Python 3.7+ 的标准做法,天然支持 asyncio 并发隔离,
      每个 run 独立预算,互不干扰

📌 对标业界实践
    - Devin / Cognition:每个 task 设成本上限,超限停,人工决定是否继续
    - Replit Agent:类似机制 + token 上限
    - 长跑 Agent 生产级必做的事,不做会出大事故

------------------------------------------------------------
"""

from __future__ import annotations

import contextvars
from contextlib import contextmanager
from typing import Iterator

from co_scientist.utils import logger


# ------------------------------------------------------------
# ContextVar 存每个 run 的累计花费和预算
# ------------------------------------------------------------
# 为什么用 ContextVar 而不是普通全局变量:
#   - ContextVar 天然支持 asyncio,每个任务自己的 context 互相隔离
#   - FastAPI 多请求并发跑 m4 不会互相"偷钱"
#   - 标准库提供,零依赖
_RUN_SPENT: contextvars.ContextVar[float] = contextvars.ContextVar(
    "co_scientist_run_spent", default=0.0
)
_RUN_BUDGET: contextvars.ContextVar[float] = contextvars.ContextVar(
    "co_scientist_run_budget", default=0.0
)


class BudgetExceeded(Exception):
    """
    单次 run 成本超限时抛出。

    为什么不让它继承 LLMError:
      - LLMError 是"LLM 调用层的问题"(密钥/限流/服务端错),可重试
      - BudgetExceeded 是"业务层的硬约束"(预算),重试也没用,必须中断
      - 分类清晰后,上层 except 能按语义精确兜底
    """

    def __init__(self, spent: float, budget: float) -> None:
        self.spent = spent
        self.budget = budget
        super().__init__(
            f"run 成本超限: {spent:.4f} USD > {budget:.4f} USD"
        )


@contextmanager
def budget_guard(limit_usd: float) -> Iterator[None]:
    """
    进入 with 块时重置 run 级的累计花费,设置本次的上限。

    Args:
        limit_usd: 本次 run 的成本上限(美元)。≤ 0 表示不限(不推荐)。

    Usage:
        with budget_guard(1.0):
            result = graph.invoke(initial_state, ...)

    ▍为什么不从 settings 直接读预算
        - 同一个服务可能给不同用户不同预算(订阅等级、实验组)
        - 测试场景想用更小/更大预算
        - 显式传参比"隐式依赖全局配置"更好
        上层调用 (cli / api / run_pipeline) 按自己语境决定传多少。
    """
    spent_token = _RUN_SPENT.set(0.0)
    budget_token = _RUN_BUDGET.set(max(0.0, limit_usd))
    logger.info("[budget] 启动成本护栏,上限 {:.2f} USD", limit_usd)
    try:
        yield
    finally:
        final_spent = _RUN_SPENT.get()
        logger.info("[budget] run 结束,本次花费 {:.4f} USD / 上限 {:.2f}",
                    final_spent, limit_usd)
        _RUN_SPENT.reset(spent_token)
        _RUN_BUDGET.reset(budget_token)


def charge(cost_usd: float, *, purpose: str = "") -> None:
    """
    记账 + 检查上限。由 cost_tracker 内部在每次 LLM 调用后调用。

    Args:
        cost_usd: 本次调用的成本(美元)
        purpose: 业务标签(用于 BudgetExceeded 消息里定位)

    Raises:
        BudgetExceeded: 累计超过预算时

    ▍为什么在这里抛而不是记 flag 让调用方检查
        - 抛异常能立刻中断整条 m? 的后续节点,避免"再调 5 次才发现超了"
        - LangGraph 的 safe_node 会捕获异常写 error_log,**业务流仍优雅结束**,
          不会留下半吊子 state

    ▍为什么不 atomic 操作(锁)
        ContextVar 每个 run 独立实例,单 run 内部调用是串行(LLM 调用是同步的或
        await 点处让出控制权),不需要锁。asyncio 并发跑多个 run 时,各 run 的
        ContextVar 互不干扰。
    """
    budget = _RUN_BUDGET.get()
    if budget <= 0:
        # 没进入 budget_guard,当做"不限",直接返回
        return

    new_total = _RUN_SPENT.get() + cost_usd
    _RUN_SPENT.set(new_total)

    if new_total > budget:
        logger.error(
            "[budget] 💸 超限!累计 {:.4f} > 上限 {:.4f},触发 BudgetExceeded (purpose={})",
            new_total, budget, purpose,
        )
        raise BudgetExceeded(spent=new_total, budget=budget)


def current_spent() -> float:
    """查当前 run 已花多少(调试用)。不在 guard 上下文里返回 0。"""
    return _RUN_SPENT.get()


def current_budget() -> float:
    """查当前 run 的预算上限(调试用)。不在 guard 上下文里返回 0。"""
    return _RUN_BUDGET.get()
