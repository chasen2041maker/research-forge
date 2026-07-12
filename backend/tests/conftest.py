import sys
from pathlib import Path

import pytest

# 让 pytest 能 import co_scientist 包
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def pytest_configure(config: pytest.Config) -> None:
    """集中注册自定义 marker,避免 PytestUnknownMarkWarning。"""
    config.addinivalue_line("markers", "net: 需要外网访问的集成测试(--run-net 打开)")
    config.addinivalue_line("markers", "eval: Agent eval 测试(见 tests/evals/,--run-evals 打开)")
