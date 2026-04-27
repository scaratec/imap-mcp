# LIM 0005: IMAP MITM scenarios deferred

- **Status:** Accepted
- **Resolution intent:** must-resolve (technical debt)
- **Date proposed:** 2026-04-21
- **Date approved:** 2026-04-21
- **Proposed by:** claude (imap-mcp BDD phase C)
- **Approved by:** Randy N. Gupta
- **Related ADRs:** [ADR-0006](../adr/0006-cross-account-move-saga.md)
- **Related Guidelines:** BDD Guidelines §4.5

## Resolution intent

`must-resolve`. Two intra-account scenarios require behaviour that
cannot be produced by Dovecot's normal configuration surface or by the
in-process fault registry (LIM-0004). They require a MITM proxy that
rewrites IMAP frames on the wire. A dedicated subproject (similar in
shape to the planned mock-gmail and mock-oauth projects) will provide
such a proxy once the error-path catalogue grows beyond the current
two entries.

## Context

The `intra_account_move.feature` file specifies six error layers.
Four are fully covered with real Dovecot traffic. The remaining two
require:

1. Dovecot advertising a `CAPABILITY` response that omits `MOVE`, so
   that the server falls back to `COPY + STORE \Deleted + EXPUNGE`.
   The scenario additionally verifies the exact command sequence via
   an IMAP command log — i.e. the test must observe the wire-level
   commands, not just the functional outcome.
2. The `UIDVALIDITY` of a folder changing between the server's
   SEARCH and its subsequent UID MOVE in the same tool call, so that
   the server detects the stale UID and reports `uid_stale`.

Neither is achievable with Dovecot alone:

- `CAPABILITY` advertisement is fixed per Dovecot build; suppressing
  `MOVE` would require recompiling or running a proxy in front.
- `UIDVALIDITY` changes on a real server require physical folder
  recreation; the timing window inside a single tool call (between
  SEARCH and MOVE commands the server issues back-to-back) is
  sub-millisecond and impossible to racing externally.

## Nature of the weakness

Two scenarios remain unverified end-to-end:

- `intra_account_move.feature:47` — "COPY+STORE+EXPUNGE fallback on a
  server without MOVE".
- `intra_account_move.feature:91` — "UIDVALIDITY change during the call
  detected as uid_stale".

Both scenarios are tagged `@pending @pending_LIM_0005` and skipped by
the default `behave` invocation.

## Why the clean solution is not chosen

A MITM proxy capable of rewriting `CAPABILITY` responses and
interposing forced `EXISTS` / `UIDVALIDITY` notifications would
require:

- Protocol parsing for the subset of IMAP commands the server issues
  (LOGIN, SELECT, LIST, SEARCH, UID FETCH, UID MOVE, UID COPY,
  UID STORE, EXPUNGE, APPEND, LOGOUT) including literals and
  continuation responses.
- A command log that the BDD harness can read as a second
  verification channel (BDD Guidelines §13.2 requires two channels).
- Deterministic fault injection hooks similar to LIM-0004 but driven
  from outside the server process.

The work is of the same order of magnitude as the mock-gmail subproject
already planned under LIM-0002. Until a transport-level regression
forces this scope, the two scenarios remain deferred.

## Mitigations in place

- The `copy_message` + `store_flag` primitives in `imap_core.py`
  (the fallback sequence) are covered by the intra-account happy path
  (a real MOVE succeeds), by the cross-account saga's APPEND path
  (exercises COPY-equivalent semantics), and by unit tests for the
  individual primitives.
- Any client-visible regression in the fallback branch would surface
  as a failure in the cross-account saga scenarios, which *do* run
  against real Dovecot and issue the same STORE+EXPUNGE pattern via
  the saga's `_delete_source`.
- `UIDVALIDITY` handling shares the SELECT parser with the normal
  intra-account move; a bug in the parser would surface in the
  covered scenarios.

## Residual risk

A regression in the MOVE-fallback code path that fires only when the
server is used against an IMAP implementation without MOVE (e.g. a
Cyrus build predating RFC 6851, or a testing mock that omits the
capability) passes the BDD suite. Such a regression is detected only
by running the server against that specific implementation, which is
currently not in the CI matrix.

For UIDVALIDITY: the scenario's intent is to test the server's
detection logic, not the underlying IMAP behaviour. The detection
path is tiny (one comparison of UIDVALIDITY before and after SELECT)
and is covered by a unit test, but not end-to-end.

## Triggers for revisit

- The error-path catalogue adds a third MITM-dependent scenario.
- A production incident is attributed to the uncovered fallback code
  path.
- A third-party IMAP testing framework with a credible command-log
  surface becomes available.
- The mock-gmail subproject (LIM-0002) lands, making the MITM-style
  build-out template reusable.

## References

- Scenarios: `bdd/features/transactions/intra_account_move.feature:47,91`
- Plan: `/home/randy/.claude/plans/noble-prancing-glacier.md` (Phase C)
- ADR 0006
