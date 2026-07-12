# 📚 AI Co-Scientist 教学资料

> 本目录把项目用到的所有知识点按"先工程后业务"的顺序整理成 12 个章节,
> 每章都是独立 Markdown,可单独阅读。配合源码学习效果最佳。

---

## 📖 章节地图

| # | 章节 | 关键技术 | 对应项目模块 |
|---|------|---------|------------|
| 01 | [基础工程](./01-基础工程/) | pydantic-settings、loguru、diskcache、SQLite | `config/`、`utils/` |
| 02 | [LLM 调用](./02-LLM调用/) | OpenAI SDK、Anthropic SDK、Prompt Cache、tenacity 重试、流式 | `llm/` |
| 03 | [Agent 框架](./03-Agent框架/) | LangGraph、StateGraph、Checkpointer、Reducer、interrupt | `state/`、`graph.py` |
| 04 | [RAG 检索](./04-RAG检索/) | Query Rewriting、RRF 融合、Citation Chasing、时间衰减、异步并发 | 模块 2 |
| 05 | [多 Agent 协作](./05-多Agent协作/) | 角色 Persona、并行评审、方差检测、Devil's Advocate、Meta 终裁 | 模块 4 |
| 06 | [知识图谱](./06-知识图谱/) | 三元组抽取、NetworkX、研究空白识别、GraphRAG | 模块 3 |
| 07 | [代码沙箱](./07-代码沙箱/) | Docker SDK、AST 静态检查、自我纠错循环、安全护栏 | 模块 6 |
| 08 | [论文生成](./08-论文生成/) | Style Guide Agent、并行章节、Editor 润色、引用防幻觉 | 模块 7 |
| 09 | [进化与对抗](./09-进化与对抗/) | Reflexion 记忆、Prompt A/B、Red/Blue Team、DPO 数据 | 附录 A/B |
| 10 | [Web 集成](./10-Web集成/) | FastAPI、WebSocket、Next.js、状态轮询 | `api/`、`frontend/` |
| 11 | [MCP 与外部集成](./11-MCP与外部集成/) | Model Context Protocol、stdio transport、FastMCP、feature flag | `m2/mcp_servers/`、`m2/mcp_client.py` |
| 12 | [生产级基础设施](./12-生产级基础设施/) | LangSmith 观测、Extended Thinking、Budget Guard、ContextVar 隔离 | `utils/observability.py`、`utils/budget_guard.py`、`llm/claude.py` |

---

## 🎯 推荐学习顺序

### 完全新手(从 0 开始)
按 01 → 10 顺序读,每章配合项目源码和"练手题"。

### 有 Python/Web 基础,只想学 Agent 部分
跳过 01,从 02 开始,重点 03、04、05。

### 准备面试 / 复习
直接读各章末尾的"📝 面试常见问题"清单。

---

## 🔗 相关资源

- 设计文档:`../AI-Co-Scientist-技术方案.md`
- 项目 README:`../README.md`
- 学习路线:`../LEARNING.md`(每章对应的代码文件清单)
