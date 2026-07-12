# ADR-003：使用 Operation Ledger 协调 PostgreSQL、Git 与 CAS

## Status

Accepted for v0.1

## Context

PostgreSQL、Git 和文件系统 CAS 无法共享 ACID 事务。只使用幂等键但没有恢复协议，会产生孤儿 Blob、重复 Commit 和错误 Attempt 状态。

## Decision

所有跨系统副作用先登记 `operations`：

```text
PREPARED → EXECUTING → SUCCEEDED
                    ├→ FAILED
                    └→ MANUAL_RECOVERY
```

字段：

```text
operation_id
idempotency_key
attempt_id
operation_type
input_hash
expected_parent_sha
target_ref_or_path
external_result_ref
lease_epoch
status
error_code
created_at/updated_at
```

## CAS Protocol

1. DB 创建 PREPARED Operation；
2. 写同文件系统 staging；
3. 计算 SHA-256 并 fsync；
4. Atomic Rename 到 `cas/<sha>`；
5. 已存在则验证 Hash/Size 后复用；
6. DB 同事务注册 Manifest/Relation/Metric，Operation SUCCEEDED；
7. 未登记 Blob 经过 TTL 由 GC 删除。

## Git Protocol

1. DB 创建 PREPARED Operation，记录 Expected Parent 和 Patch Hash；
2. Git Adapter 验证目标 Ref；
3. 应用 Patch，创建带 Operation Trailer 的 Commit；
4. 使用 `git update-ref <ref> <new> <expected-old>`；
5. DB Finalize 保存 Commit SHA；
6. Retry 通过专用 Operation Ref/结构化索引复用 Commit；
7. Ref 已推进则返回 ConcurrencyConflict，禁止强推。

Commit Trailer：

```text
Research-Forge-Operation: <operation_id>
Research-Forge-Input-Hash: <input_hash>
```

## Reconciler

定期扫描：

- 过期 PREPARED/EXECUTING；
- CAS Blob 已存在但 DB 未登记；
- Git Commit 已存在但 DB 未 Finalize；
- DB SUCCEEDED 但引用缺失；
- 超过 TTL 的孤儿资源。

Reconciler 必须幂等，不猜测模糊 Commit Message。

## Failure Injection

必须覆盖：

1. PREPARED 后 Kill；
2. CAS Rename 后 Kill；
3. Git Commit 后 Kill；
4. DB Finalize 后、Queue ACK 前 Kill。

每次恢复只有一个逻辑 Commit、一个正式 Artifact 和一个成功 Operation。

## Alternatives

- 分布式事务：拒绝，不适合 Git/文件系统；
- 只依赖 Celery Retry：拒绝，无法解决部分成功；
- 每次失败手工清理：拒绝，不能证明长任务可靠。

## Rollback

第一条 Slice 可以只实现 `CAS_PUT`、`WORKTREE_CREATE`、`SANDBOX_RUN` 三种 Operation，但所有类型遵守同一生命周期。
