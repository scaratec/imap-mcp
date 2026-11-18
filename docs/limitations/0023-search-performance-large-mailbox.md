# LIM 0023: Search performance on large mailboxes

- **Status:** Resolved
- **Resolution intent:** must-resolve (technical debt)
- **Date proposed:** 2026-05-07
- **Date approved:** 2026-05-07
- **Date resolved:** 2026-05-11
- **Proposed by:** Production deployment test
- **Approved by:** Randy Nel Gupta
- **Resolution commit:** 89ddd0e, fffa904, 46235eb, e94f308
- **Scope:** server/src/imap_mcp/handlers/search.py, server/src/imap_mcp/imap_core.py
- **Related ADRs:** [ADR-0041](../adr/0041-direction-aware-rule-grammar-and-provenance.md), [ADR-0043](../adr/0043-non-interference-transparency-and-search.md), [ADR-0052](../adr/0052-tool-surface-3-0-0-and-non-goals.md)
- **Related Guidelines:** BDD Guidelines §4.3, §4.5

## Resolution intent

`must-resolve`. Resolved in commits `89ddd0e`, `fffa904`, `46235eb`, and `e94f308` via IMAP-side search pre-filtering, the 7-day default window (`scope: "recent"`), `limit`/`offset` pagination, single-session batch envelope fetching (`fetch_envelopes_batch`), and the `list_messages` overview tool. Renumbered from the historical duplicate `0011-search-performance-large-mailbox.md` on 2026-11-17.

## Context

Initial V1 `search` fetched all UIDs via `UID SEARCH ALL` and opened one IMAP connection per message to evaluate sender rules, timing out (>60 s) on 53 000-message production inboxes.

## Nature of the weakness

Lack of IMAP `SEARCH` pushdown, pagination, default recent-window bounding, and batch `UID FETCH` made listing/searching large folders unusable.

## Why the clean solution is not chosen

Temporary V1 walking-skeleton simplification; paid down immediately upon production measurement.

## Mitigations in place

- Paid down completely via IMAP pre-filtering (`89ddd0e`), batch envelope fetching (`e94f308`), and connection pooling (`548de6d`, [ADR-0055](../adr/0055-bounded-per-account-connection-pool.md)).

## Residual risk

Sub-day duration queries (`newer_than: "60s"`) on folders receiving tens of thousands of messages in a single 24-hour window still fetch envelopes for that 1–2 day IMAP superset window before in-process UTC post-filtering ([ADR-0041](../adr/0041-direction-aware-rule-grammar-and-provenance.md)).

## Triggers for revisit

- Observed p95 latency > 5 seconds on `search` or `list_messages` in production traces.

## Resolution note

Resolved 2026-05-11 (`89ddd0e`, `fffa904`, `46235eb`, `e94f308`), reducing a 700-message inbox scan from 199 connections / 270 s to 2 connections / 2.8 s; non-interference count semantics refined in `3.0.0` under [ADR-0043](../adr/0043-non-interference-transparency-and-search.md).

## References

- [ADR-0041](../adr/0041-direction-aware-rule-grammar-and-provenance.md) — IMAP search pre-filter superset rules.
- [ADR-0043](../adr/0043-non-interference-transparency-and-search.md) — `applied_scope`, pagination, and non-interference.
- `bdd/features/performance/connection_reuse.feature`, `bdd/features/tool_surface/search_criteria_and_pagination.feature`.
