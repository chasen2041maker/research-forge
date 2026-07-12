# 09 - 进化与对抗

> 让 Agent 越用越聪明 + 自动产出训练数据。
> 涵盖:Reflexion 记忆库、Prompt A/B 自动进化、Red/Blue Team、DPO 数据生成。

---

## 9.1 自我进化:Reflexion 思想

### 论文出处
**Reflexion: Language Agents with Verbal Reinforcement Learning**(NeurIPS 2023)

### 核心思想
人在做完任务后会"复盘",提炼"下次怎么做更好"的经验。
Agent 也可以:
1. 任务结束后,LLM 反思整个过程
2. 提炼可复用的"经验文本"
3. 存入记忆库
4. 下次类似任务时召回相关经验,加进 prompt

### 简化架构
```
任务 N 结束
   ↓
LLM 反思:"哪些做对了?哪些踩坑?"
   ↓
提取 1-5 条经验,带类型标签
   ↓
存入 SQLite/向量库
   ↓
任务 N+1 开始
   ↓
召回相关经验,加进 system prompt
   ↓
跑任务(更聪明了)
```

---

## 9.2 五类记忆

| 类型 | 内容 |
|------|------|
| **domain** | 领域知识(稳定事实) |
| **strategy** | 有效的方法套路 |
| **failure** | 踩过的坑及避免方式 |
| **user** | 用户偏好的发现 |
| **tool** | 工具使用技巧 |

### 反思 Prompt
```python
REFLECT_SYSTEM = """\
你是 Agent 反思助手。基于刚结束的任务,提炼可复用的经验。
返回 JSON: {"memories": [{"type": "...", "content": "..."}]}
不超过 5 条,只保留对未来真正有用的。"""

result = llm.chat_json([
    {"role": "system", "content": REFLECT_SYSTEM},
    {"role": "user", "content": task_summary},
])
```

---

## 9.3 记忆召回

### 简易版:词袋匹配
```python
def recall(query, top_k=5):
    terms = set(query.lower().split())
    rows = db.execute("SELECT id, type, content FROM memories").fetchall()
    scored = []
    for mid, t, c in rows:
        score = len(terms & set(c.lower().split()))
        if score > 0:
            scored.append((score, {"id": mid, "type": t, "content": c}))
    scored.sort(reverse=True)
    return [m for _, m in scored[:top_k]]
```

### 进阶:embedding 召回
```python
embed = model.encode(content)  # BGE-M3 / DeepSeek Embedding
qdrant.upsert(point=Point(id=mid, vector=embed, payload={...}))
hits = qdrant.search(query_vector=embed_query, limit=top_k)
```

### 📌 本项目实现(`appendix/evolve/memory.py`)
- **已走 embedding**:默认调 DeepSeek `settings.MODEL_EMBEDDING` 做余弦相似度,阈值 0.3。
- **自动降级**:embedding 调用失败或老库记忆没有 embedding 字段 → 回落到词袋匹配,保证零外部依赖也能跑通。
- **挂入主 DAG**:`appendix_recall` 节点在 `START` 之后、`m1_refine` 之前执行,
  结果写到 `state.recalled_memories`,被 m5_experiment 作为"历史经验提示"拼进 prompt。
- **分层召回(mem_type 过滤)**:`recall(query, mem_type=...)` 可在 SQL 层按类型过滤,
  避免不相关类型稀释信号。用法:
    - m4 批判场景 → `recall(q, mem_type="failure")` 看历史踩坑
    - m5 实验设计 → `recall(q, mem_type="strategy")` 看有效套路
    - m1 问题精炼 → `recall(q, mem_type="domain")` 看领域知识
  `appendix_recall` 节点不传此参数,做"全量召回"作为默认安全值。
- **使用频次统计**:每次 recall 命中会自动 `UPDATE used_count = used_count + 1`,
  为遗忘策略提供数据支撑 —— 从未被命中的记忆就是"写了也没用"的噪音。
- **遗忘机制(forget_stale)**:`forget_stale(max_age_days=90, min_uses=1)` 淘汰
  "老且没被用过"的双重噪音记忆。用 AND 而非 OR,避免误杀"老但经典"的经验。
  建议 CLI / 定时任务每周跑一次,不要塞进主 DAG。

---

## 9.4 Prompt 自动 A/B 进化

### 论文出处
- **DSPy**(Stanford):自动优化 Prompt 的框架
- **OPRO**(Google,2023):Large Language Models as Optimizers

### 流程
```
1. 注册多个 Prompt 变体到表里
2. 每次跑任务用某个变体,记录评分
3. 表现差时,LLM 看失败案例改 Prompt → 注册为新变体
4. 选当前最高均分的变体上线(A/B)
```

### 表结构
```sql
CREATE TABLE prompts (
    pid TEXT PRIMARY KEY,
    name TEXT,           -- 用途名,如 "m4_novelty"
    text TEXT,           -- prompt 内容
    total_score REAL,
    runs INTEGER
)
```

### Prompt 改进 Agent
```python
SYSTEM = "你是 Prompt 工程专家。基于失败案例改进 system prompt。"

new_prompt = llm.chat([
    {"role": "system", "content": SYSTEM},
    {"role": "user", "content": f"当前: {current}\n失败案例: {fails}"}
]).content
```

📌 **项目对应**:`backend/co_scientist/appendix/evolve/`

### 📌 本项目实现闭环
```
m5_experiment.designer
  └─ PromptABTester().best_for("m5_experiment")
       └─ 挑当前最优变体,没变体回退默认 SYSTEM_M5_EXPERIMENT
       └─ 把使用的 variant 信息写进 state.metadata.m5_prompt_variant

graph.appendix_reflect
  └─ 读 metadata.m5_prompt_variant.pid
  └─ 按 meta_decision.final_rating 做 record_score(pid, rating)
       这就是 A/B 的自动评分回写,整条 run 就是一次 trial
```

CLI 工具:
```bash
python -m co_scientist.cli prompt-ab-register --name m5_experiment --file p.txt   # 新候选
python -m co_scientist.cli prompt-ab-best     --name m5_experiment                # 当前胜者
python -m co_scientist.cli prompt-ab-score    --pid <pid> --score 8.5             # 手动打分
python -m co_scientist.cli prompt-ab-evaluate --name m5_experiment --questions q.txt  # 自动评测
python -m co_scientist.cli prompt-ab-evolve   --name ... --current p.txt --failures f.txt  # LLM 改
python -m co_scientist.cli evolve-dashboard                                       # 记忆+AB 面板
```

---

## 9.5 对抗式数据工厂(GAN 思想)

### 思路
```
Blue Team 提方案 → Red Team 找漏洞 → Blue 修 → Red 再攻 → ...
```
每一轮对话都是一条**训练数据**。

### 7 个可加对抗的位置
1. 文献相关性打分
2. 研究问题生成
3. **实验方案设计(Red/Blue Team)** ⭐
4. **论文写作(Author vs Reviewer)** ⭐
5. 代码生成(写 vs 找 bug)
6. 幻觉检测(Fact Hunter)
7. 红蓝对抗整体评估

---

## 9.6 Red/Blue Team 实现

### 角色设定
```python
BLUE_SYS = "你是 Blue Team。提出研究方案或修复 Red 的批评。给出具体可执行方案。"

RED_SYS = "你是 Red Team。找方案的薄弱点。列出 3 个最致命漏洞,说明攻击路径。"

JUDGE_SYS = "你是 Judge。打分:{blue_score, red_score, chosen, reason}"
```

### 一轮对抗
```python
def run_round(proposal):
    red_attack = red_chat.chat([SYS=RED, USER=proposal], temperature=0.8)
    blue_fixed = blue_chat.chat([SYS=BLUE, USER=proposal+attack], temperature=0.6)
    judgment = judge.chat_json([SYS=JUDGE, USER=all_three])
    return AdversarialRound(...)
```

### 模型搭配
```
Blue:  deepseek-chat        (创造性)
Red:   deepseek-reasoner    (推理强,找漏洞狠) + 高温度
Judge: claude-opus-4-7      (关键裁决,质量决定性)
```

不同 system + 不同温度 → 避免"互相吹捧"。

### 📌 本项目实现(`appendix/adversarial/red_blue.py`)
- `run_round(proposal)`: 单轮 Pairwise。
- `run_multi_round(proposal, max_rounds=3)`: **多轮版**,Blue 修 → Red 再攻,
  停止原因三选一:
  - `no_more_issue`: Red 命中"未发现/无漏洞"等关键词
  - `max_rounds`: 达到上限
  - `red_failed`: Red 调用整体异常
- `run_factory(..., multi_round=True)`: 批量多轮,每轮都产一条 DPO 对。
- Judge 失败自动降级到 reasoner,保证批量产数据时单条失败不拖垮整批。
- 不挂主 DAG(对抗不是研究流程一部分),通过 CLI 显式触发:
  ```bash
  python -m co_scientist.cli adversarial-run   --proposal "..."
  python -m co_scientist.cli adversarial-multi --proposal "..." --max-rounds 3
  python -m co_scientist.cli adversarial-build --input seeds.txt --multi-round
  ```

---

## 9.7 产出 DPO 训练数据

### DPO 数据格式
```json
{
  "prompt": "请基于 Red 的攻击修复方案: ...",
  "chosen": "<更好的版本>",
  "rejected": "<更差的版本>",
  "score": 8.2
}
```

### 用途
- 微调小模型(如 Qwen2.5-7B)做 RLHF/DPO
- 开源到 HuggingFace Hub 作为数据集资产

---

## 9.8 七种对抗模式

1. **Pairwise Debate**:Blue vs Red 单轮(本项目实现)
2. **Tournament**:多个方案两两对决,Elo 排名
3. **Self-Play**:同一 Agent 双身份(节省成本)
4. **Evolutionary**:遗传算法式,优秀方案"杂交"产出新方案
5. **Adversarial Perturbation**:对原方案做微扰动看模型是否仍正确
6. **Minimax**:Blue 最大化得分,Red 最小化,数学博弈
7. **Multi-Agent Debate**:3+ Agent 多方辩论

---

## 9.9 避坑指南

| 坑 | 对策 |
|----|------|
| **互相吹捧** | 不同 system + 不同温度;关键裁决用不同模型(Claude vs DeepSeek) |
| **Mode Collapse** | 高温度 + 多样性约束 |
| **无限循环** | 最大轮次 + 评分停滞停止 |
| **评分不一致** | 多 Judge 投票 + 固定 rubric |
| **数据质量差** | Judge 阈值过滤 + 人工抽检 5% |

---

## 9.10 数据飞轮

```
对抗产出 10k DPO 数据
    ↓
开源至 HuggingFace Hub
    ↓
被下载 / 被引用
    ↓
简历硬核资产
```

> 本项目只产数据,不做微调(严格只用 DeepSeek + Claude)。
> 微调是另一个独立项目。

---

## 📝 面试常见问题

1. **Reflexion 是什么?**
   - 任务后让 LLM 反思,提炼经验存档,下次召回。NeurIPS 2023 论文

2. **如何让 Agent 越用越聪明?**
   - L1 经验记忆(Reflexion)、L2 Prompt A/B、L3 工具自生成、L4 架构自重构、L5 微调

3. **DPO 数据怎么造?**
   - Red/Blue Team 对抗,Judge 标注哪个更好,组成 (chosen, rejected) 对

4. **多 Agent 互相吹捧怎么办?**
   - 不同模型家族 / 不同 system / 不同温度

5. **对抗模式有哪些?**
   - Pairwise、Tournament、Self-Play、Evolutionary、Minimax、Multi-Agent Debate

---

## 🎯 练手题

1. ~~把 `EvolvingMemory.recall` 从词袋换成 DeepSeek embedding 召回~~ ✅ 本项目已实现,练手改为:把 DeepSeek embedding 换成本地 BGE-M3,或接 Qdrant
2. 实现 Tournament 模式:6 个方案两两对决,Elo 排名
3. 跑 10 个种子问题,产 100 条 DPO 对,清洗后开源到 HF Hub
4. 给 Prompt A/B 加"灰度发布":新变体先 10% 流量,胜出再全量
5. 把 `_red_found_issue` 的关键词判定升级成结构化字段(让 Red 返回 `{found: bool, ...}`)
6. ~~把 L3 技能自生成补上:m6_code 产出的可复用函数自动注册到技能库(Voyager 式)~~ ✅ 本项目已实现,练手改为:把 `_register_verified_skills` 的"函数体 ≥ 3 行"过滤改成用 LLM 判断"这个函数是否真的可复用"

---

## 📚 本项目落地情况(截至当前)

| Level / 模块 | 状态 | 位置 |
|---|---|---|
| L1 Reflexion 记忆库(embedding + 词袋降级 + 分层召回 + 遗忘机制) | ✅ 已挂主 DAG | `appendix/evolve/memory.py` |
| Agent Evals 套件(schema + 一致性方差 + LLM-as-Judge) | ✅ 已落地 | `backend/tests/evals/` |
| L2 Prompt A/B(含 evolve_prompt / evaluate 闭环) | ✅ 已挂主 DAG + 5 个 CLI | `appendix/evolve/prompt_ab.py` |
| L3 技能自生成(Voyager 风格) | ✅ 已实现 | `appendix/evolve/skill_library.py` + m6 双向挂接 |
| L4 架构自我重构 | ❌ 未实现(文档标注演示用) | 设计文档 A.2 |
| L5 微调进化(LoRA/DPO) | ❌ 未实现(文档标注 Future Work) | 设计文档 A.2 |
| A.6 进化仪表盘 | ✅ 终端版 `evolve-dashboard` | `cli.py` |
| B.3 Pairwise 单轮 | ✅ `run_round` | `appendix/adversarial/` |
| B.3 多轮收敛循环 | ✅ `run_multi_round` | `appendix/adversarial/` |
| B.4 Tournament / Self-Play / Evolutionary / Minimax / Multi-Agent Debate | ❌ 未实现(留作练手题) | 设计文档 B.4 |

---

## ✅ 练手题参考答案

### 答案 1:DeepSeek embedding → 本地 BGE-M3 / Qdrant

本地 BGE-M3 替换(最小改动):
```python
# appendix/evolve/memory.py 的 _embed 改写
def _embed(self, text: str) -> list[float]:
    try:
        from FlagEmbedding import BGEM3FlagModel
        if not hasattr(self.__class__, "_bge"):
            self.__class__._bge = BGEM3FlagModel("BAAI/bge-m3", use_fp16=True)
        vec = self.__class__._bge.encode(text[:8000])["dense_vecs"]
        return vec.tolist()
    except Exception as e:
        logger.debug("[evolve] BGE 不可用: {}", e); return []
```

Qdrant 版需要把 recall 里的 SQLite 遍历 + 余弦改成调 Qdrant:
```python
from qdrant_client import QdrantClient, models
_qc = QdrantClient(url=settings.QDRANT_URL)
COL = "memories"

def add(self, mem_type, content):
    mid = uuid.uuid4().hex[:12]
    vec = self._embed(content)
    _qc.upsert(COL, points=[models.PointStruct(
        id=mid, vector=vec, payload={"type": mem_type, "content": content})])
    return mid

def recall(self, query, top_k=5):
    qv = self._embed(query)
    hits = _qc.search(COL, query_vector=qv, limit=top_k, score_threshold=0.3)
    return [{"id": h.id, "type": h.payload["type"], "content": h.payload["content"], "score": h.score} for h in hits]
```

要点:BGE-M3 单机就能跑,Qdrant 适合多进程 / 跨服务共享。接口形状不变,下游 m5 不用动。

### 答案 2:Tournament 模式(Elo 排名)

```python
# appendix/adversarial/tournament.py
from dataclasses import dataclass
import itertools, math, random

@dataclass
class EloPlayer:
    proposal: str
    rating: float = 1500.0

def elo_update(a: EloPlayer, b: EloPlayer, score_a: float, k: int = 32):
    ea = 1 / (1 + 10 ** ((b.rating - a.rating) / 400))
    a.rating += k * (score_a - ea)
    b.rating += k * ((1 - score_a) - (1 - ea))

def pairwise_judge(p1: str, p2: str) -> float:
    """让 Judge 比较两方案,返回 p1 的得分(1=p1 赢, 0=p2 赢, 0.5=平)。"""
    from co_scientist.llm import get_llm
    j = get_llm("critical")
    r = j.chat_json(messages=[
        {"role": "system", "content": "比较两个研究方案,输出 {\"winner\": \"A\"|\"B\"|\"tie\"}"},
        {"role": "user", "content": f"A: {p1}\n\nB: {p2}"},
    ], purpose="tournament")
    w = r.get("winner", "tie")
    return {"A": 1.0, "B": 0.0, "tie": 0.5}[w]

def run_tournament(proposals: list[str], rounds: int = 3) -> list[EloPlayer]:
    players = [EloPlayer(p) for p in proposals]
    for _ in range(rounds):
        pairs = list(itertools.combinations(range(len(players)), 2))
        random.shuffle(pairs)
        for i, j in pairs:
            sa = pairwise_judge(players[i].proposal, players[j].proposal)
            elo_update(players[i], players[j], sa)
    players.sort(key=lambda p: -p.rating)
    return players
```

要点:
- **round-robin 每对比一次,rounds 控制多轮** 让 Elo 收敛
- k=32 是国象标准 k-factor,新手研究可以调大(收敛快但噪声大)
- 成本:C(n,2) × rounds 次 Judge 调用,6 方案 × 3 轮 = 45 次,Claude 账单 ~$0.5

### 答案 3:跑 100 条 DPO 对开源 HF Hub

```bash
# 1. 准备 10 个种子问题
echo "RAG 减少 LLM 幻觉" > seeds.txt
echo "..."              >> seeds.txt

# 2. 多轮对抗批量产数据
python -m co_scientist.cli adversarial-build \
  --input seeds.txt --output data/outputs/dpo.jsonl \
  --multi-round --max-rounds 3

# 3. 清洗脚本(去 Judge 失败、score<5、chosen==rejected 的)
```

```python
import json
with open("data/outputs/dpo.jsonl") as f, open("dpo_clean.jsonl", "w") as out:
    for line in f:
        r = json.loads(line)
        if r["chosen"] == r["rejected"]: continue
        if r.get("score", 0) < 5: continue
        if len(r["chosen"]) < 50 or len(r["rejected"]) < 50: continue
        out.write(line)
```

```python
# 4. 上传 HF Hub
from datasets import Dataset
ds = Dataset.from_json("dpo_clean.jsonl")
ds.push_to_hub("your-username/ai-coscientist-dpo-100", token="hf_xxx")
```

要点:
- **清洗是关键**:LLM 产的 DPO 偶尔会 chosen == rejected 或极短 ,必须过一遍
- HF Hub 需要 CC-BY 或 MIT 授权,README 里标明
- 100 条在 DPO 训练里算小样本,说明上写"demo 规模,不适合直接训生产模型"

### 答案 4:灰度发布

改 `PromptABTester.best_for`:
```python
import random

def best_for_with_canary(self, name: str, canary_rate: float = 0.1):
    """
    canary_rate 概率返回一个新变体(runs 很少),其余返回当前最优。
    这样新变体能拿到真实流量去评分,不用单独跑评测集。
    """
    if random.random() < canary_rate:
        with sqlite3.connect(self.db_path) as conn:
            rows = conn.execute(
                "SELECT pid,name,text,total_score,runs FROM prompts "
                "WHERE name=? AND runs<3 ORDER BY runs ASC LIMIT 1",
                (name,)).fetchone()
        if rows:
            pid, _, text, total, runs = rows
            avg = total / runs if runs else 0.0
            return PromptVariant(pid, name, text, avg, runs)
    return self.best_for(name)  # 原逻辑
```

要点:
- **只让评分次数 < 3 的变体享受灰度**,防止旧变体永远抢不到流量
- canary_rate 建议 5-10%,更高会伤用户体验
- 真正生产级 A/B 建议上多臂老虎机(Thompson sampling),比固定 canary 更智能

### 答案 5:`_red_found_issue` 结构化

改 Red 的 system prompt 要求 JSON 输出:
```python
RED_SYS_V2 = """你是 Red Team。审查 Blue 方案,返回 JSON:
{"found": true|false, "attacks": [{"vuln": "...", "path": "..."}], "confidence": 0-1}
found=false 表示方案已经足够好,你找不到致命漏洞。"""
```

`run_round` 改用 `chat_json` + 读结构化字段:
```python
red_resp = red_chat.chat_json(messages=[{"role": "system", "content": RED_SYS_V2}, ...], ...)
red_attack = "\n".join([f"- {a['vuln']}: {a['path']}" for a in red_resp.get("attacks", [])])
red_found = red_resp.get("found", True)  # 直接布尔值,不用关键词 hack
```

然后 `_red_found_issue(attack_text)` 改成直接读 `red_found`。

要点:**结构化 > 关键词**,LLM 返回 `{"found": false}` 比文本里出现"未发现"靠谱多了。唯一代价:chat_json 偶尔 schema 失败,外层要 fallback 到旧关键词判定。

### 答案 6:L3 技能自生成

**代码现状**:本项目已实现,见 `appendix/evolve/skill_library.py`。核心类 `SkillLibrary` 提供 register / retrieve / get_code / bump_uses / delete / list_all 六个方法,m6 双向挂接:

- **m6_code/code_gen.py::generate_code** 调 LLM 前调 `SkillLibrary().retrieve(task_hint, top_k=3)`,命中的技能签名通过 `format_skills_for_prompt` 拼进 system prompt
- **m6_code/code_gen.py::_register_verified_skills** 在 `full_execute` 沙箱 exit=0 后扫描所有 .py,把符合条件的顶层函数注册入库:
  - 跳过 `main`/`train`/`evaluate`/下划线开头等"不可复用"的名字
  - 函数体 ≥ 3 行(太短的 helper 复用价值低)
  - 同名覆盖(`INSERT OR REPLACE`,简化版本管理)
- CLI: `skill-list` / `skill-show --name` / `skill-delete --name` / `evolve-dashboard`(含技能表)

**为什么放在 full_execute 成功后才注册,不在 dry_run 就注册**
`dry_run` 只做语法 + import 检查,无法保证函数逻辑正确。保守策略:只认"真跑过且 exit=0"的代码,避免库被"语法对但逻辑错"的函数污染。

**本项目简化的部分**(留作进阶练手):
- 召回用词袋重叠,可换 embedding(和 L1 memory 同样改造)
- 没做 Voyager 原论文的版本管理:同名注册直接覆盖,不比"新版本是否通过更多测试"
- 没做淘汰:`uses` 字段留着但没写衰减策略

**进阶练手**:
1. 把"函数体 ≥ 3 行 + 名字黑名单"的过滤改成 LLM 判断(给 function_src + task_context,问"这函数值不值得复用?"),准确率会高一个量级
2. 在 register 里跑一次独立沙箱跑 skill 单独导入 + 调用,确认它真的可独立运行(目前只靠"在主流程里跑通过"的间接证据)
3. 版本管理:同名函数保留多版本,retrieve 时返回 `uses` 最高的那版
