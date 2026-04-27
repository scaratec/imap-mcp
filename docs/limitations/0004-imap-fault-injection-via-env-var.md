# LIM 0004: IMAP fault injection via in-process env var

- **Status:** Accepted
- **Resolution intent:** must-resolve (technical debt)
- **Date proposed:** 2026-04-21
- **Date approved:** 2026-04-21
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

## References

- Plan: `/home/randy/.claude/plans/noble-prancing-glacier.md` (Phase A)
- Module: `server/src/imap_mcp/fault_injection.py`
- Scenarios: `bdd/features/transactions/cross_account_move_saga.feature`
- BDD Guidelines §4.5, §13.2
