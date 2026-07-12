# 方向 B:垂直场景 Agent(真实用户 / 真实数据)

> **一句话**:新起一个解决**真实用户真实痛点**的 Agent 产品,哪怕小,要有**真实使用日志**。这是简历上"产品感"的唯一来源。

## 为什么这个方向最能拿 offer

2026 的 HR/面试官见过太多"LangGraph + 5 个 Agent + RAG"的学生项目了。**区分度的关键不再是技术,而是"有没有真实用户"**:
- 有 100 个真实用户用过 > 任何技术深度
- 一段用户反馈 > 一页架构图
- "我迭代了 5 版因为用户说 X" > "我用了最新的 MCP"

## 为什么选这三个选题

| 选题 | 为什么值得做 | 你能复用现有 agent3 的什么? |
|---|---|---|
| **B1 个性化学习 Agent** | 延续你教育背景 + 真实用户好找 | memory / skill library / evals |
| **B2 代码仓库审计 Agent** | 对标 Cursor/Cognition + 技术深 | Docker 沙箱 / 多 Reviewer / RRF |
| **B3 面试准备 Agent** | 真用户是你自己 + 同届 | 多 Agent 圆桌 / Reflexion / 评估 |

---

# B1:个性化学习 Agent(推荐首选)

## 核心价值主张
"**学生上传错题 → Agent 分析知识点漏洞 → 生成专属复习计划 + 例题 → 长期跟踪进步**"

## 产品场景

1. **首次使用**:学生上传最近一次考试试卷 / 错题本 photo
2. **知识点分析**:Agent OCR + 分析每道错题背后的知识点
3. **漏洞诊断**:定位学生的弱项(不只是"错了",而是"为什么错")
4. **生成计划**:一周复习计划,每天 3-5 道针对性例题
5. **进度追踪**:每周末 Agent 复盘,调整下周计划(Reflexion 闭环)

## 技术栈

| 层 | 选型 | 复用自 agent3? |
|---|---|---|
| 前端 | Next.js + shadcn/ui | ❌ 新建 |
| 后端 | FastAPI + LangGraph | ✅ 结构类似 |
| OCR | PaddleOCR / Qwen-VL | ❌ 新增 |
| Agent 编排 | LangGraph DAG | ✅ 直接复用 |
| 记忆 | 改造 EvolvingMemory 成"学生画像 + 知识点掌握度" | ✅ 扩展 |
| 技能库 | SkillLibrary 改造成"例题生成模板库" | ✅ 扩展 |
| Evals | 改造 evals 套件做"生成题目质量" | ✅ 扩展 |
| 部署 | Docker + Railway/Vercel | ✅ 有 docker-compose |
| 数据库 | Postgres(用户+错题+知识图谱) | ❌ 新增 |
| 用户鉴权 | Clerk / Supabase Auth | ❌ 新增 |

## Agent 架构(9 个节点)

```
START
 ├─ upload_parse      (OCR + 题型分类)
 ├─ profile_recall    (召回该学生历史画像)← 复用 EvolvingMemory
 ├─ knowledge_map     (从错题抽取知识点 → 图谱节点)
 ├─ weakness_diagnose (多 Agent 圆桌:3 个诊断视角)← 复用 m4 范式
 │   ├─ concept_reviewer  (概念错还是应用错?)
 │   ├─ pattern_reviewer  (有没有固定错误模式?)
 │   └─ meta_teacher      (综合给出诊断)
 ├─ plan_generate     (生成复习计划)
 ├─ example_retrieve  (从例题库 SkillLibrary 找匹配题)
 ├─ example_generate  (库里没有就 LLM 新生成)
 ├─ weekly_reflect    (每周 Reflexion)← 复用 appendix_reflect
 └─ END
```

## 5 周详细计划

### Week 1:MVP 骨架
- Day 1-2:建 Next.js 前端 + FastAPI 后端骨架 + 用户鉴权
- Day 3-5:接 OCR(PaddleOCR 最简单) + 题型分类 LLM Prompt
- **里程碑**:上传一张错题图 → 后端识别出题目文本

### Week 2:核心 Agent 逻辑
- Day 6-8:实现 knowledge_map + weakness_diagnose(三 Agent 圆桌)
- Day 9-10:实现 plan_generate + example_retrieve
- **里程碑**:Demo 可跑完整流程(不求好看,求能跑通)

### Week 3:记忆与追踪
- Day 11-12:改造 EvolvingMemory → StudentProfile(持久化 + 按学生 ID 隔离)
- Day 13-14:实现 weekly_reflect 每周自动复盘
- Day 15:Evals:生成题目的质量(难度适配 / 知识点覆盖 / 语法正确)
- **里程碑**:同一个学生连续用 3 天,Agent 表现有可见提升

### Week 4:产品化
- Day 16-17:UI 打磨 —— 错题上传、计划展示、进度图表
- Day 18:部署 Railway + 域名 + 限流
- Day 19-20:**找 5-10 个真实用户**(同学 / 亲戚 / 学弟学妹)
- **里程碑**:有真实用户在跑,每天产生日志

### Week 5:迭代 + 包装
- Day 21-23:看用户日志,修最影响体验的 3-5 个问题
- Day 24-25:写项目总结博客 + 准备面试讲稿
- Day 26-28:录一个 2 分钟产品 Demo 视频
- **里程碑**:简历 link 上挂三个:GitHub / 在线体验 / Demo 视频

## 面试讲点(B1 独有)

| 面试官问 | 你答(要点) |
|---|---|
| 有多少用户? | 12 个真实用户,累计 87 次使用,日志我都看了 |
| 用户反馈什么? | 最意外的是某某(举一个具体例子,越具体越好) |
| 怎么评估 Agent 质量? | 多维:生成题目正确率(手工标注) / 知识点覆盖率 / 用户留存 / 一致性方差 |
| 知识图谱怎么做的? | 小学初中高中数学 / 英语的知识点列表手工整理了一份,约 200 个节点;实体关系从错题 LLM 抽 |
| 记忆模块怎么设计的? | Reflexion 的三层(经验/策略/失败)基础上,加了"知识点掌握度"作为 domain 类的子类型 |
| 踩过什么坑? | (挑两个真实的讲,越具体越有说服力) |

## 风险与降级

| 风险 | 概率 | 降级 |
|---|---|---|
| OCR 精度太差 | 高 | 先只接纯文本输入,OCR 放 v2 |
| 找不到真实用户 | 中 | 自己连续用 1 个月也行(讲"产品自闭环") |
| 学科覆盖难 | 高 | **先只做一个学科**(如高中数学),做深比做广有说服力 |
| 用户隐私 | 中 | 纯教学项目,加显眼免责声明 + 不留真实个人信息 |

---

# B2:代码仓库审计 Agent

## 核心价值主张
"**输入 GitHub URL → Agent 克隆仓库 → 多维度审计 → 改进建议报告**"

## 产品场景

1. 输入 `github.com/user/repo` 或上传 zip
2. Agent 克隆到本地沙箱
3. 多 Reviewer 圆桌审计:
   - `security_reviewer`:漏洞扫描 / 敏感信息泄露
   - `quality_reviewer`:代码复杂度 / 命名 / 重复
   - `test_reviewer`:测试覆盖率 / 测试质量
   - `doc_reviewer`:README / 注释 / API 文档
   - `meta_reviewer`:综合评分 + 优先级排序
4. 输出 Markdown 报告(类似 CodeRabbit / Sonar 的风格)

## 技术栈

| 层 | 选型 |
|---|---|
| 后端 | FastAPI + LangGraph |
| 沙箱 | Docker(直接复用 agent3 的 m6) |
| 静态分析 | ruff / bandit / radon(Python);eslint / semgrep(JS) |
| 测试执行 | pytest / jest 在沙箱里跑 |
| 嵌入/检索 | 代码片段 embedding(bge-code / jina-code) |
| 前端 | 最简:HTML + htmx(审计结果是 Markdown) |

## Agent 架构

这个和 agent3 的 m4 圆桌**几乎同构**,直接把 `ReviewerPersona` 换成这 5 类。

**复用率**:~70%(m4 结构 + Docker 沙箱 + evals 框架)

## 4 周计划

### Week 1
- GitHub URL → 本地克隆 + 语言识别 + 项目结构分析

### Week 2
- 5 个 Reviewer 的 prompt 设计 + 静态工具集成

### Week 3
- Meta-Reviewer 综合报告 + 前端展示

### Week 4
- 部署 + 找 5-10 个开源项目审计 + 收集反馈 + 博客

## 面试讲点(B2 独有)
- "我的 Agent 能审计任意 GitHub 项目,跑过 X 个开源项目,发现了 Y 类问题"
- "和 CodeRabbit / Sonar 的区别是:我的 5 个 Reviewer 可以并行独立评审,避免 anchoring bias"
- "Docker 沙箱隔离,就算仓库里有恶意代码也跑不出我的容器"

## 风险
- 大仓库(>10k files)处理慢 → 加 "只看 diff" 模式
- LLM 看代码经常"看懂了但说错" → 静态分析工具+ LLM 双重验证
- 安全风险:克隆陌生仓库 → 严格 Docker 沙箱,只读挂载

---

# B3:面试准备 Agent

## 核心价值主张
"**上传 JD + 简历 → Agent 生成定制题库 + 模拟面试 + 个性化反馈**"

## 产品场景

1. 输入:岗位 JD + 你的简历
2. Agent 分析岗位要点 + 你的经历匹配度
3. 出 10-20 道定制题(技术 + 行为)
4. 模拟面试:你回答,多 Agent 圆桌评价
   - `technical_reviewer`:技术正确性
   - `communication_reviewer`:表达流畅度
   - `behavioral_reviewer`:STAR 结构 / 真诚度
   - `hr_reviewer`:人岗匹配度
5. 每轮后给反馈 + 下一轮更难的题
6. 周报:你这周练了什么,进步在哪

## 技术栈

几乎 100% 复用 agent3:多 Reviewer 圆桌 + Reflexion 记忆 + evals + LaTeX→PDF 报告。

**额外需要**:
- 语音输入(Whisper)可选
- STT + TTS 做真实感(edge-tts)

## 3-4 周计划

最轻量的选题,因为技术完全复用。重点放在**UI 和真实感**。

## 为什么放最后一位
- 场景有点"元"(找工作的人做找工作工具)
- 面试官可能觉得"你做这个是为了面试加分",动机稍尴尬
- 但**真用户极好找**(你自己 + 同届 + 各种求职群),数据产生快

## 聪明讲法
> "我做这个不是为了应付面试,是因为我发现**自己总在同样的行为问题上翻车**,想造一个工具逼自己闭环练习。现在我已经用它练了 40 轮模拟面试,Agent 记录我每次的弱点,下次出题针对性加强。"

这样讲反而加分 —— 展示**自我驱动 + 产品自用 + 闭环思维**。

---

## 三选一决策

| 如果... | 选 |
|---|---|
| 你想最大化"场景不重合"的讲故事能力 | **B1** |
| 你技术自信强,想做最炫的工程 | **B2** |
| 你时间紧、想快速出成果 | **B3** |
| 你有教育行业人脉 | **B1** |
| 你关注开源社区 | **B2** |
| 你自己处于求职期 | **B3** |

---

## 全 B 方向通用建议

### 1. 真实用户比什么都重要
哪怕 5 个用户用一周,产生的**使用日志 + bug 反馈 + 意外发现**,面试价值超过 10 周的技术深度。

### 2. 复用 agent3 是黑魔法
新项目从零到能跑 Demo 的时间 = **2 周**(不复用的话 = 5 周起)。
你 agent3 已经建好的:LLM 抽象 / 多 Agent 编排 / 记忆 / evals / 沙箱,**全部是可搬模块**。

### 3. 博客 + Demo 视频是放大器
项目做完当天必做:
- GitHub README 要有 GIF Demo
- 在线 URL 一定要能访问
- 博客中英双版各一篇
- Twitter / 小红书 / 知乎发一下

### 4. 开工前必做:把 agent3 的这些模块解耦成独立包
```
backend/co_scientist/
├── llm/          ← 抽成 pip 包 co_scientist_llm
├── appendix/evolve/  ← 抽成 co_scientist_memory
└── tests/evals/  ← 抽成 co_scientist_evals
```
这样下个项目 `pip install` 就能用,复用率从 70% 升到 90%。

---

## 面试叙事("两个项目互补")话术

> "我做过两个 Agent 项目。
>
> 第一个是 **AI Co-Scientist**,研究级科研助手,展示的是**多 Agent 协作架构**能做到的深度 —— Orchestrator-Subagent 模式、Reflexion 式记忆、三层 evals。
>
> 第二个是 **个性化学习 Agent**(或 B2/B3),展示的是**把架构落到真实场景**的能力 —— 12 个真实用户、87 次使用日志、5 次迭代。
>
> 一个讲**技术能做多深**,一个讲**产品能解决多真实的问题**。加起来能证明我既不是只会堆框架的,也不是只会画 PPT 的。"

这段话讲出来,大部分面试官就不问别的了。
