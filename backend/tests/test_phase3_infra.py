"""
============================================================
 Phase 3 单元测试:观测性 + Extended Thinking + Budget Guard
============================================================

🎓 教学目标
    Phase 3 新增的三件基础设施,各自有独立测试覆盖:
      Part A:observability(LangSmith env 初始化)
      Part B:Extended Thinking(thinking 参数透传给 SDK)
      Part C:Budget Guard(累计超限抛 BudgetExceeded)
"""

from __future__ import annotations

import os
from typing import Any

import pytest


# ============================================================
# Part A:observability / LangSmith
# ============================================================


def test_langsmith_skip_when_tracing_off(monkeypatch: pytest.MonkeyPatch) -> None:
    """settings.LANGSMITH_TRACING=False 时不应 export 任何 env。"""
    from co_scientist.config import settings as s
    from co_scientist.utils import observability

    observability.reset_for_test()
    monkeypatch.setattr(s, "LANGSMITH_TRACING", False)
    assert observability.setup_langsmith() is False
    assert "LANGCHAIN_API_KEY" not in os.environ


def test_langsmith_skip_when_no_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """开了 tracing 但没 Key 应该跳过 + warn,不抛异常。"""
    from pydantic import SecretStr

    from co_scientist.config import settings as s
    from co_scientist.utils import observability

    observability.reset_for_test()
    monkeypatch.setattr(s, "LANGSMITH_TRACING", True)
    monkeypatch.setattr(s, "LANGSMITH_API_KEY", SecretStr(""))
    assert observability.setup_langsmith() is False


def test_langsmith_exports_env_when_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    """正确配置时应 export LANGCHAIN_* 和 LANGSMITH_* 两套 env。"""
    from pydantic import SecretStr

    from co_scientist.config import settings as s
    from co_scientist.utils import observability

    observability.reset_for_test()
    monkeypatch.setattr(s, "LANGSMITH_TRACING", True)
    monkeypatch.setattr(s, "LANGSMITH_API_KEY", SecretStr("fake-key"))
    monkeypatch.setattr(s, "LANGSMITH_PROJECT", "test-proj")

    assert observability.setup_langsmith() is True
    assert os.environ["LANGCHAIN_TRACING_V2"] == "true"
    assert os.environ["LANGCHAIN_API_KEY"] == "fake-key"
    assert os.environ["LANGCHAIN_PROJECT"] == "test-proj"
    assert os.environ["LANGSMITH_TRACING"] == "true"

    observability.reset_for_test()  # 清理防污染其他测试


def test_langsmith_is_idempotent(monkeypatch: pytest.MonkeyPatch) -> None:
    """同进程多次调用应只真正生效一次。"""
    from pydantic import SecretStr

    from co_scientist.config import settings as s
    from co_scientist.utils import observability

    observability.reset_for_test()
    monkeypatch.setattr(s, "LANGSMITH_TRACING", True)
    monkeypatch.setattr(s, "LANGSMITH_API_KEY", SecretStr("k"))

    assert observability.setup_langsmith() is True
    # 第二次也返回 True(已初始化),但不会重复 export
    assert observability.setup_langsmith() is True

    observability.reset_for_test()


# ============================================================
# Part B:Extended Thinking(Claude client)
# ============================================================


class _FakeClaudeSDK:
    """假 Anthropic SDK,捕获 messages.create 的参数供断言。"""

    def __init__(self) -> None:
        self.last_kwargs: dict[str, Any] = {}
        self.messages = self  # messages.create 同类自引用,简化 mock

    def create(self, **kwargs: Any) -> Any:
        self.last_kwargs = kwargs

        # 返回一个最小可用的 resp
        class _Usage:
            input_tokens = 100
            output_tokens = 50
            cache_read_input_tokens = 0

        class _Block:
            type = "text"
            text = "ok"

        class _Resp:
            content = [_Block()]
            usage = _Usage()

        return _Resp()


def _make_client_with_fake_sdk(monkeypatch: pytest.MonkeyPatch) -> Any:
    from co_scientist.llm.claude import ClaudeClient

    fake = _FakeClaudeSDK()
    client = ClaudeClient()
    monkeypatch.setattr(client, "_sdk", fake)
    return client, fake


def test_thinking_disabled_by_default_for_non_meta(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """普通 purpose 不应传 thinking 参数。"""
    from co_scientist.config import settings as s

    monkeypatch.setattr(s, "CLAUDE_THINKING_BUDGET_DEFAULT", 0)
    monkeypatch.setattr(s, "CLAUDE_THINKING_BUDGET_META", 4000)

    client, fake = _make_client_with_fake_sdk(monkeypatch)
    client.chat(
        [{"role": "user", "content": "hi"}],
        purpose="m1_check",
    )
    assert "thinking" not in fake.last_kwargs


def test_thinking_auto_enabled_for_meta_purpose(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """purpose 含 'meta' 应自动启用 Extended Thinking,temp=1。"""
    from co_scientist.config import settings as s

    monkeypatch.setattr(s, "CLAUDE_THINKING_BUDGET_DEFAULT", 0)
    monkeypatch.setattr(s, "CLAUDE_THINKING_BUDGET_META", 4000)

    client, fake = _make_client_with_fake_sdk(monkeypatch)
    client.chat(
        [{"role": "user", "content": "hi"}],
        purpose="m4_meta_decision",
        temperature=0.2,  # 就算显式传了低温也应被覆盖为 1
    )
    assert fake.last_kwargs.get("thinking") == {
        "type": "enabled",
        "budget_tokens": 4000,
    }
    assert fake.last_kwargs["temperature"] == 1.0


def test_thinking_explicit_override(monkeypatch: pytest.MonkeyPatch) -> None:
    """调用方显式传 thinking_budget 应压过 purpose 默认。"""
    client, fake = _make_client_with_fake_sdk(monkeypatch)
    client.chat(
        [{"role": "user", "content": "hi"}],
        purpose="m4_meta",
        thinking_budget=8000,
    )
    assert fake.last_kwargs["thinking"]["budget_tokens"] == 8000


def test_thinking_explicit_zero_disables(monkeypatch: pytest.MonkeyPatch) -> None:
    """显式传 0 应关闭,即使 purpose 是 meta。"""
    from co_scientist.config import settings as s

    monkeypatch.setattr(s, "CLAUDE_THINKING_BUDGET_META", 4000)

    client, fake = _make_client_with_fake_sdk(monkeypatch)
    client.chat(
        [{"role": "user", "content": "hi"}],
        purpose="m4_meta",
        thinking_budget=0,
    )
    assert "thinking" not in fake.last_kwargs


# ============================================================
# Part C:Budget Guard
# ============================================================


def test_budget_guard_charges_accumulate() -> None:
    from co_scientist.utils.budget_guard import (
        budget_guard,
        charge,
        current_spent,
    )

    with budget_guard(1.0):
        charge(0.3)
        assert current_spent() == pytest.approx(0.3)
        charge(0.2)
        assert current_spent() == pytest.approx(0.5)


def test_budget_guard_raises_on_exceed() -> None:
    from co_scientist.utils.budget_guard import BudgetExceeded, budget_guard, charge

    with pytest.raises(BudgetExceeded) as exc_info:
        with budget_guard(1.0):
            charge(0.6)
            charge(0.5)  # 累计 1.1 > 1.0

    err = exc_info.value
    assert err.spent == pytest.approx(1.1)
    assert err.budget == pytest.approx(1.0)


def test_budget_guard_outside_context_noop() -> None:
    """不在 budget_guard 上下文里时,charge 应完全无副作用。"""
    from co_scientist.utils.budget_guard import charge, current_spent

    charge(999.0)  # 这是"野生"调用,应被忽略
    assert current_spent() == 0.0


def test_budget_guard_isolates_per_run() -> None:
    """连续两个 run 互不干扰:run1 的累计不影响 run2。"""
    from co_scientist.utils.budget_guard import budget_guard, charge, current_spent

    with budget_guard(1.0):
        charge(0.7)

    # run1 出来,进入 run2
    with budget_guard(1.0):
        assert current_spent() == 0.0  # 干净重置
        charge(0.2)
        assert current_spent() == pytest.approx(0.2)


def test_budget_guard_zero_limit_disabled() -> None:
    """limit_usd=0 应当做'不限'(用户有意关闭),任何 charge 都不抛。"""
    from co_scientist.utils.budget_guard import budget_guard, charge

    with budget_guard(0.0):
        charge(999.0)
        charge(9999.0)
    # 不抛异常即算通过


def test_cost_tracker_integration_with_budget(monkeypatch: pytest.MonkeyPatch) -> None:
    """
    端到端:CostTracker.add() 里挂的 budget hook 真的会触发超限。
    用真的 CostTracker,但给一个临时 SQLite 文件避免污染。
    """
    import tempfile
    from pathlib import Path

    from co_scientist.utils.budget_guard import BudgetExceeded, budget_guard
    from co_scientist.utils.cost_tracker import CostTracker

    # calc_cost 对 Claude Opus 4.7 很贵,一次 100K+10K token 足以超 $0.001
    # Windows 下 SQLite 连接未关闭就删 tempdir 会被锁 → ignore_cleanup_errors
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
        tracker = CostTracker(db_path=Path(td) / "cost.db")

        with pytest.raises(BudgetExceeded):
            with budget_guard(0.001):  # 只给 0.1 美分,一次调用就超
                tracker.add(
                    model="claude-opus-4-7",
                    input_tokens=100_000,  # 10 万 token Claude → 贵
                    output_tokens=10_000,
                    purpose="m4_meta",
                )
