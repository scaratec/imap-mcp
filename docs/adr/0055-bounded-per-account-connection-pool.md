# ADR 0055: Bounded Per-Account IMAP Connection Pool

- **Status:** Accepted
- **Date:** 2026-11-17
- **Deciders:** Randy Nel Gupta

## Context

[ADR 0013](0013-hybrid-connection-pool.md) configured per-account IMAP pooling for a single server process (`max_size = 4`, `idle_ttl = 600s`) and left the caller response on `acquire_timeout` unspecified. On workstations where MCP clients spawn one stdio process per session ([ADR 0048](0048-pre-effect-audit-and-test-boundary.md)), ten sessions with `max_size = 4` can open 40 concurrent connections to one Gmail account, exceeding Gmail's ~15-connection per-account limit.

## Decision

We pool authenticated IMAP connections **per account with multi-instance-safe defaults (`max_size = 2`, `idle_ttl = 120s`, `max_age = 1800s`, `acquire_timeout = 30s`), mandatory `NOOP` liveness checks before reuse, full drain on credential/account changes, and a normalized `ALLOW + ERROR` (`error.type: "pool_exhausted"`) response when `acquire_timeout` elapses**.

### Pool parameters and semantics

| Setting | Env override | Default | Meaning |
|---|---|---|---|
| `max_size` | `IMAP_MCP_POOL_MAX_SIZE` | `2` | Max concurrent connections per account in one process (`>= 1`) |
| `idle_ttl` | `IMAP_MCP_POOL_IDLE_TTL` | `120` | Idle seconds before `LOGOUT` |
| `max_age` | `IMAP_MCP_POOL_MAX_AGE` | `1800` | Maximum connection lifetime in seconds |
| `acquire_timeout` | `IMAP_MCP_POOL_ACQUIRE_TIMEOUT` | `30` | Wait budget before returning `pool_exhausted` |

1. **Exclusive lease & `NOOP` check:** Idle connections < `max_age` are tested with `NOOP` before reuse; failed `NOOP` or expired age closes and replaces the connection transparently.
2. **Explicit `SELECT` per operation:** Pools are keyed strictly by `account_id` and issue `SELECT`/`EXAMINE` for the target wire mailbox before UID commands.
3. **Drain on token/config change:** OAuth token refresh ([ADR 0059](0059-oauth2-pkce-and-durable-account-health.md)) and `SIGHUP` account removal/credential updates ([ADR 0049](0049-single-caller-boundary-and-validated-loading.md)) close idle connections immediately, mark active leases to close on release, and log `pool_drain`.
4. **Closed `pool_exhausted` timeout:** Waiting longer than `acquire_timeout` returns `ALLOW + ERROR` with `error.type: "pool_exhausted"` ([ADR 0044](0044-normalized-envelope-and-code-registry.md)).

## Consequences

### Positive

- **Workstation connection safety.** `max_size = 2` and `idle_ttl = 120s` keep multi-session desktop usage within provider connection caps while reusing connections across sequential calls in a turn.
- **Deterministic saturation contract.** Callers receive `pool_exhausted` instead of unhandled timeouts.

### Negative

- High-concurrency single-instance sidecars may queue at `max_size = 2` unless `IMAP_MCP_POOL_MAX_SIZE=4` is set explicitly.

### Neutral

- Long-lived `IDLE` push watchers remain a permanent non-goal ([ADR 0052](0052-tool-surface-3-0-0-and-non-goals.md)).

## Security Implications

- **Attack surface.** Bounded `acquire_timeout` prevents connection-starvation deadlocks.
- **Trust boundaries.** Strict per-`account_id` pools prevent cross-account socket or credential sharing.
- **Data exposure.** `NOOP` transmits no mailbox data; `pool_exhausted` details omit credentials and hostnames.
- **Failure modes.** Dead sockets evict on `NOOP`; clean shutdown (`SIGTERM` / stdin EOF) sends `LOGOUT` on all pooled sessions.
- **Auditability.** `pool_drain` events and `pool_exhausted` outcomes write chained audit records ([ADR 0048](0048-pre-effect-audit-and-test-boundary.md)).

## Alternatives Considered

- **Retain `max_size = 4, idle_ttl = 600s` ([ADR 0013](0013-hybrid-connection-pool.md)).** Rejected: exhausts Gmail's per-account connection limit across concurrent local sessions.
- **Unpooled connect-per-call.** Rejected: adds 1–2 seconds of TLS + XOAUTH2 handshake latency to every MCP call.
- **Cross-process shared connection multiplexer daemon.** Rejected: requires local IPC passing authenticated IMAP sessions across independent agent processes, widening the attack surface.

## References

- Supersedes [ADR 0013](0013-hybrid-connection-pool.md).
- [ADR 0044](0044-normalized-envelope-and-code-registry.md), [ADR 0048](0048-pre-effect-audit-and-test-boundary.md), [ADR 0059](0059-oauth2-pkce-and-durable-account-health.md).
- RFC 3501 §6.1.2 (`NOOP`).
