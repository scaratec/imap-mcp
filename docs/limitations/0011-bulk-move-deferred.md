# LIM 0011: bulk_move tool deferred

- **Status:** Accepted
- **Resolution intent:** must-resolve (technical debt)
- **Date proposed:** 2026-06-05
- **Date approved:** 2026-06-05
- **Proposed by:** Randy Nel Gupta
- **Approved by:** Randy Nel Gupta
- **Related ADRs:** [ADR-0026](../adr/0026-tool-surface-consistency.md), [ADR-0006](../adr/0006-cross-account-move-via-saga.md)
- **Related Guidelines:** BDD Guidelines §4.2 (positive and negative paths)

## Resolution intent

`must-resolve` — when a caller use case demands it. The placeholder
in the tool surface is small; the gap exists because the design has
not been worked out, not because we have decided against the
capability.

## Context

[ADR 0026] adds `bulk_mark_tagged` to mirror `bulk_mark_seen`, but
does not add `bulk_move`. A user request "move all of these messages
to that folder" must be expressed as a search followed by N calls to
`move`. For a hundred-message clear-out the round-trip cost is
real, and the absence of a bulk variant breaks the symmetry the rest
of the bulk family establishes.

## Nature of the weakness

`bulk_move(account, source_folder, target_folder, criteria)` would
require a saga design decision that the present ADR set does not
make. Specifically:

- **Atomicity scope.** Is the whole batch one saga transaction (all-or-
  nothing), or N independent sagas? The existing `move` saga is per-
  message ([ADR 0006]); a batch saga would be a new WAL shape with
  its own recovery story.
- **Failure semantics.** On the 47th message of 100, the IMAP source
  returns a transient failure. Does the bulk abort? Continue and
  return a partial result? Both have a defensible position; the
  choice changes the response envelope and the audit-log shape.
- **Cross-account batches.** A single criteria may yield messages
  from multiple Gmail labels that all map to different
  `[Gmail]/All Mail` UIDs. Cross-account move-batch becomes a
  many-to-many saga that the existing one-to-one design does not
  cover.

None of these is impossible. Each is a real design decision that
ADR 0026 chose not to make under the time pressure of the present
refactor.

## Why the clean solution is not chosen

The clean solution — a `bulk_move` tool with the same shape as
`bulk_mark_seen` — would either commit to one of the answers above
silently (footgun) or require a dedicated ADR documenting the choice.
We chose to defer rather than ship a half-decided design. The
opportunity cost is small: a caller that needs the operation today
can loop over `move` with `search` results and pay the per-call
overhead.

## Mitigations in place

- The `search` + per-message `move` pattern works today and is
  documented in the project README.
- `bulk_mark_seen` and `bulk_mark_tagged` cover the common bulk
  use case (mark-as-read on a search result). The remaining bulk
  motion is the less common one in observed usage.
- The audit log records one entry per `move` call; a bulk-loop in
  caller code produces a clear trace.

## Residual risk

A caller looping `move` over a large result set may hit IMAP
connection limits or saga-table churn that a server-side bulk
implementation would amortize. The worst case is a noticeable
latency increase on operations of 100+ messages, not a correctness
failure. If a user reports this as friction, the LIM becomes a
trigger to design `bulk_move` properly.

## Triggers for revisit

- A user opens an issue describing concrete pain (latency, audit
  noise, IMAP throttling) from looped `move`.
- A new ADR addresses the atomicity-scope question for any other
  bulk tool, in which case `bulk_move` can adopt that decision.
- A future Gmail / Exchange feature offers a server-side batch-move
  primitive (`MOVE 1,3,5,7` is supported by RFC 6851 itself; a
  batch implementation could exploit it directly).

## References

- [ADR-0006](../adr/0006-cross-account-move-via-saga.md) — saga
  design for per-message moves; a bulk variant would extend or
  replace this.
- [ADR-0026](../adr/0026-tool-surface-consistency.md) — the
  decision that introduces `bulk_mark_tagged` but defers
  `bulk_move`, with the justification in this LIM.
- RFC 6851 — IMAP `MOVE` extension; supports message-set form
  natively.
