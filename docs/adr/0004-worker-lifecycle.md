---
title: Worker lifecycle
status: accepted-partial
scope: research_forge
---

# ADR-004：定义 Worker Lease、Heartbeat、Retry、Cancel 与 Resume

## Status

Accepted — partially implemented. Mission and Attempt have version/lease behavior; the documented
uniform concurrency contract for Task, Operation, and Approval remains incomplete.

## Context

Legacy 使用 FastAPI BackgroundTasks 和进程内状态，无法在进程崩溃后可靠恢复，也不能防止旧 Worker 重复提交。

## Decision

Celery/Redis 只运输 Attempt ID。PostgreSQL 控制领取和状态。

## Lease

领取使用数据库条件更新：

```text
attempt_id
lease_owner
lease_epoch
lease_expires_at
version
```

规则：

- 使用数据库时间；
- 每次领取 `lease_epoch + 1`；
- Heartbeat 必须匹配 Owner/Epoch；
- 所有 Finalize 必须匹配 Owner/Epoch/Expected Version；
- 旧 Worker Lease 丢失后不能提交；
- 外部副作用前后检查 Lease 和 Cancel。

## Crash Resume

- Worker Crash：同一 Attempt 可重新领取；
- 使用同一 Checkpoint 和 Operation Ledger；
- 已完成副作用通过 Idempotency 恢复；
- Queue 重投不会创建新业务状态。

## Logical Retry

- 只有 RetryableFailure 可自动重试；
- 逻辑重试创建新 Attempt；
- 新 Attempt 引用父 Attempt 和允许的输入 Artifact；
- 不覆盖旧 Checkpoint/Failure。

## Approval Resume

- Worker 产生 Proposal 后退出；
- Mission/Task 进入 WAITING_APPROVAL；
- Approval 持久化；
- 通过后创建子 Attempt；
- 子 Attempt 使用 `resume_from_checkpoint_ref`；
- Worker 不阻塞等待用户。

## Cancel

```text
RUNNING → CANCELLING → CANCELLED
```

- Cancel Token 持久化；
- Broker 停止容器；
- 未完成 Artifact 标为 Aborted；
- Cancel 后禁止新 Commit/Artifact/Operation；
- 无法确认停止时保持 CANCELLING，不伪装 CANCELLED。

## Checkpoint Lifecycle

- Namespace：Mission/Task/Attempt；
- Crash 继续同一 Attempt；
- Retry/Approval 创建新 Attempt；
- Terminal 后只读；
- 默认保留 7–30 天；
- 删除不影响 Mission/Git/Artifact/Audit。

## Validation

- Kill before ACK；
- Lease 过期双 Worker 竞争；
- 旧 Worker Finalize 被拒绝；
- Retry 不重复 Operation；
- Cancel 停止 Sandbox；
- Approval 后恢复不重复已完成步骤。

## Rollback

第一条 Slice 可以固定单并发，但仍必须实现 Epoch/Version，不能以“暂时只有一个 Worker”为由删除并发保护。
