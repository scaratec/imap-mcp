# LIM 0011: bulk_move tool deferred

- **Status:** Accepted
- **Resolution intent:** must-resolve (technical debt)
- **Date proposed:** 2026-06-05
- **Date approved:** 2026-06-05
- **Proposed by:** Randy Nel Gupta
- **Approved by:** Randy Nel Gupta
- **Scope:** server/src/imap_mcp/handlers/move.py
- **Related ADRs:** [ADR-0045](../adr/0045-write-visibility-floors-and-flow-monotonicity.md), [ADR-0046](../adr/0046-content-verified-sagas-and-wal-leases.md), [ADR-0052](../adr/0052-tool-surface-3-0-0-and-non-goals.md)
- **Related Guidelines:** BDD Guidelines §4.2 (positive and negative paths)

## Resolution intent

`must-resolve`. `bulk_move` is deferred until a dedicated ADR specifies
multi-message partial-failure semantics across intra-account `UID MOVE`
and cross-account WAL sagas. Unlike the permanent non-goals in
[ADR-0052](../adr/0052-tool-surface-3-0-0-and-non-goals.md), `bulk_move`
is planned future functionality.

## Context

[ADR-0052](../adr/0052-tool-surface-3-0-0-and-non-goals.md) exposes
`bulk_mark_seen` and `bulk_mark_tagged` alongside single-UID `mark_seen`
and `mark_tagged`, whereas `move` currently exists only in single-UID
form. Moving N matching messages requires one `search` / `list_messages`
call followed by N `move` calls.

## Nature of the weakness

Batch filing of dozens of messages incurs N MCP round-trips (reusing the
account's pooled IMAP connection per [ADR-0055](../adr/0055-bounded-per-account-connection-pool.md))
rather than one server-side loop.

## Why the clean solution is not chosen

Unlike idempotent flag stores, moving 50 messages across accounts creates
up to 50 independent sagas ([ADR-0046](../adr/0046-content-verified-sagas-and-wal-leases.md))
and per-message flow-monotonicity checks ([ADR-0045](../adr/0045-write-visibility-floors-and-flow-monotonicity.md)).
Designing partial-failure reporting, batch size caps, and pre-effect
intent granularity warrants its own focused ADR rather than rushing an
underspecified batch primitive into `3.0.0`.

## Mitigations in place

- Per-account connection pooling ([ADR-0055](../adr/0055-bounded-per-account-connection-pool.md))
  eliminates per-call TCP/TLS/auth overhead across sequential `move` calls.
- Every individual `move` enforces `Write <= Read` (`ENVELOPE`), flow
  monotonicity (`relocation_would_escalate`), and Gmail multi-label
  `move_out` checks ([ADR-0040](../adr/0040-message-centric-policy-and-gmail-composition.md),
  [ADR-0045](../adr/0045-write-visibility-floors-and-flow-monotonicity.md)).

## Residual risk

Agents filing large backlogs spend extra LLM turns issuing sequential
`move` calls and may stop mid-batch if turn limits expire, leaving the
remaining messages in the source folder.

## Triggers for revisit

- Usability matrix runs ([ADR-0060](../adr/0060-usability-matrix-evaluation-boundaries.md))
  showing turn-limit exhaustion or > 10 sequential `move` calls in routine
  filing tasks.
- Introduction of an automated archival agent processing > 25 messages
  per run.

## References

- [ADR-0045](../adr/0045-write-visibility-floors-and-flow-monotonicity.md), [ADR-0046](../adr/0046-content-verified-sagas-and-wal-leases.md), [ADR-0052](../adr/0052-tool-surface-3-0-0-and-non-goals.md), [ADR-0055](../adr/0055-bounded-per-account-connection-pool.md).
