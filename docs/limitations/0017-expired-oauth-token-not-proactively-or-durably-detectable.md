# LIM 0017: Expired OAuth refresh token is not proactively or durably detectable

- **Status:** Withdrawn
- **Resolution intent:** must-resolve (technical debt)
- **Date proposed:** 2026-06-18
- **Date approved:** n/a
- **Date withdrawn:** 2026-11-17
- **Proposed by:** Claude (implementation agent)
- **Approved by:** n/a
- **Withdrawal commit:** 8324738, 366c013
- **Scope:** server/src/imap_mcp/auth, server/src/imap_mcp/wal.py
- **Related ADRs:** [ADR-0064](../adr/0064-durable-account-health-and-token-lifecycle.md)
- **Related Guidelines:** BDD Guidelines §4.3 (persistence validation), §7.2

## Withdrawal note

Withdrawn during the `3.0.0` specification and security overhaul before owner acceptance. Durable `needs_rebootstrap` persistence is implemented in `wal.db` (`account_health` table, [ADR-0064](../adr/0064-durable-account-health-and-token-lifecycle.md)), surviving process restarts and `SIGHUP` reloads until cleared by `imap-mcp-oauth-bootstrap` or external refresh-token rotation.
