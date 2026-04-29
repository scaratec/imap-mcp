# ADR 0023: Test-only control surface (env vars, hidden tools, private response keys)

- **Status:** Accepted
- **Date:** 2026-04-27
- **Deciders:** Randy Nel Gupta

## Context

The BDD suite must exercise behaviour the production stack alone cannot
produce deterministically: a saga that crashes between WAL transitions,
a recovery loop that runs on demand, an audit record that hashes a
sender's domain even though the sender is not in the tool's argument
list. Three concrete needs surfaced during Phases A, B and F:

1. **Saga fault injection.** A saga must terminate or fail at a specific
   WAL state to verify recovery (`saga_crash_recovery.feature`).
   See [LIM-0004][lim4] for the broader reasoning around in-process
   fault injection.
2. **Recovery trigger.** Some scenarios (`cross_account_move_saga.feature`
   retry-limit case, `saga_crash_recovery.feature`) need to drive the
   server's recovery loop a deterministic number of times after the
   move call has returned. The server cannot rely on a real
   background scheduler in the test harness without making the test
   non-deterministic.
3. **Audit-side data flow.** The DENY-with-`sender_blacklisted` audit
   record carries a `from_domain_sha256` field that hashes the
   sender's domain. The handler producing the DENY knows the sender
   address; the audit writer needs it without the value leaking into
   the JSON response delivered to the caller.

A standard production code path covers none of the three. Adding them
ad hoc — an env var here, a back-door tool there, a hidden response
field — is exactly the kind of accumulation of test-only mechanisms
that destabilizes a codebase. They need a single, named contract that
operators of every future change can recognise and audit.

[lim4]: ../limitations/0004-imap-fault-injection-via-env-var.md

## Decision

We define a **test-only control surface** with three primitives:

1. `IMAP_MCP_CRASH_AT` — environment variable consulted by `saga.py`.
2. `_test_run_recovery` — MCP tool registered only when
   `IMAP_MCP_TEST_MODE=1`.
3. `_<name>` — leading-underscore convention for private response
   fields that the audit writer consumes and strips before emitting
   the response to the caller. First instance: `_matched_sender`.

Each primitive is documented below as a contract: what it accepts,
when it activates, and what guarantees it keeps the production stack.

### 1. `IMAP_MCP_CRASH_AT`

The saga consults the environment variable at four named transition
points. If the value matches the point name, the server flushes
stdio and exits with `os._exit(1)`. Otherwise the saga proceeds.

| Value                       | Trigger location                                                                |
|-----------------------------|---------------------------------------------------------------------------------|
| `post_begin`                | After `wal.begin` and the `begin` audit record, before FETCH                    |
| `post_fetch`                | After `wal.record_fetch` and the `fetched` audit record, before APPEND          |
| `post_append_pre_staged`    | After `append_message` returns OK but before `wal.mark_staged`                  |
| `post_delete`               | After `wal.mark_deleted` and the `deleted` audit record, before `wal.commit`    |

A value not in this set is silently ignored. There is no `post_commit`
because a successful commit is a terminal saga state — recovery has
nothing to do.

The variable is consulted via a single helper, `_maybe_crash(at)`, in
`saga.py`. No production code path sets the variable; if it is unset
or empty, the helper is a no-op that adds one dictionary lookup per
transition.

### 2. `_test_run_recovery`

A single MCP tool registered alongside the production tools, but only
when `IMAP_MCP_TEST_MODE=1` is set in the server's environment at
startup. It accepts `{"passes": int}` and runs
`SagaManager.run_pending_recovery()` that many times, returning
`{"processed": <count>, "passes": <n>}`.

The tool is **not** listed by `tools/list` even when active. Test
clients invoke it via `raw_call("tools/call", …)`. This deliberate
asymmetry mirrors the BDD harness's stance: a feature file may invoke
the tool, but a discovery-style probe (e.g. `mcp_tool_discovery.feature`)
sees only the production set.

If `IMAP_MCP_TEST_MODE` is unset, an invocation of `_test_run_recovery`
is rejected with the same JSON-RPC -32601 ("Unknown tool") error that
any other unrecognised name produces. There is no "deny silently"
mode.

### 3. Private response fields (`_<name>` prefix)

A handler that needs to pass internal context to the audit writer
without exposing it on the JSON wire returns the value under a key
prefixed with a single underscore. The audit writer is allowed to
read the value and is required to remove the key from the response
dict before the response is serialized.

First instance: `_matched_sender` carries the sender address that
triggered a `sender_blacklisted` decision in `_handle_fetch_envelope`.
The audit writer consumes it, computes `from_domain_sha256`, and
removes the key. The caller never sees the cleartext sender.

The convention applies only in-process: there is no need for the
underscore in any wire-format schema. New private keys may be added
when a similar handler-to-audit data flow is needed; each addition
must extend the list below.

| Key              | Producer                  | Consumer        | Computed audit field    |
|------------------|---------------------------|-----------------|-------------------------|
| `_matched_sender`| `_handle_fetch_envelope`  | `_audit_tool_call` | `from_domain_sha256` |

## Consequences

### Positive

- **Test surface is enumerable.** Reading this ADR plus its three
  bullet points is enough to know every test-only hook in the server.
  No grep through code is required.
- **Production behaviour unchanged.** Each primitive is gated:
  `IMAP_MCP_CRASH_AT` consults an env var that is unset; the
  `_test_run_recovery` tool needs `IMAP_MCP_TEST_MODE=1`; the
  underscore-prefix convention only fires for keys the handler
  itself sets.
- **BDD scenarios stay deterministic.** Crash recovery and retry-limit
  scenarios run with the same wall-clock cost on every CI machine;
  the harness does not race against a real background scheduler.

### Negative

- **Three mechanisms add three audit points.** Any future change
  near the saga (`saga.py`), the tool dispatcher (`server.py`'s
  `known_tools` set), or the audit redaction must keep this ADR
  current.
- **`_test_run_recovery` is one more conditional in the dispatcher.**
  Production deployments that omit `IMAP_MCP_TEST_MODE` pay a single
  env-var lookup at startup; trivial.
- **Underscore-prefix convention is a private contract.** A future
  contributor unfamiliar with this ADR who adds a key like
  `_user_token` to a response could mistakenly believe it is hidden
  from the caller — only this ADR's enumeration of consumers
  guarantees the field is actually stripped.

### Neutral

- The control surface is intentionally not part of `imap-mcp`'s
  public protocol. It is documented as an implementation detail
  alongside the BDD harness it serves.

## Security Implications

- **Attack surface.** None added in production deployments. All three
  primitives are gated:
  - `IMAP_MCP_CRASH_AT`: harmless when unset; even when set, it can
    only crash the server (denial-of-service against itself), not
    leak data or escalate access. A malicious operator with
    environment-variable-set capability can already terminate the
    process by other means.
  - `_test_run_recovery`: requires `IMAP_MCP_TEST_MODE=1`; without
    it, invocations return -32601. With it, the tool only re-runs
    recovery for transactions already present in the WAL — it does
    not bypass policy or authorization.
  - Underscore-prefix keys: strictly internal. The audit writer is
    the sole consumer; the `_emit` step never sees the key.
- **Trust boundaries.** Unchanged. The production trust boundary
  (caller ↔ server, server ↔ IMAP) is identical to a build that
  omits this ADR's primitives.
- **Data exposure.** The `_matched_sender` private field exists
  *because* of a security requirement (no cleartext sender in the
  caller's response). The convention prevents the field from
  leaking; the test discipline below makes that discipline
  verifiable.
- **Failure modes.** A leak of a `_<name>` key would manifest as the
  field appearing in a caller-visible response. The
  no-content-leak scenario (`audit_log_format.feature`) and the
  reason-code-contract scenarios indirectly catch this: any `_*` key
  appearing in a response payload would be picked up as an
  unexpected field by strict assertions over the response schema in
  the contract tests.
- **Auditability.** The crash-injection mechanism does not log;
  there is nothing to audit because the server exits immediately.
  `_test_run_recovery` writes a `saga_transition` audit record per
  resumed transaction, the same as a normal recovery would. The
  underscore-prefix consumer (`_matched_sender` → `from_domain_sha256`)
  *is* the audit path.

## Alternatives Considered

- **Keep the three mechanisms undocumented.** Rejected. Each
  mechanism alters server behaviour visibly; without an ADR a
  future contributor must reverse-engineer it from BDD step code.
- **Move the crash hook into a dedicated test build.** Rejected.
  Maintaining a separate "test build" doubles the integration test
  matrix without adding signal — the production code path is the
  one we want to test.
- **Use a real background scheduler for recovery.** Rejected. A
  realistic scheduler with a 10-second tick makes scenarios
  flaky and CI runs longer; the determinism gain from `_test_run_recovery`
  is decisive.
- **Pass the sender as a top-level response field then redact in the
  serializer.** Rejected. Top-level fields are part of the response
  schema; a future caller might rely on its presence ("the field is
  there, it must be safe"). Underscore-prefix makes the contract
  unambiguous: never present in a serialized response.
- **Use a side-channel (thread-local or context-var) instead of a
  private response key.** Rejected. The handler-to-audit data flow
  benefits from being explicit at the call site; a side-channel
  hides the dependency and is a known source of hard-to-reproduce
  bugs.

## References

- [LIM-0004](../limitations/0004-imap-fault-injection-via-env-var.md)
  — env-based fault injection for IMAP I/O. Resolved 2026-04-29:
  superseded by wire-level proxy hooks in `bdd/support/imap_proxy.py`.
  This ADR continues to document the saga-state-level crash injection
  (`IMAP_MCP_CRASH_AT`), which remains an in-process mechanism.
- [ADR-0006](0006-cross-account-move-saga.md) — the saga whose state
  transitions `IMAP_MCP_CRASH_AT` partitions.
- [ADR-0007](0007-wal-schema.md) — the WAL inspected by recovery.
- [ADR-0017](0017-response-transparency-for-filtered-data.md) §2.3
  — closed reason-code vocabulary that drives `_matched_sender`'s
  existence.
- [ADR-0018](0018-non-goal-tool-surface.md) — the discovery
  asymmetry that keeps `_test_run_recovery` off `tools/list`.
- BDD scenarios: `cross_account_move_saga.feature`,
  `saga_crash_recovery.feature`, `audit_log_format.feature`
  (sender hashing).
