"""
单元测试:EvolvingMemory 纯逻辑(不调真实 LLM/embedding)。

运行:pytest backend/tests/test_memory.py
"""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture
def mem(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """用 tmp_path 隔离数据库,并禁用 embedding(让 _embed 返回 [])。"""
    from co_scientist.appendix.evolve.memory import EvolvingMemory

    # 关键:monkeypatch 掉 _embed,让它永远返回 [],
    # 这样测试不依赖 DeepSeek API,跑的是词袋降级分支。
    monkeypatch.setattr(EvolvingMemory, "_embed", lambda self, text: [])

    return EvolvingMemory(db_path=tmp_path / "memory.db")


def test_add_and_count(mem) -> None:
    mid = mem.add("strategy", "用 RAG 减少幻觉")
    assert isinstance(mid, str) and len(mid) == 12


def test_recall_bow_hits(mem) -> None:
    mem.add("strategy", "retrieval augmented generation reduces hallucination")
    mem.add("failure", "unrelated content about quantum physics")
    hits = mem.recall("retrieval hallucination")
    # 词袋降级:第一条命中 2 个词,第二条命中 0
    assert len(hits) == 1
    assert hits[0]["type"] == "strategy"


def test_recall_empty_db(mem) -> None:
    assert mem.recall("anything") == []


def test_recall_no_match(mem) -> None:
    mem.add("domain", "AAA BBB CCC")
    assert mem.recall("XXX YYY ZZZ") == []


def test_reflect_invalid_type_filtered(mem, monkeypatch) -> None:
    """LLM 偶尔返回非法 type 应该被过滤,不入库。"""
    from co_scientist.appendix.evolve import memory as mem_mod

    class FakeLLM:
        def chat_json(self, **kwargs):
            return {
                "memories": [
                    {"type": "strategy", "content": "ok one"},
                    {"type": "alien", "content": "illegal type"},
                    {"type": "failure", "content": ""},  # 空内容也该过滤
                ]
            }

    monkeypatch.setattr(mem_mod, "get_llm", lambda role: FakeLLM())
    count = mem.reflect_and_save("任务摘要")
    assert count == 1
    assert len(mem.recall("one")) == 1


def test_reflect_llm_failure_returns_0(mem, monkeypatch) -> None:
    from co_scientist.appendix.evolve import memory as mem_mod

    class ExplodingLLM:
        def chat_json(self, **kwargs):
            raise RuntimeError("llm boom")

    monkeypatch.setattr(mem_mod, "get_llm", lambda role: ExplodingLLM())
    assert mem.reflect_and_save("摘要") == 0


def test_cosine_edge_cases() -> None:
    from co_scientist.appendix.evolve.memory import EvolvingMemory

    assert EvolvingMemory._cosine([], [1.0]) == 0.0
    assert EvolvingMemory._cosine([1.0], [1.0, 2.0]) == 0.0
    assert EvolvingMemory._cosine([0.0, 0.0], [1.0, 1.0]) == 0.0
    # 完全同向
    assert abs(EvolvingMemory._cosine([1.0, 0.0], [2.0, 0.0]) - 1.0) < 1e-9
    # 正交
    assert abs(EvolvingMemory._cosine([1.0, 0.0], [0.0, 1.0])) < 1e-9


def test_recall_filters_by_mem_type(mem) -> None:
    """mem_type 过滤:只召回指定类型,其他类型即使词袋命中也不返回。"""
    mem.add("strategy", "alpha beta gamma")
    mem.add("failure", "alpha beta gamma")  # 同内容但不同类型
    mem.add("domain", "alpha beta gamma")

    # 不过滤:3 条全命中
    assert len(mem.recall("alpha")) == 3

    # 过滤 failure:只拿 1 条,且类型对
    failure_hits = mem.recall("alpha", mem_type="failure")
    assert len(failure_hits) == 1
    assert failure_hits[0]["type"] == "failure"

    # 过滤 user:没这类 → 空
    assert mem.recall("alpha", mem_type="user") == []


def test_recall_bumps_used_count(mem) -> None:
    """召回命中后,used_count 应该 +1。"""
    import sqlite3

    mid = mem.add("strategy", "unique_target_word_xyz")
    # 初始 used_count = 0
    with sqlite3.connect(mem.db_path) as conn:
        before = conn.execute(
            "SELECT used_count FROM memories WHERE id = ?", (mid,)
        ).fetchone()[0]
    assert before == 0

    # 召回 3 次 → 应该变成 3
    for _ in range(3):
        hits = mem.recall("unique_target_word_xyz")
        assert len(hits) == 1

    with sqlite3.connect(mem.db_path) as conn:
        after = conn.execute(
            "SELECT used_count FROM memories WHERE id = ?", (mid,)
        ).fetchone()[0]
    assert after == 3


def test_recall_no_hit_no_bump(mem) -> None:
    """召回没命中时,used_count 不应被错误累加。"""
    import sqlite3

    mid = mem.add("strategy", "apple banana cherry")
    mem.recall("zzz_no_match_qqq")  # 完全不相关的 query
    with sqlite3.connect(mem.db_path) as conn:
        used = conn.execute(
            "SELECT used_count FROM memories WHERE id = ?", (mid,)
        ).fetchone()[0]
    assert used == 0


def test_forget_stale_deletes_old_and_unused(mem) -> None:
    """又老又没被召回的记忆应被淘汰;新的 或 被用过的 保留。"""
    import sqlite3
    import time as time_module

    # 造 3 条数据,手动改 created_at 和 used_count 模拟不同状态
    m_old_unused = mem.add("strategy", "老且没用")
    m_old_used = mem.add("strategy", "老但被用过")
    m_new_unused = mem.add("strategy", "刚写的没用过")

    very_old = time_module.time() - 100 * 86400  # 100 天前
    with sqlite3.connect(mem.db_path) as conn:
        conn.execute(
            "UPDATE memories SET created_at = ? WHERE id = ?", (very_old, m_old_unused)
        )
        conn.execute(
            "UPDATE memories SET created_at = ?, used_count = 5 WHERE id = ?",
            (very_old, m_old_used),
        )
        # m_new_unused 保留默认(刚创建、used_count=0)

    deleted = mem.forget_stale(max_age_days=90.0, min_uses=1)

    # 只有"老且 used_count < 1"的被删
    assert deleted == 1
    with sqlite3.connect(mem.db_path) as conn:
        remaining = {r[0] for r in conn.execute("SELECT id FROM memories").fetchall()}
    assert m_old_unused not in remaining
    assert m_old_used in remaining  # 虽然老但被用过
    assert m_new_unused in remaining  # 虽然没用过但还新


def test_forget_stale_empty_db(mem) -> None:
    """空库上调 forget_stale 不崩,返回 0。"""
    assert mem.forget_stale() == 0


def test_recall_prefers_semantic_when_available(tmp_path, monkeypatch) -> None:
    """有 embedding 时走余弦,并覆盖词袋结果。"""
    from co_scientist.appendix.evolve.memory import EvolvingMemory

    # 让 _embed 返回可预测向量:内容含 "target" → [1,0],否则 [0,1]
    def fake_embed(self, text: str) -> list[float]:
        return [1.0, 0.0] if "target" in text.lower() else [0.0, 1.0]

    monkeypatch.setattr(EvolvingMemory, "_embed", fake_embed)
    m = EvolvingMemory(db_path=tmp_path / "mem.db")
    m.add("domain", "target hit content")
    m.add("domain", "totally different")

    # query 也会 embed 成 [1,0],只有 "target" 那条余弦相似度 > 0.3
    hits = m.recall("something with target word")
    assert len(hits) == 1
    assert "target" in hits[0]["content"]
    assert hits[0]["score"] > 0.9
