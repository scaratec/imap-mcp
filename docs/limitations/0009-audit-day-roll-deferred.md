# LIM 0009: Audit day-roll and retention deferred

- **Status:** Resolved
- **Resolution intent:** must-resolve (technical debt)
- **Date proposed:** 2026-04-21
- **Date approved:** 2026-04-21
- **Date mitigated:** 2026-04-28 — `AuditWriter.rotate()` mit
  hot/warm/delete-Boundaries, Day-Roll-Detection on startup, eof_day-
  Trailer mit `final_hash`, gzip preserving file mode, Test-Tool
  `_test_run_audit_rotation` exposed via `IMAP_MCP_TEST_MODE`. Day-
  rotation, file-mode-transitions, retention-deletion, gzip-integrity,
  retention-parameter-overrides und no-MCP-tool-reads-audit Szenarien
  laufen grün.
- **Date resolved:** 2026-05-06 — external-root-hash-hook und
  manual-deletion-detection implementiert. Structured logging via
  `logging.critical()` bei out-of-band-Löschung. Alle 7 Szenarien
  in `audit_retention.feature` grün.
- **Proposed by:** claude (imap-mcp BDD phase F)
- **Approved by:** Randy N. Gupta
- **Related ADRs:** [ADR-0021](../adr/0021-audit-format.md),
  [ADR-0022](../adr/0022-audit-retention.md)
- **Related Guidelines:** BDD Guidelines §4.5

## Resolution intent

`must-resolve`. The audit writer persists every record with a SHA-256
hash chain (ADR 0021) and the retention policy (ADR 0022) requires
day-rotation with gzip, warm/cold boundaries, and optional external
root-hash notification. The hash-chain and no-content-leak invariants
are covered end-to-end; everything that depends on UTC-day
boundaries needs time mocking that is not yet wired in the BDD
harness.

## Context

Thirteen scenarios require the server to cross a UTC day boundary
deterministically:

- `audit_log_format.feature:78` — hash chain spans day rotation.
- `audit_log_format.feature:113` — audit file permissions include a
  transition from 0600 to 0400 at day roll.
- `audit_retention.feature` (all 12 scenarios: gzip, deletion,
  parameter overrides, root-hash hook, manual-removal detection,
  warm-file permissions).

Time mocking via `freezegun` — or a server-side
`IMAP_MCP_FAKE_NOW_UTC` env var — plus a test-only
`_test_trigger_rotation` tool would unblock all of these. Neither is
yet implemented.

## Nature of the weakness

The thirteen scenarios are skipped and uncovered. A bug in the
day-roll path (e.g. forgetting to fsync the final_hash record, or
losing the prev_hash chain across the boundary) would pass the
current suite.

## Why the clean solution is not chosen

Scope-bounded. The retention subsystem is a small module on its own
with clear invariants (gzip at day+hot_days, delete at day+hot+warm,
permission transitions). Its BDD coverage is a single focused pass;
it is deferred only because the Phase F work sequencing put crash
recovery and HTTP transport ahead of it.

## Mitigations in place

- Hash chain within a single day is fully covered
  (`audit_log_format.feature:71` tamper scenario).
- Directory permissions 0700 and current-day file permissions 0600
  are enforced at creation (unit-tested in the audit writer).
- A manual inspection of the retention code path has confirmed no
  obvious logic error; the deferral is about having BDD-grade
  coverage, not about correctness belief.

## Residual risk

A silent regression in the day-roll path goes undetected until the
first production operator observes an inconsistent chain. In
practice the first real-world day roll happens within 24 h of
deployment; the blast radius is limited to the prior-day file and
can be manually verified.

## Triggers for revisit

- First production deployment reaches its first day roll.
- An operator reports a gap in the hash chain.
- The retention parameters are exposed in a configuration surface
  that admins edit (higher likelihood of regression).
- Phase F of the BDD plan is re-opened.

## Resolution

**Date:** 2026-05-06

The two remaining scenarios are now green:

1. **External root-hash hook** — `_invoke_hook(final_hash)` was already
   implemented in `AuditWriter`; the BDD step now configures the hook
   via `PolicyBuilder.audit_external_root_hook`, drives a day roll, and
   verifies the output file contains the expected `final_hash`.

2. **Manual-deletion detection** — `_detect_missing_active_file()` was
   already implemented; added `logging.critical()` at detection time so
   the "logs a critical error to its structured log" Then-step can
   verify via server stderr. The `audit_file_missing` record is emitted
   to the recreated file and verified via `AuditReader`.

## References

- Scenarios listed above.
- ADR 0021, ADR 0022.
- Plan: `/home/randy/.claude/plans/noble-prancing-glacier.md` (Phase F)
