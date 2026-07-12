# ADR-002：定义 PostgreSQL、Git、CAS 与 Checkpoint 的事实所有权

## Status

Accepted for v0.1

## Context

Legacy 同时使用 LangGraph SQLite、Fork DB、API 内存、输出目录和多个业务 SQLite，导致恢复、状态和 Artifact 可能冲突。

## Decision

| 事实 | 唯一可写主人 | DB 角色 |
|---|---|---|
| Mission/Task/Attempt/Approval/Operation 当前状态 | PostgreSQL | 完整业务状态 |
| 状态历史与待发布事件 | PostgreSQL Audit/Outbox | 同事务保存 |
| 代码、Diff、Branch、Commit | Git Object Database | 保存 SHA/Ref 索引 |
| 日志、指标、环境、补丁、Bundle 字节 | Local CAS | 保存 Manifest/关系 |
| Metric 语义 | Metric Artifact + PostgreSQL Index | Pointer、数值、单位和关联 |
| Agent 临时消息和游标 | Attempt Checkpoint Store | 保存 Checkpoint Ref |
| 队列消息 | 无事实权威 | Outbox/Attempt 决定是否执行 |
| UI Timeline | 无事实权威 | 查询 Audit |

CAS 拥有字节真相；PostgreSQL 拥有 Artifact 是否正式登记和属于哪个 Attempt 的业务真相。

## Explicit Non-Decisions

- 不采用完整 Event Sourcing；
- Redis/Celery Result Backend 不保存业务真相；
- LangGraph Checkpoint 不保存 Mission 当前状态；
- API 不维护 `_runs` 可写缓存；
- DB 不内联大 Artifact。

## Transaction Rules

以下必须同一 PostgreSQL 事务：

- 状态 + Version + Audit + Outbox；
- Lease/Heartbeat；
- Approval + 恢复状态；
- Operation Finalize + External Ref + Attempt 条件更新；
- Artifact Manifest + Metric Index + Attempt 关联；
- Claim + Evidence Edge + Validation；
- Mission Complete + Bundle Ref + 完整性结果。

## Consequences

- 需要 Reconciler 处理 DB/Git/CAS 部分成功；
- Checkpoint 删除不影响业务事实；
- UI 重启后从 DB 恢复；
- Artifact 篡改可通过 Hash 检测。

## Validation

- Architecture Test 限制事实写入位置；
- API 重启后状态一致；
- Redis 清空不改变 Mission 状态；
- Checkpoint 清理不破坏 Completed Bundle；
- Artifact 修改后验证失败。

## Rollback

PostgreSQL 可以在纯单元测试中由 InMemoryUoW 替代，但生产语义和约束不得改变。

