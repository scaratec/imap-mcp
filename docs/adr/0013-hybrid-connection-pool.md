# ADR 0013: Hybrid IMAP Connection Pool

- **Status:** Accepted
- **Date:** 2026-04-20
- **Deciders:** Randy Nel Gupta

## Context

Establishing an IMAP session is expensive: TCP handshake, TLS handshake,
SASL authentication (XOAUTH2 for OAuth providers), and the initial
`CAPABILITY` / `SELECT` cycle. Even on a fast network, a Gmail-bound
connection takes around one second cold.

Typical agent workloads issue bursts of related calls against one
account: `list_folders`, `search`, `fetch_envelope` × N,
`fetch_body` × k, `move`. Paying connection cost per call inflates a
five-second burst into a minute of wall time.

On the other end of the spectrum, permanent connections are fragile:
servers idle them out after 10–30 minutes, OAuth tokens expire,
network segments drop. A pool of forever-open connections becomes a
state-management problem.

IDLE listeners, if offered, are a different shape again: they hold a
connection blocked on a server notification and cannot share with
other commands. Push events themselves are out of scope for this
server ([ADR 0014]), but some internal consumers (health checks,
optional future subscribers) may still need long-lived sessions.

## Decision

The server manages IMAP connections via a **hybrid pool with time-to-
live** semantics, per account. A dedicated code path exists for
long-lived sessions (IDLE-style); those are not managed by the pool.

```
Pool per account_id:
  max_size:       configurable (default 4)
  idle_ttl:       10 minutes          # drop on inactivity
  max_age:        1 hour              # hard cap against drift
  healthcheck:    NOOP before reuse   # invalidates stale sockets
  acquire_timeout: 30 seconds         # caller gets a clear error
```

Pool semantics:

- A connection is leased to exactly one caller at a time. After use
  it returns to the pool unless it has exceeded `max_age` or failed
  healthcheck, in which case it is closed.
- Folder state is carried inside a connection. On lease, if the
  connection is not selected on the required folder, a `SELECT` is
  issued. The pool key is per account, not per folder — segmenting by
  folder would explode size for no benefit.
- `NOOP` healthchecks are cheap and sufficient to detect a half-closed
  TCP socket or an expired auth.
- A connection whose auth has expired is closed and replaced. Re-auth
  on an existing socket is not attempted; not all servers handle it
  cleanly.

Separate category — **long-lived sessions** (for example IDLE-style
watchers, should an internal consumer ever need one):

- Owned by a single task, not recycled, not returned to any pool.
- Counted against a per-account limit separate from `max_size`.
- Closed explicitly by the owning task; leaked tasks are detected via
  structured concurrency ([ADR 0012]) and their sessions are torn down
  on the parent task group's exit.

Pool invalidation on **token refresh**: when [ADR 0010] renews an
access token, the pool is drained (all idle connections closed, leased
connections closed on return). Subsequent connections authenticate
with the new token. No attempt at in-place re-authentication.

**Saga transactions** ([ADR 0006]) use two independent leases — one
from the source pool, one from the target pool. They are not wrapped
into a "distributed transaction"; the correctness invariants of the
saga do not require simultaneous connection hold.

## Consequences

### Positive

- **Bursty workloads amortize the connect cost.** The first tool call
  in a session pays, subsequent calls within the TTL do not.
- **Stale connections are caught cheaply.** `NOOP` round-trip is under
  a few milliseconds on a healthy session; it is the canonical IMAP
  liveness probe.
- **No forever-open sessions by default.** `max_age` acts as a
  backstop against accumulated protocol drift, memory leaks in the
  IMAP library, or server-side state corruption.
- **IDLE-style consumers can coexist** without polluting the pool or
  starving it.

### Negative

- **Pool size requires tuning.** A too-small pool under bursty load
  queues callers; a too-large pool holds sockets open to no benefit.
  Defaults are conservative; operators tune per deployment.
- **Healthcheck false positives.** A connection that has just been
  interrupted mid-command may pass `NOOP` but fail the next real
  command. Handled by single retry with a fresh connection (at most
  once).
- **Saga uses two connections per move.** This is not a regression
  but is worth noting: pool sizing must account for saga concurrency
  as well as plain tool calls.

### Neutral

- Connection identity is never exposed to callers. The MCP layer
  speaks in account/folder/UID tuples; the pool is entirely below
  that abstraction.

## Security Implications

- **Token lifecycle is enforced at the pool boundary.** A refresh
  event drains the pool; the next authenticated connection uses the
  new token. A stale connection cannot linger with yesterday's
  credentials.
- **Per-account isolation.** Connections are pooled per `account_id`;
  there is no cross-account sharing even for identically-authenticated
  providers. An incident on one account does not contaminate another.
- **No shared TLS state across accounts.** Session resumption is
  allowed only within one account's pool; tickets are per-account.
- **Healthcheck data is minimal.** `NOOP` exposes no mailbox
  information; a malicious middlebox observing healthchecks learns
  only that the server occasionally says `NOOP`.
- **Leak detection.** Structured concurrency ([ADR 0012]) ensures
  orphaned long-lived sessions are torn down at parent cancellation.
  Silent connection leaks (holding OAuth-authenticated sockets with
  no owner) are a classical vulnerability surface; the pool logs
  non-returns and closes them aggressively.
- **Acquire timeout.** A caller that cannot lease a connection within
  `acquire_timeout` gets an explicit error rather than blocking
  indefinitely. Absent this, a misbehaving consumer could block all
  others and present as a stealth availability attack.

## Alternatives Considered

- **On-demand only (no pool).** Rejected. Cold-connect cost per call
  dominates real-agent workload; acceptable for a debugging script,
  not for production.
- **Permanent pool (no TTL).** Rejected. Servers close idle
  connections on their own schedule; a permanent pool would be half
  full of silently dead sockets.
- **Per-folder pool.** Rejected as described above: folder
  cardinality × account cardinality is too many buckets for
  negligible gain.
- **Shared cross-account pool.** Rejected; kills the isolation and
  per-token lifecycle properties.
- **Actor model with one task per connection.** Plausible but heavier
  than needed for a small number of accounts. A classical leased
  pool fits the problem shape.

## References

- [ADR 0006] — saga using two leases.
- [ADR 0010] — token refresh triggers pool drain.
- [ADR 0012] — structured concurrency used in implementation.
- [ADR 0014] — push / IDLE is out of MCP scope; informs the
  separate-from-pool long-lived-session category.
- RFC 3501 §6.1.2 — IMAP `NOOP` as liveness probe.
- RFC 2177 — IMAP IDLE.

[ADR 0006]: 0006-cross-account-move-via-saga.md
[ADR 0010]: 0010-configurable-token-cache-strategy.md
[ADR 0012]: 0012-python-runtime-and-library-stack.md
[ADR 0014]: 0014-policy-as-git-versioned-yaml.md
