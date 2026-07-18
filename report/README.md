# Report 文档入口

这组材料原本把“技术方案、架构设想、学习笔记、面试稿”分别展开，内容重叠较多。当前分支已把 Research Studio 和 Forge Runtime 通过受控交接连接为一个产品流程，因此以 **[项目功能架构总览](./项目功能架构总览.md)** 作为唯一默认入口；先读它即可了解全项目，而不必通读全部原稿。

## 建议阅读顺序

1. [项目功能架构总览](./项目功能架构总览.md)：先理解 Studio、Forge、交接层和验证边界。
2. [AI Co-Scientist 项目总览（精简版）](./AI-Co-Scientist_项目总览_精简版.md)：再深入 Studio 的 LangGraph 工作流。
3. `../backend/co_scientist/graph.py`：以代码确认 Studio 主工作流。
4. `../backend/research_gateway/studio_to_forge.py`：确认 Studio → Forge 的受控交接。
5. 有明确目的时，再查阅 `原始材料/` 中的专题稿。

## 原始材料如何使用

| 目的 | 对应材料 | 使用建议 |
| --- | --- | --- |
| 查完整设计细节 | `AI-Co-Scientist-技术方案.md` | 参考资料，不作为当前实现状态的唯一依据。 |
| 查 M0 / GapCard / M5.5 设计演进 | `新增架构设想_整理版.md` | 优先读这一份；长版仅保留历史细节。 |
| 准备面试 | `面试讲稿_终极版.md` | 按需摘取，不必背诵全文。 |
| 补基础概念 | `学习过程问题发现与体会.md`、`学习路径指南.md` | 学习材料，不属于项目规格。 |
| 了解历史阅读路线 | `项目阅读顺序.md` | 已被本 README 的新入口替代。 |

## 范围说明

`backend/co_scientist`（Research Studio）和 `backend/research_forge`（Forge Runtime）仍是两个独立系统：不共享内部状态、业务数据库或实现导入。当前 `feat/studio-forge-handoff` 分支新增了由版本化 JSON 合约驱动的 Studio → Forge → Studio 结果回传流程。不要把本目录中的远期设想当作已实现功能。

原稿均移动到 `原始材料/` 保留，未删除任何内容。
