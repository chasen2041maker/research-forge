# Sandbox Durability Implementation Result

Reviewed: 2026-07-12

## Implemented

- Each sandbox operation derives exactly one Docker name: `rf-<sha256(operation-id)[:20]>`. Containers are labelled with both `research-forge.operation` and an immutable request hash; Docker's anonymous `--rm` lifecycle is not used.
- Broker State is a host-local recovery directory controlled only by the sandbox broker. It persists `request.json`, `result.json`, `stdout.bin`, `stderr.bin`, and declared output bytes in a hashed operation directory. Request creation is exclusive across processes; writes use a staging file, file `fsync`, atomic replacement where replacement is appropriate, and directory `fsync`.
- A new broker validates schema version, operation ID, immutable request payload/hash, payload sizes, and SHA-256 values before returning a durable `SandboxResult`. Unsafe directories, escaping paths, and symbolic-link traversal fail closed.
- A broker first reloads durable completed state, then rediscovers an existing named container by immutable label. Concurrent brokers converge on the same container; a different request for the same operation ID is rejected.
- Timeouts and cancellation use `docker stop --time 2`, then `docker kill` if necessary, then `docker rm -f`. A successfully persisted result is written before its container is removed. A later retry removes an orphaned named container only after its immutable label is verified.
- Docker log collection drains both pipes while retaining no more than the requested total log budget.

## Evidence from this workspace

| Check | Result |
| --- | --- |
| `python -m pytest backend/tests/research_forge -q` | `68 passed, 9 skipped` |
| `python -m ruff check backend/research_forge backend/tests/research_forge` | Passed |
| CI-equivalent mypy command | Passed: 94 source files |
| `python backend/scripts/check_research_forge_secrets.py` | Passed |
| Frozen evaluator | `31/31` passed; manifest SHA-256 `7e66c8a9724e0208f9055dae8788d0c83577a36252b1e8f3bdd67202bf398aa2` |

The frozen evaluation report generated locally is intentionally not committed; CI publishes equivalent reports as build artifacts.

## Linux Docker acceptance gate

The current host is native Windows without Docker or an installed WSL distribution. The Docker-specific tests therefore skipped here; they were not represented as successful Docker execution. On a Linux/WSL2 host with Docker Engine, run:

```bash
python -m pytest backend/tests/research_forge/test_docker_e2e.py -q -m docker
```

The gate covers normal completion followed by a broker restart, two real broker processes recovering completed work, two brokers converging on one concurrently started container, process loss while a container is still running, cancellation, timeout cleanup, and an immutable-input conflict.

## State ownership, GC, and remaining risks

Broker State is recovery material, not a second business database: PostgreSQL owns Operation status, CAS owns registered artifacts, and Git owns commits. An operator may remove an operation directory only after terminal PostgreSQL reconciliation, verified CAS registration, and no unpublished Outbox work; the deployment runbook records that audited procedure.

Remaining operational risks are deliberately fail-closed: corrupt recovery bytes or a request/hash mismatch require investigation rather than an automatic re-run; local disk capacity and backup policy remain host-operator responsibilities; and a multi-host deployment needs host-affine routing or a separately designed shared state store with equivalent atomic-file guarantees. Linux Docker execution remains the final environment-specific acceptance step until the command above passes in CI or on a supported host.
