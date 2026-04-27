# LIM 0006: 5-tuple fallback identity deferred

- **Status:** Accepted
- **Resolution intent:** must-resolve (technical debt)
- **Date proposed:** 2026-04-21
- **Date approved:** 2026-04-21
- **Proposed by:** claude (imap-mcp BDD phase B)
- **Approved by:** Randy N. Gupta
- **Related ADRs:** [ADR-0008](../adr/0008-saga-idempotency.md)
- **Related Guidelines:** BDD Guidelines §4.5, §13.2

## Resolution intent

`must-resolve`. ADR 0008 specifies a primary identity key (Message-ID)
and a fallback 5-tuple (from, date, subject, size, first-4-KiB SHA-256)
for saga idempotency. The primary path is fully covered; the fallback
path needs a schema extension on the WAL, an IMAP SEARCH composer that
emits the 5-tuple predicates, and conflict-resolution logic for
ambiguous matches. The work is a self-contained module-scoped change
and is deferred only because it is uncorrelated with the remaining
BDD phases.

## Context

Two scenarios in `saga_crash_recovery.feature` exercise the fallback
path:

- `saga_crash_recovery.feature:96` — "Fallback key — message without
  Message-ID identified uniquely by 5-tuple"
- `saga_crash_recovery.feature:110` — "Fallback key ambiguous — two
  identical candidates trigger escalation to needs_operator"

Both require that the saga, on recovery, perform:

1. Read the stored 5-tuple from the WAL.
2. Issue an IMAP SEARCH against the target folder with
   `FROM <from> SENTON <date> SUBJECT <subject>` (plus a follow-up
   RFC822.SIZE + partial BODY fetch to compare the 4-KiB SHA-256).
3. Count matches; 0 → APPEND, 1 → mark staged, ≥ 2 → escalate to
   `needs_operator` with reason `ambiguous_fallback_match`.

The current saga only performs the Message-ID lookup (primary key).

## Nature of the weakness

The two scenarios listed above are tagged `@pending @pending_LIM_0006`
and skipped. A message that arrives at the saga without a Message-ID
header (uncommon but produced by some mail-generating systems) would,
on crash recovery, cause a duplicate APPEND rather than being
identified against the target and recognised as already-staged.

## Why the clean solution is not chosen

Not a disproportionate-cost exception; simply a scope decision to
isolate the change. Its introduction requires:

- WAL schema migration (add columns: `fallback_from`, `fallback_date`,
  `fallback_subject`, `fallback_size`, `fallback_4kb_sha256`).
- Saga instrumentation: compute and persist the 5-tuple when
  Message-ID is absent.
- Recovery SEARCH composition + escalation branch.
- BDD harness: pre-seed WAL entries with fallback data.

These are bounded, orthogonal to Phase D/E/F concerns, and are tracked
as a single follow-up work item.

## Mitigations in place

- Scenarios that rely on Message-ID (the vast majority of real-world
  email) are fully covered.
- The saga currently returns `ERROR fetch_failed` with a logged
  exception rather than silently duplicating when Message-ID is
  absent — partial protection.
- The in-progress transaction count after a crash is monitored by
  `get_transaction_status`, so an operator seeing `status=staged`
  indefinitely can intervene.

## Residual risk

A rare crash during a saga whose source message has no Message-ID
header results in a duplicated target-side APPEND on recovery, or in
a `pending` transaction that blocks further work on the same source
UID. The scenario is unlikely in practice (≤ 1% of real-world
messages omit Message-ID) but is strictly covered by ADR 0008 and
therefore owed to the spec.

## Triggers for revisit

- An incident report attributes a duplicate target message to the
  fallback path.
- The proportion of Message-ID-less messages observed in the WAL
  exceeds 2% of saga traffic.
- ADR 0008 is superseded with a different idempotency contract.
- Phase B of the BDD plan is re-opened for full coverage.

## References

- Scenarios: `bdd/features/transactions/saga_crash_recovery.feature:96,110`
- ADR 0008, section "Fallback key".
- Plan: `/home/randy/.claude/plans/noble-prancing-glacier.md` (Phase B)
