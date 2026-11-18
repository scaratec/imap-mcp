# LIM 0012: Wire-level response transparency uneven across tools

- **Status:** Withdrawn
- **Resolution intent:** must-resolve (technical debt)
- **Date proposed:** 2026-05-16
- **Date approved:** n/a
- **Date withdrawn:** 2026-11-17
- **Proposed by:** claude
- **Approved by:** n/a
- **Withdrawal commit:** 8324738, 366c013
- **Scope:** server/src/imap_mcp/handlers, server/src/imap_mcp/imap_core.py
- **Related ADRs:** [ADR-0044](../adr/0044-normalized-envelope-and-code-registry.md)
- **Related Guidelines:** BDD Guidelines §4.3, §4.5

## Withdrawal note

Withdrawn during the `3.0.0` specification and security overhaul before owner acceptance. Raw `imap_response` reflection was superseded by the normalized `ALLOW + ERROR` (`error.{type, detail}`) envelope and closed sanitized status-token registry in [ADR-0044](../adr/0044-normalized-envelope-and-code-registry.md), and silent `STORE` / `SELECT` / `APPEND` failures were resolved directly in `imap_core.py` and verified by wire-level BDD scenarios.
