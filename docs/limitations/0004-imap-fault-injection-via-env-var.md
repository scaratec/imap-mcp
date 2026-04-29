# LIM 0004: IMAP fault injection via in-process env var

- **Status:** Resolved
- **Resolution intent:** must-resolve (technical debt)
- **Date proposed:** 2026-04-21
- **Date approved:** 2026-04-21
- **Date resolved:** 2026-04-29
- **Proposed by:** claude (imap-mcp BDD phase A)
- **Approved by:** Randy N. Gupta
- **Related ADRs:** [ADR-0006](../adr/0006-cross-account-move-saga.md),
  [ADR-0007](../adr/0007-wal-schema.md),
  [ADR-0008](../adr/0008-saga-idempotency.md),
  [ADR-0023](../adr/0023-test-only-control-surface.md)
- **Related Guidelines:** BDD Guidelines §4.5 (error-path enumeration),
  §13.2 (persistence validation)

## Resolution intent

`must-resolve`. The fault-injection mechanism is a test-only in-process
registry today. The clean long-term outcome is to inject faults at the
network layer — i.e. a MITM proxy between the server and Dovecot, or
primable failure modes on the Dovecot fixture itself — so that the
tested code path is indistinguishable from the production path. The
current mechanism is accepted as the quickest way to unblock seven
saga-level scenarios while the proxy subproject is scoped separately.

## Context

The saga scenarios in `features/transactions/cross_account_move_saga.feature`
exercise five distinct IMAP failure modes of the target server:

1. APPEND returns 5xx once
2. APPEND delays the response past a configured server timeout
3. APPEND returns 5xx on every attempt
4. EXPUNGE on the source returns 5xx once
5. Target server refuses connections

None of these are naturally producible by a healthy Dovecot instance,
and toggling them via live Dovecot configuration changes between
scenarios would be slow, racy, and brittle.

## Nature of the weakness

The server consults a process-local `FaultRegistry` (populated from the
`IMAP_MCP_FAULT_INJECTION` environment variable at startup) before
issuing APPEND, EXPUNGE, and connect operations. When a primed fault
matches, the registry short-circuits the real IMAP call and raises the
corresponding exception *inside the server process*. The actual network
stack, the Dovecot instance, and the aioimaplib transport layer are
not exercised by the fault path.

Consequence: any bug that lives only in the transport (e.g. a
half-closed connection after a 5xx, a retry-after-EXPUNGE-error race
against Dovecot's command pipeline, a TLS renegotiation quirk) is
invisible to the saga scenarios covered by this mechanism.

## Why the clean solution is not chosen

A MITM proxy that sits between the server and Dovecot is the clean
alternative. It would require a separate subproject (new repository
layout, Docker service, deterministic command-rewriting rules,
fixtures for every primable fault). At current scope that effort is
comparable to the rest of the BDD suite combined and is deferred until
either the fault-injection surface grows beyond APPEND/EXPUNGE/connect
or a transport-level bug is observed that this mechanism cannot
reproduce.

## Mitigations in place

- The registry is opt-in: absent the environment variable, no code
  path in the server consults it. Production behaviour is unchanged.
- The injection hooks live in a single module (`fault_injection.py`)
  and are called from exactly three points (`_open_imap`,
  `append_message`, `_delete_source`). The production call chain is
  trivially auditable.
- The `_test_run_recovery` tool that drives the retry-limit scenario
  is registered only when `IMAP_MCP_TEST_MODE=1`. It is not returned
  by `list_tools` and is rejected with JSON-RPC -32601 otherwise.
- The happy-path and idempotency-path saga scenarios use no fault
  injection at all — they remain a faithful end-to-end test of the
  production stack.

## Residual risk

A transport-level regression in the saga's APPEND or EXPUNGE path —
for example, a change to aioimaplib that leaves the connection in an
unclean state after a 5xx response — would pass all seven saga
scenarios but fail against a real Dovecot. The residual risk is that
such a regression reaches production and is only caught by a manual
against-real-server smoke test or by the production WAL reporting
elevated retry_count in operational dashboards.

## Triggers for revisit

- A new saga-level failure mode is needed that this registry cannot
  express (e.g. "the server returns 5xx after the command bytes have
  been accepted but before the status line").
- The fault-injection surface grows to ≥ 6 distinct primable faults.
- An incident report attributes a saga regression to a
  transport-level issue that the registry bypassed.
- A production-quality IMAP MITM proxy (e.g. from an OSS testing
  framework) becomes available without a substantial build-out.

## Resolution

Resolved 2026-04-29 by lifting all five fault modes onto the existing
MITM proxy that LIM-0005 introduced (`bdd/support/imap_proxy.py`). The
in-process env-var FaultRegistry (`server/src/imap_mcp/fault_injection.py`)
and its three hook calls (`imap_core._open_imap`, `imap_core.append_message`,
`saga._delete_source`) are deleted; the production call chain no longer
contains any test-mode branch for these failures. The five faults map
to wire-level proxy hooks as follows:

1. **Connection refuse.** No proxy is started. `_start_imap_proxy`
   picks a free port, releases it, and rewires the account's `port` so
   the server's TCP `connect` returns ECONNREFUSED. A pre-flight TCP
   probe inside `_open_imap` raises `ConnectionRefusedError`
   explicitly, since `aioimaplib` swallows the OS-level error and
   would otherwise surface as a generic `TimeoutError`.

2. **APPEND 5xx (next / every).** Proxy matches `<tag> APPEND` C2S,
   synthesises `<tag> NO [SERVERBUG] simulated error 500 (APPEND)\r\n`
   to the client, and DOES NOT forward upstream. The client's literal
   body is never sent (no `+ continue` from server) so upstream is
   untouched.

3. **APPEND delay.** Proxy matches `<tag> APPEND` C2S, sleeps the
   configured seconds, then drops the command (does not forward).
   The server's `asyncio.wait_for(append, timeout=N)` fires and the
   saga records `target_append_timeout`. On retry, the delay spec's
   `remaining` counter has been decremented — the next APPEND
   forwards normally.

4. **EXPUNGE 5xx (once).** Proxy matches `<tag> EXPUNGE`, synthesises
   `<tag> NO …\r\n`, drops the command. The retry attempt forwards
   normally and EXPUNGE succeeds.

Counter persistence: the proxy loads its config dict ONCE at process
start and shares it across all sessions; `remaining` decrements live
in that shared dict so they survive the per-retry IMAP reconnects the
saga's recovery loop performs. The per-connection reload that the
proxy did before LIM-0004 was unnecessary for our scenarios and
broke counter semantics.

The synchronous-IMAP-literal forwarding in `_pump_c2s` was
restructured: the command line is now forwarded to upstream FIRST
(so Dovecot can issue `+ continue`), then the literal body is read
from the client and forwarded byte-for-byte. The earlier "buffer
whole frame, then forward" approach deadlocked because the client
won't send the body without first seeing `+`.

The `_test_run_recovery` MCP tool (gated by `IMAP_MCP_TEST_MODE=1`)
remains. It is a recovery-loop trigger, not a fault-injection
mechanism — orthogonal to the LIM-0004 surface.

## References

- Plan: `/home/randy/.claude/plans/noble-prancing-glacier.md` (Phase R)
- Module: `bdd/support/imap_proxy.py` (proxy with fault hooks)
- Server: `server/src/imap_mcp/imap_core.py` (`_open_imap` pre-flight TCP probe)
- Scenarios: `bdd/features/transactions/cross_account_move_saga.feature`
- BDD Guidelines §4.5, §13.2
