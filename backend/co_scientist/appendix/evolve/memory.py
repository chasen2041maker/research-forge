"""
============================================================
 附录 A:经验记忆库(appendix/evolve/memory.py)
============================================================

🎓 教学目标
    Reflexion 思想:任务结束 → LLM 反思 → 提炼经验 → 存档 → 下次自动召回。
    让 Agent 越用越聪明。

    对应论文:Reflexion: Language Agents with Verbal Reinforcement Learning
    (NeurIPS 2023)

📌 简化实现(教学版)
    - 用 SQLite + 词袋重叠做轻量记忆库
    - 生产应该用向量库(Qdrant) + embedding 做语义召回
    - 5 类记忆:domain / strategy / failure / user / tool

💡 为什么教学版不一上来就接 Qdrant/embedding
    1. 本模块的重点是讲清"记忆何时写入、何时读取、如何影响下游",
       不是讲向量数据库的用法。
    2. 词袋召回虽然弱,但接口形状(recall → top_k dict 列表)和向量召回一致,
       未来只需要替换 score 计算即可升级,不会影响调用方。
    3. 零外部依赖 → 读者 git clone 下来直接就能跑通闭环。

📌 与主流程的挂接点
    - 写入:graph.py 的 appendix_reflect 节点(m7 之后)
    - 读取:graph.py 的 appendix_recall 节点(m1 之前),结果放进
             state.recalled_memories,被 m5_experiment 消费

📌 生产级扩展(已实现)
    1. **used_count 使用频次统计**:每次 recall 命中都会 UPDATE used_count + 1,
       为淘汰策略提供数据支撑 —— 从未被命中的记忆就是"写了也没用"的噪音。
    2. **分层召回(mem_type 过滤)**:recall(query, mem_type="failure") 只取该类,
       避免不相关类型稀释信号。典型用法:
         - m4 批判场景 → 召 failure(历史踩坑)
         - m5 实验设计 → 召 strategy(有效套路)
         - m1 问题精炼 → 召 domain(领域稳定知识)
    3. **遗忘机制(forget_stale)**:过期 + 低命中率的记忆定期淘汰,
       防止记忆库越积越多 → 召回质量被陈旧经验污染。
       建议通过 CLI / 定时任务每周跑一次。

💡 为什么遗忘策略用"时间 + 命中数"而不是单一维度
    - 只看时间:经典记忆(比如"用 RCT 优于 observational study")被强删,但它长期有效
    - 只看命中数:刚写进去的新记忆还没来得及被命中就被删
    - "老且没用"(创建超过 max_age_days 且 used_count < min_uses)才是真噪音

------------------------------------------------------------
"""

from __future__ import annotations

import json
import math
import sqlite3
import time
import uuid
from pathlib import Path
from typing import Literal

from co_scientist.config import settings
from co_scientist.llm import get_llm
from co_scientist.utils import logger

# ------------------------------------------------------------
# 五类记忆的类型枚举。选择这 5 类而不是让 LLM 自由起类型名,是为了:
#   1. 召回时能按类型过滤(例如只召回 failure 类给 m4 做批判参考);
#   2. 把记忆库的可观测性做好 —— 有类型就能画"每类有几条"的面板。
# ------------------------------------------------------------
MemoryType = Literal["domain", "strategy", "failure", "user", "tool"]

# 给反思 Agent 的 system prompt。
# 关键设计:
#   - 要求严格 JSON,便于 chat_json 解析
#   - 明确"不超过 5 条",防止 LLM 灌水
#   - 每种类型都给一个 1 句话定义,减少 LLM 把 strategy 和 failure 混到一起
REFLECT_SYSTEM = """\
你是 Agent 反思助手。基于刚刚结束的任务,提炼可复用的经验。
返回 JSON: {"memories": [{"type": "domain|strategy|failure|user|tool", "content": "..."}]}
- type=strategy:有效的方法套路
- type=failure:踩过的坑及避免方式
- type=domain:领域知识(应该是稳定事实,不是任务细节)
- type=user:对用户偏好的发现
- type=tool:工具使用技巧

不超过 5 条,只保留对未来真正有用的。"""


class EvolvingMemory:
    """
    教学版长期记忆库。

    核心方法:
      - add:直接写入一条(给外部工具/CLI 调)
      - reflect_and_save:给 LLM 任务摘要,让它提炼多条然后入库
      - recall:按 query 召回 top-k,可按 mem_type 分层过滤,命中自动 used_count+1
      - forget_stale:按"创建时间 + 命中次数"淘汰过期无用记忆

    有意不做的能力(留给生产版):
      - 记忆去重 / 合并(embedding 相似度 > 0.95 合并为同一条)
      - LLM 反思质量门禁(长度 / 具体性 / 可操作性评分)
    """

    def __init__(self, db_path: Path | None = None) -> None:
        # 默认把 sqlite 文件放在 settings.DATA_DIR/memory.db,和其他运行时数据同目录。
        # 允许传自定义路径主要是为了写单元测试时隔离数据库。
        self.db_path = Path(db_path or settings.DATA_DIR / "memory.db")
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _init_db(self) -> None:
        # used_count 先留着但暂时没更新逻辑,是为了将来加"衰减/淘汰"时不用改 schema。
        # embedding 列存 JSON 序列化的 float 数组;记忆写入时若 embedding 服务
        # 不可用就留空,recall 时会自动降级到词袋。
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """CREATE TABLE IF NOT EXISTS memories (
                    id TEXT PRIMARY KEY,
                    type TEXT,
                    content TEXT,
                    created_at REAL,
                    used_count INTEGER DEFAULT 0,
                    embedding TEXT DEFAULT ''
                )"""
            )
            # 老库兼容:如果之前没有 embedding 列,ALTER 加上
            cols = [r[1] for r in conn.execute("PRAGMA table_info(memories)").fetchall()]
            if "embedding" not in cols:
                conn.execute("ALTER TABLE memories ADD COLUMN embedding TEXT DEFAULT ''")

    # ---------------- embedding 工具函数 ----------------
    #
    # 教学版的语义召回走 GPT 中转站的 OpenAI-compatible embedding 接口。
    # 任何一步失败(没部署 embedding、key 失效、网络超时)都让函数返回 []。
    # recall() 看到空向量就自动回落到原来的词袋实现,这样"能用就用,不能用也不崩"。
    def _embed(self, text: str) -> list[float]:
        try:
            llm = get_llm("chat")
            embed = getattr(llm, "embed", None)
            if not callable(embed):
                return []
            vectors = embed(
                [text[:8000]],  # 避免过长 token 被截断报错
                model=settings.RELAY_MODEL_EMBEDDING,
                purpose="evolve_memory_embed",
            )
            return list(vectors[0]) if vectors else []
        except Exception as e:
            logger.debug("[evolve] embedding 不可用,走词袋降级: {}", e)
            return []

    @staticmethod
    def _cosine(a: list[float], b: list[float]) -> float:
        if not a or not b or len(a) != len(b):
            return 0.0
        dot = sum(x * y for x, y in zip(a, b))
        na = math.sqrt(sum(x * x for x in a))
        nb = math.sqrt(sum(y * y for y in b))
        if na == 0.0 or nb == 0.0:
            return 0.0
        return dot / (na * nb)

    def add(self, mem_type: MemoryType, content: str) -> str:
        """直接写一条记忆,返回记忆 ID。供外部工具/CLI 调。"""
        mid = uuid.uuid4().hex[:12]
        vec = self._embed(content)
        emb_str = json.dumps(vec) if vec else ""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "INSERT INTO memories VALUES (?, ?, ?, ?, ?, ?)",
                (mid, mem_type, content, time.time(), 0, emb_str),
            )
        return mid

    def reflect_and_save(self, task_summary: str) -> int:
        """
        让 LLM 在任务结束后做一次"复盘",把可迁移经验写入记忆库。

        ▍设计要点
            - temperature=0.5:反思需要一定发散(不是纯总结),但又不能太跳;
            - 过滤非法 type:防止 LLM 偶尔冒出 "other" 之类的非预期值污染枚举;
            - 整个过程不抛异常:反思失败(LLM 超时/JSON 解析失败)不应该把
              调用它的主流程 graph 搞崩,所以 except 后只记警告,返回 0。

        返回:成功入库的记忆条数(用于观测性)。
        """
        llm = get_llm("chat")
        try:
            result = llm.chat_json(
                messages=[
                    {"role": "system", "content": REFLECT_SYSTEM},
                    {"role": "user", "content": task_summary},
                ],
                purpose="evolve_reflect",
                temperature=0.5,
            )
        except Exception as e:
            logger.warning("[evolve] 反思失败: {}", e)
            return 0

        count = 0
        for m in result.get("memories", []) or []:
            t = m.get("type", "")
            c = m.get("content", "")
            if t in ("domain", "strategy", "failure", "user", "tool") and c:
                self.add(t, c)
                count += 1
        logger.info("[evolve] 沉淀 {} 条新记忆", count)
        return count

    def recall(
        self,
        query: str,
        top_k: int = 5,
        mem_type: MemoryType | None = None,
    ) -> list[dict]:
        """
        语义召回(优先)+ 词袋召回(降级),可按类型分层过滤,命中自动计数。

        Args:
            query: 查询语句
            top_k: 返回前 k 条
            mem_type: 可选,限定记忆类型(domain/strategy/failure/user/tool)。
                     None 表示不过滤。典型用法:
                       - m4 批判场景  → mem_type="failure" (看历史踩坑)
                       - m5 实验设计  → mem_type="strategy"(看有效套路)
                       - m1 问题精炼  → mem_type="domain"  (看领域知识)

        ▍策略
            1. 先尝试 embed(query):若返回非空 → 对库里带 embedding 的记忆做
               余弦相似度,取分数 > 0.3 的 top_k(阈值是经验值,过低会灌水)。
            2. 若 embed 失败、或库里没有任何带 embedding 的记忆 → 退回词袋匹配,
               保证零外部依赖也能跑通闭环。

        ▍为什么要在 SQL 层做 mem_type 过滤而不是 Python 侧
            - 库里有 10000 条 domain、50 条 failure,想召 failure 时在 Python 侧
              过滤要先把 10050 条全取出来再丢 10000 条,浪费 IO 和反序列化开销。
            - SQL WHERE type=? 走的是索引(type 是常量枚举,选择率低),一步到位。

        ▍为什么在 recall 里 UPDATE used_count 而不是放在调用方
            - 调用方只关心"我拿到了哪些记忆",不关心计数这件事。
            - 放在 recall 内做"副作用":每次命中都 +1,和业务解耦。
            - 代价是 recall 从纯读变成读+写;SQLite 在并发极低的教学场景下可接受。
              真生产若担心写锁,可改成异步队列批量回写。

        ▍为什么不一开始就强制 embedding
            - 本仓库很多读者跑测试/单元验证时中转站 key 可能没配,
              或者在 CI 里 mock 掉网络。走"能用就用"的策略让使用门槛更低。
            - 生产部署若想强制 embedding,改这里 raise 即可。

        进阶参考:09-进化与对抗/README.md 9.3 节给了 Qdrant + embedding 的标准版。
        """
        # ---- 在 SQL 层做 type 过滤:少拉数据 ----
        sql = "SELECT id, type, content, embedding FROM memories"
        params: tuple = ()
        if mem_type is not None:
            sql += " WHERE type = ?"
            params = (mem_type,)

        with sqlite3.connect(self.db_path) as conn:
            rows = conn.execute(sql, params).fetchall()
        if not rows:
            return []

        results: list[dict] = []

        # --- 尝试语义召回 ---
        qvec = self._embed(query)
        if qvec:
            sem_scored: list[tuple[float, dict]] = []
            for mid, t, c, emb_str in rows:
                if not emb_str:
                    continue
                try:
                    vec = json.loads(emb_str)
                except Exception:
                    continue
                score = self._cosine(qvec, vec)
                if score > 0.3:
                    sem_scored.append(
                        (score, {"id": mid, "type": t, "content": c, "score": score})
                    )
            if sem_scored:
                sem_scored.sort(reverse=True, key=lambda x: x[0])
                results = [m for _, m in sem_scored[:top_k]]

        # --- 词袋降级 ---
        if not results:
            terms = set(query.lower().split())
            bow_scored: list[tuple[int, dict]] = []
            for mid, t, c, _ in rows:
                tokens = set(c.lower().split())
                score = len(terms & tokens)
                if score > 0:
                    bow_scored.append((score, {"id": mid, "type": t, "content": c}))
            bow_scored.sort(reverse=True, key=lambda x: x[0])
            results = [m for _, m in bow_scored[:top_k]]

        # ---- 命中回写 used_count:为 forget_stale 提供淘汰依据 ----
        # 为什么用单条 UPDATE ... IN (?, ?, ...) 而不是 N 次 executemany:
        #   命中条数通常 ≤ top_k=5,单 SQL 一次搞定,减少事务开销。
        if results:
            ids = [m["id"] for m in results]
            placeholders = ",".join(["?"] * len(ids))
            with sqlite3.connect(self.db_path) as conn:
                conn.execute(
                    f"UPDATE memories SET used_count = used_count + 1 "
                    f"WHERE id IN ({placeholders})",
                    ids,
                )

        return results

    def forget_stale(
        self,
        max_age_days: float = 90.0,
        min_uses: int = 1,
    ) -> int:
        """
        遗忘机制:淘汰"又老又没用"的记忆。

        Args:
            max_age_days: 超过这个天数的老记忆才进入淘汰候选(默认 90 天)
            min_uses: 从未被 recall 命中超过这个次数的算"没用"(默认 1 次,即一次都没命中)

        Returns:
            被删除的记忆条数

        ▍为什么默认 90 天 + 1 次
            - 90 天:一个季度的业务周期,足够"真正有用的经验"被反复召回
            - 1 次:一次都没命中 = 写进去之后主流程从没觉得它相关 = 典型噪音

        ▍为什么要"AND"不是"OR"
            - OR 太激进:一条 100 天前写的但最近刚被命中的经典经验会被误杀
            - AND 只杀"既老又没用"的双重噪音,误杀率低

        ▍调用建议
            - CLI 手动触发:python -m co_scientist.cli memory-forget
            - 定时任务:每周日凌晨 crontab 跑一次
            - 不建议放进主 DAG:每次 run 都淘汰会让刚写进去的新记忆来不及被命中

        ▍面试讲点
            这是区分"玩具 Agent"和"生产 Agent"的关键 —— 有写入没淘汰 = 记忆库越用越糊。
            对应论文:MemGPT(2023)的"记忆分页驱逐"思想的极简版。
        """
        threshold = time.time() - max_age_days * 86400
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(
                "DELETE FROM memories WHERE created_at < ? AND used_count < ?",
                (threshold, min_uses),
            )
            deleted = cursor.rowcount
        if deleted > 0:
            logger.info(
                "[evolve] 淘汰 {} 条陈旧记忆 (age > {} 天 且 used_count < {})",
                deleted,
                max_age_days,
                min_uses,
            )
        return deleted
