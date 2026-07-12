"""
单元测试:SkillLibrary(L3 工具自生成)纯逻辑。

不需要真 LLM / 真沙箱 —— 直接用字符串代码测试 ast 解析 + SQLite 操作。
"""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture
def lib(tmp_path: Path):
    from co_scientist.appendix.evolve.skill_library import SkillLibrary

    return SkillLibrary(db_path=tmp_path / "skills.db")


SAMPLE_OK = """
def rrf_fusion(result_lists: list, k: int = 60) -> list:
    merged = {}
    for rlist in result_lists:
        for rank, item in enumerate(rlist, start=1):
            merged[item] = merged.get(item, 0.0) + 1.0 / (k + rank)
    return sorted(merged.items(), key=lambda x: -x[1])
"""


def test_register_ok(lib) -> None:
    sid = lib.register(SAMPLE_OK, "RRF 融合多个排序列表")
    assert sid and len(sid) == 10


def test_register_bad_syntax(lib) -> None:
    assert lib.register("def broken(:", "syntax error demo") is None


def test_register_no_function(lib) -> None:
    code = "x = 1\ny = 2\n"
    assert lib.register(code, "just assignments") is None


def test_register_signature_captures_args(lib) -> None:
    lib.register(SAMPLE_OK, "...")
    items = lib.list_all()
    assert len(items) == 1
    sig = items[0].signature
    assert sig.startswith("rrf_fusion(")
    assert "result_lists: list" in sig
    assert "k: int" in sig


def test_retrieve_hits(lib) -> None:
    lib.register(SAMPLE_OK, "RRF 融合 多个排序列表")
    hits = lib.retrieve("融合 排序")
    assert len(hits) == 1
    assert hits[0]["name"] == "rrf_fusion"


def test_retrieve_empty_task(lib) -> None:
    lib.register(SAMPLE_OK, "RRF 融合")
    assert lib.retrieve("") == []
    assert lib.retrieve("   ") == []


def test_retrieve_no_match(lib) -> None:
    lib.register(SAMPLE_OK, "RRF 融合")
    assert lib.retrieve("完全不相关的词语 xyzzy") == []


def test_retrieve_ranks_by_overlap(lib) -> None:
    # 两个技能,一个描述高度匹配,一个弱匹配
    lib.register(SAMPLE_OK, "融合 排序 列表")
    lib.register(
        "def bow_score(a: str, b: str) -> int:\n    return len(set(a) & set(b))\n",
        "词袋 评分 简单",
    )
    hits = lib.retrieve("融合 排序 列表")
    assert hits[0]["name"] == "rrf_fusion"


def test_same_name_replaces(lib) -> None:
    """同名注册覆盖旧版本 —— 用新描述验证。"""
    lib.register(SAMPLE_OK, "old desc")
    lib.register(SAMPLE_OK, "new desc")
    items = lib.list_all()
    assert len(items) == 1
    assert items[0].description == "new desc"


def test_get_code_roundtrip(lib) -> None:
    lib.register(SAMPLE_OK, "...")
    assert "def rrf_fusion" in lib.get_code("rrf_fusion")
    assert lib.get_code("nonexistent") is None


def test_bump_uses(lib) -> None:
    lib.register(SAMPLE_OK, "...")
    assert lib.list_all()[0].uses == 0
    lib.bump_uses("rrf_fusion")
    lib.bump_uses("rrf_fusion")
    assert lib.list_all()[0].uses == 2


def test_delete(lib) -> None:
    lib.register(SAMPLE_OK, "...")
    assert lib.delete("rrf_fusion") is True
    assert lib.delete("rrf_fusion") is False  # 第二次已经没了
    assert lib.list_all() == []


def test_format_skills_for_prompt() -> None:
    from co_scientist.appendix.evolve.skill_library import format_skills_for_prompt

    # 空列表返回空字符串
    assert format_skills_for_prompt([]) == ""

    text = format_skills_for_prompt([
        {"name": "f1", "signature": "f1(x: int)", "description": "做 x 加倍"},
        {"name": "f2", "signature": "f2(s: str)", "description": "清洗字符串"},
    ])
    assert "f1(x: int)" in text
    assert "做 x 加倍" in text
    assert "f2(s: str)" in text
