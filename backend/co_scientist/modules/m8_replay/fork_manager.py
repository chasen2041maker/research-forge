"""
============================================================
 模块 8:过程回放与分叉(m8_replay/fork_manager.py)
============================================================

🎓 教学目标
    LangGraph Checkpointer 让整个 State 图可回滚 + 可分支。
    这一模块实现"研究分叉树"管理:
      - replay:回到任意节点,重新跑
      - fork:从某节点拉出新分支,探索不同假设
      - compare:横向对比多条分支的成果
      - merge:优秀子路径合回主线

💡 两套存储职责分离
    本模块有一个关键设计决策:分叉系统用了"两套 SQLite"。
      1. LangGraph Checkpointer(data/checkpoints/graph.sqlite):
         存每个节点跑完后的完整 State 快照,让图能真正"时间旅行"。
         表结构由 LangGraph 决定,我们不碰。
      2. 本文件的 forks.db:
         只存分叉的元数据(parent_fork_id / branch_node / status / rating),
         用于"我有哪些分支、哪些跑完了、哪个评分最高"这种管理视图。

    为什么不合到一张表?
      - Checkpointer 的表结构属于 LangGraph,随版本可能变,我们的代码不该
        依赖它内部 schema
      - 元数据列(描述、评分、状态)是业务语义,不该污染框架的库
      - 前端画"研究树"只需要元数据,不需要把快照 blob 都查出来

💡 fork_id 和 thread_id 的关系
    LangGraph 用 thread_id 作为 Checkpointer 的主键,本项目里
    fork_id = thread_id,两边以同一个 ID 串起来。create_fork 生成
    fork_id 之后,图跑的时候把它作为 thread_id 传进去,快照就天然落到
    这个分叉下。

📌 存储策略
    MVP 用 SQLite Checkpointer(langgraph.checkpoint.sqlite),零部署
    生产可切 PostgresCheckpointer

🔧 依赖
    from langgraph.checkpoint.sqlite import SqliteSaver

------------------------------------------------------------
"""

from __future__ import annotations

import sqlite3
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from co_scientist.config import settings
from co_scientist.utils import logger


@dataclass
class ForkMeta:
    """
    分叉元信息。每个字段都是刻意选的:

      fork_id        : 本分叉的唯一 ID,同时作为 LangGraph thread_id
      parent_fork_id : 父分叉 ID。空串表示根(第一次研究)
      branch_node    : 从哪个节点分的叉(例如 "m4_critique")
                       用于回答"我是在评审后分的?还是代码生成后分的?"
      description    : 人读的说明,例如"用 GPT-4 当 Reviewer 试试"
      created_at     : Unix 时间戳,用于 list_forks 排序
      final_rating   : meta_decision.final_rating 的拷贝,让 list 视图
                       不必每次去翻 checkpoint 拿分数
      status         : running / done / abandoned / mainline
                       - running:流程中
                       - done:完整跑完
                       - abandoned:用户主动放弃这条分支
                       - mainline:被 merge 选为主线(整理版 §9.5 第一阶段)
      topic_id       : 整理版 Phase D:若该 fork 由 TopicCard 创建,记录其 topic_id,
                       便于 compare 时反查方向描述
    """

    fork_id: str
    parent_fork_id: str
    branch_node: str  # 从哪个节点分出
    description: str
    created_at: float
    final_rating: float = 0.0
    status: str = "running"  # running / done / abandoned / mainline
    topic_id: str = ""


class ForkManager:
    """
    轻量分叉管理器,用独立 SQLite 存分支元数据。
    真正的 state 快照由 LangGraph Checkpointer 负责。

    ▍典型用法(给读者一张"地图")
        fm = ForkManager()
        # 1. 创建一个分叉(还没跑)
        meta = fm.create_fork(parent_fork_id="", branch_node="root", description="baseline")
        # 2. 把 meta.fork_id 作为 thread_id 塞进 LangGraph
        graph.invoke(state, config={"configurable": {"thread_id": meta.fork_id}})
        # 3. 跑完把结果回写到元数据表
        fm.update_status(meta.fork_id, "done", final_rating=8.5)
        # 4. 前端想看整棵树
        tree = fm.build_tree()
    """

    def __init__(self, db_path: Path | None = None) -> None:
        self.db_path = Path(db_path or settings.DATA_DIR / "forks.db")
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _init_db(self) -> None:
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """CREATE TABLE IF NOT EXISTS forks (
                    fork_id TEXT PRIMARY KEY,
                    parent_fork_id TEXT,
                    branch_node TEXT,
                    description TEXT,
                    created_at REAL,
                    final_rating REAL,
                    status TEXT
                )"""
            )
            # ◍ 整理版 Phase D 升级:topic_id 记录 fork 是哪张 TopicCard 派生的
            # ◍ 用 ALTER 增量加列而不是丢库重建,有三个原因:
            #   1) 用户老 forks.db 里有跑过的历史分支,丢库等于丢历史
            #   2) ALTER ADD COLUMN 在 SQLite 里是 O(1) 操作(不重写数据,只改 schema)
            #   3) DEFAULT '' 让老行的 topic_id 字段为空字符串,与新行行为一致
            # ◍ 这是"渐进式 schema 演进"的标准做法,生产数据库迁移也遵循同样思路
            cols = {row[1] for row in conn.execute("PRAGMA table_info(forks)").fetchall()}
            if "topic_id" not in cols:
                conn.execute("ALTER TABLE forks ADD COLUMN topic_id TEXT DEFAULT ''")

    def create_fork(
        self,
        parent_fork_id: str,
        branch_node: str,
        description: str = "",
        topic_id: str = "",
    ) -> ForkMeta:
        import time

        meta = ForkMeta(
            fork_id=uuid.uuid4().hex[:12],
            parent_fork_id=parent_fork_id or "",
            branch_node=branch_node,
            description=description,
            created_at=time.time(),
            topic_id=topic_id,
        )
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "INSERT INTO forks "
                "(fork_id, parent_fork_id, branch_node, description, created_at, "
                "final_rating, status, topic_id) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    meta.fork_id,
                    meta.parent_fork_id,
                    meta.branch_node,
                    meta.description,
                    meta.created_at,
                    meta.final_rating,
                    meta.status,
                    meta.topic_id,
                ),
            )
        logger.info(
            "[M8] 新分叉: {} (from {} @ {}) {}",
            meta.fork_id,
            parent_fork_id or "root",
            branch_node,
            f"topic={topic_id}" if topic_id else "",
        )
        return meta

    def update_status(self, fork_id: str, status: str, final_rating: float = 0.0) -> None:
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "UPDATE forks SET status=?, final_rating=? WHERE fork_id=?",
                (status, final_rating, fork_id),
            )

    # ============================================================
    # 整理版 Phase D 新增 API:批量分叉 / merge / 查询
    # ============================================================

    def get_meta(self, fork_id: str) -> ForkMeta | None:
        """按 fork_id 查询单条元信息。"""
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT fork_id, parent_fork_id, branch_node, description, created_at, "
                "final_rating, status, COALESCE(topic_id, '') "
                "FROM forks WHERE fork_id=?",
                (fork_id,),
            ).fetchone()
        if not row:
            return None
        return ForkMeta(
            fork_id=row[0], parent_fork_id=row[1] or "", branch_node=row[2] or "",
            description=row[3] or "", created_at=float(row[4] or 0.0),
            final_rating=float(row[5] or 0.0), status=row[6] or "",
            topic_id=row[7] or "",
        )

    def branch_from_topic_cards(
        self,
        topic_cards: list[dict],
        *,
        parent_fork_id: str = "",
        branch_node: str = "m0_discover",
    ) -> list[ForkMeta]:
        """
        整理版 §9.2:有 M0 时,Top-K TopicCard → 批量 create_fork。

        每张卡片独立一条 fork,description 取卡片 title 便于 list/compare 时识别。
        返回顺序与 topic_cards 一致(便于上层 zip 跑 graph)。

        ▍为什么不在这里就启动跑图
            ForkManager 的职责是"管理元数据",不应该耦合 LangGraph runtime。
            实际跑图由 multi_branch.py 的 runner 负责;这里只负责在 forks 表里
            登记分支,把 fork_id 还回去给 runner 当 thread_id 用。
        """
        metas: list[ForkMeta] = []
        for card in topic_cards:
            title = (card.get("title") or "").strip()
            tid = (card.get("topic_id") or "").strip()
            metas.append(self.create_fork(
                parent_fork_id=parent_fork_id,
                branch_node=branch_node,
                description=f"M0 候选: {title or tid or '(未命名)'}",
                topic_id=tid,
            ))
        return metas

    def branch_from_gate_decision(
        self,
        parent_fork_id: str,
        gate_decision: str,
        description: str = "",
    ) -> ForkMeta | None:
        """
        整理版 §9.4:M5.5 ResearchGate 决定回退/换题时,创建一条新 fork。

        gate_decision 与 branch_node 的映射:
          fetch_more_evidence → 回 m2(补检索)
          revise_experiment   → 回 m5(重设实验)
          refine_question     → 回 m1(重写 PICO)
          choose_new_topic    → 回 m0(换候选)
          continue_to_m6 / stop → 不创建新 fork(返回 None)

        ▍为什么 stop / continue 返回 None
            stop:研究终止,没必要再开 fork。
            continue:主分支继续走 m6,本分叉就是它,不需另开。
        """
        mapping = {
            "fetch_more_evidence": "m2_retrieve",
            "revise_experiment": "m5_experiment",
            "refine_question": "m1_refine",
            "choose_new_topic": "m0_discover",
        }
        target = mapping.get(gate_decision)
        if target is None:
            return None
        return self.create_fork(
            parent_fork_id=parent_fork_id,
            branch_node=target,
            description=description or f"由 M5.5 决策派生: {gate_decision}",
        )

    def get_winner(self, fork_ids: list[str]) -> ForkMeta | None:
        """
        从给定 fork_ids 中取 final_rating 最高且状态非 abandoned 的那条。
        平分时按 created_at 取最新(后跑的通常加进了改进)。
        ▍为什么不直接返回 fork_id 字符串
            上层经常需要拿 description / topic_id 展示给用户,
            一次性返回 ForkMeta 比让调用方再来一次 SELECT 省事。
        """
        if not fork_ids:
            return None
        placeholders = ",".join("?" * len(fork_ids))
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute(
                f"SELECT fork_id, parent_fork_id, branch_node, description, created_at, "
                f"final_rating, status, COALESCE(topic_id, '') "
                f"FROM forks "
                f"WHERE fork_id IN ({placeholders}) AND status != 'abandoned' "
                f"ORDER BY final_rating DESC, created_at DESC LIMIT 1",
                fork_ids,
            ).fetchone()
        if not row:
            return None
        return ForkMeta(
            fork_id=row[0], parent_fork_id=row[1] or "", branch_node=row[2] or "",
            description=row[3] or "", created_at=float(row[4] or 0.0),
            final_rating=float(row[5] or 0.0), status=row[6] or "",
            topic_id=row[7] or "",
        )

    def mark_mainline(self, fork_id: str) -> None:
        """
        整理版 §9.5 第一阶段:把 winner fork 标记为 mainline。

        实现:把这条 fork 的 status 改 mainline;同一父分叉下其他 mainline 状态被降回 done。
        ▍为什么这么做
            "mainline"是研究树的当前主线视图。前端需要一个明确的"当前主线"指针;
            如果两条都标 mainline 会让分支树渲染混乱。所以同父之下唯一。
        """
        meta = self.get_meta(fork_id)
        if not meta:
            logger.warning("[M8] mark_mainline:未找到 {}", fork_id)
            return
        with sqlite3.connect(self.db_path) as conn:
            # 先把同父下其他 mainline 降回 done
            conn.execute(
                "UPDATE forks SET status='done' "
                "WHERE parent_fork_id=? AND status='mainline' AND fork_id!=?",
                (meta.parent_fork_id, fork_id),
            )
            conn.execute(
                "UPDATE forks SET status='mainline' WHERE fork_id=?",
                (fork_id,),
            )
        logger.info("[M8] mainline 已设置: {} (desc={})", fork_id, meta.description)

    def list_forks(self) -> list[dict[str, Any]]:
        with sqlite3.connect(self.db_path) as conn:
            rows = conn.execute(
                "SELECT * FROM forks ORDER BY created_at DESC"
            ).fetchall()
            cols = [d[0] for d in conn.execute("SELECT * FROM forks LIMIT 0").description]
        # 老库可能没有 topic_id 列,统一补空串方便前端渲染
        out: list[dict[str, Any]] = []
        for r in rows:
            d = dict(zip(cols, r))
            d.setdefault("topic_id", "")
            out.append(d)
        return out

    def build_tree(self) -> dict[str, list[str]]:
        """
        构建父→子映射,方便前端画树。

        返回形如:{"root": ["abc", "def"], "abc": ["xyz"], ...}
        前端用这个结构直接递归渲染出树状图(D3 / Cytoscape.js 都好接)。

        ▍为什么返回 dict 而不是嵌套节点对象
            嵌套对象(parent 里放 children 数组)在 SQL 展平数据上构造麻烦,
            还得一次性把所有分叉读完。映射表可以 lazy 展开,前端拿到根的
            children 再按需查。另外映射天然去重,不会因为数据竞态产生同一
            分叉出现两次。
        """
        tree: dict[str, list[str]] = {}
        for f in self.list_forks():
            parent = f["parent_fork_id"] or "root"
            tree.setdefault(parent, []).append(f["fork_id"])
        return tree

    def compare(self, fork_ids: list[str]) -> list[dict[str, Any]]:
        if not fork_ids:
            return []
        placeholders = ",".join("?" * len(fork_ids))
        with sqlite3.connect(self.db_path) as conn:
            rows = conn.execute(
                f"SELECT * FROM forks WHERE fork_id IN ({placeholders})",
                fork_ids,
            ).fetchall()
            cols = [d[0] for d in conn.execute("SELECT * FROM forks LIMIT 0").description]
        return [dict(zip(cols, r)) for r in rows]
