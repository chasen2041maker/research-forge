# AI Co-Scientist 项目总览（精简版）

> 适用范围：`backend/co_scientist/`。这是仓库保留的教学/演示型科研 Agent；当前生产主线为独立的 Research Forge，见 `backend/research_forge/`。

## 一句话定位

AI Co-Scientist 将一个研究方向或问题，依次处理为可检索的证据、知识图谱与研究空白、批判性结论、实验方案、验证代码和报告草稿；全流程由 LangGraph 状态图编排，并在首尾接入经验记忆。

它的价值是演示 **Agent 编排、证据驱动检索、多角色评审、受控执行与过程回放**，而不是宣称自动完成可信科研结论。

## 主流程

```mermaid
flowchart LR
    A[用户问题] --> R[召回历史经验]
    R --> M0[M0 候选课题发现，可选]
    M0 --> M1[M1 问题精炼]
    M1 --> M2[M2 多源文献检索]
    M2 --> M25[M2.5 文献访问状态，可选]
    M25 --> M3[M3 知识图谱与 GapCard]
    M3 --> M4[M4 证据驱动圆桌评审]
    M4 --> M5[M5 实验设计]
    M5 --> G[M5.5 研究质量门禁，可选]
    G --> M6[M6 代码生成与验证执行]
    M6 --> M7[M7 报告/论文草稿]
    M7 --> F[反思并沉淀经验]
```

M0、M2.5、M5.5 由配置开关控制；当前代码默认启用，但都可以关闭以回退到较短流程。M8 不在上述主线中，它用于对运行过程做分叉与回放。

## 分层架构

```text
CLI / FastAPI / 前端
        │
        ▼
LangGraph 主图（graph.py） ── 统一 ResearchState、节点进度、异常兜底
        │
        ├── 业务模块：M0 ~ M8、记忆与 A/B 评估
        ├── LLM 抽象：统一模型工厂、成本统计、预算保护
        ├── 工具与数据源：arXiv / OpenAlex / Semantic Scholar；可选 MCP
        └── 基础设施：SQLite / PostgreSQL、Redis、Qdrant、Neo4j、Docker
```

所有模块通过同一个 `ResearchState` 传递中间结果，避免模块直接相互耦合。每个节点由 `safe_node` 包装：节点出错会记录在 `error_log`，尽量不让单个失败直接中断整条流程。

## 模块速查

| 模块 | 负责什么 | 关键产出 |
| --- | --- | --- |
| M0 | 发现并排序候选课题 | `TopicCard`，用户可选择方向 |
| M1 | 用 PICO 等结构精炼问题 | 明确的研究问题与约束 |
| M2 / M2.5 | 多源检索并标记证据可访问性 | 文献、引用、证据等级 |
| M3 | 构建知识图谱并识别研究空白 | `GapCard` |
| M4 | 多角色评审并由编排器汇总 | `DecisionCard` / 评审结论 |
| M5 / M5.5 | 设计实验并做质量门禁 | 实验计划、通过/回退建议 |
| M6 | 生成验证代码；在允许时执行 | 代码、日志、执行结果 |
| M7 | 生成报告草稿并核验引用 | 报告/论文草稿 |
| M8 | 保存、分叉和回放运行轨迹 | 可比较的 fork |
| 附录 A / B | 经验记忆、Prompt A/B；对抗数据实验 | 学习和评估材料 |

## 运行与依赖：按需启用

| 目标 | 需要的组件 | 不需要的组件 |
| --- | --- | --- |
| 阅读代码、跑大部分单测 | Python、项目依赖 | Docker、PostgreSQL、Redis、Neo4j、Qdrant |
| 基础工作流 | LLM API 配置、网络检索能力 | Neo4j / Qdrant 可先不用 |
| 代码实际沙箱执行 | Docker | 不必同时启用所有数据库 |
| 完整基础设施模式 | PostgreSQL、Redis；按功能启用 Qdrant、Neo4j | 不等于每次开发都必须启动 |

根目录 `docker-compose.yml` 是这套旧系统的“完整基础设施”示例。它与 Research Forge 的部署 Compose 都会占用 PostgreSQL / Redis 的默认端口，二者不能同时启动。

## 当前事实与远期设想要分开

- **当前代码事实**：LangGraph 主流程、M0/M2.5/M5.5 开关、FastAPI 接口、模块化检索与评审、进度回调、成本与预算工具均可在代码中看到。
- **教学实现限制**：API 当前使用 `BackgroundTasks` 和进程内 `_runs` 保存运行状态；代码注释已明确，真正长任务应改由 Celery 等持久队列承载。
- **不应夸大为已交付**：自动技能生成、DPO 数据训练、通用 MCP 网关、全自动多分支研究和所有“生产级”设想，属于实验或路线图；是否可用必须以测试和对应代码为准。

## 最短阅读路径

1. [`graph.py`](../backend/co_scientist/graph.py)：先理解状态图与模块顺序。
2. [`state/research_state.py`](../backend/co_scientist/state/research_state.py)：再看状态契约。
3. [`m4_critique/`](../backend/co_scientist/modules/m4_critique/)：理解多 Agent 评审亮点。
4. [`m2_retriever/`](../backend/co_scientist/modules/m2_retriever/)：理解检索、融合排序和 MCP 边界。
5. [`api/main.py`](../backend/co_scientist/api/main.py)：最后看 API 与前端联动。

## 面试时怎么讲

“我做的是一个以 LangGraph 为编排核心的科研工作流原型。它把候选课题、证据检索、知识图谱、多人评审、实验设计和验证串成可观察的状态图；其中 M4 采用编排器协调多个评审角色，M2 统一多源检索，M8 支持运行分叉与回放。对于未完成的生产化能力，我会明确说明其路线图属性，不把设计稿当成交付能力。”
