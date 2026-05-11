# LIM 0011: Search performance on large mailboxes

- **Status:** Resolved
- **Resolution intent:** must-resolve (user-facing)
- **Date proposed:** 2026-05-07
- **Date resolved:** 2026-05-11 — Two-phase fix:
  (1) IMAP pre-filtering, 7-day default scope, limit/offset pagination,
  and blacklist fast-path were already implemented (discovered 2026-05-07).
  (2) N+1 connection bug found via OTEL tracing: per-message
  imap_fetch_envelope() opened 199 IMAP connections for 700 messages
  (270 seconds). Fixed by batch-fetching all envelopes in a single
  IMAP session via imap_fetch_envelopes_batch(). Result: 2 connections,
  2.8 seconds. Bug proven via connection_reuse.feature (42→4 connections
  in mock, 199→2 against production Gmail).
- **Proposed by:** Production deployment test
- **Related ADRs:** ADR-0004, ADR-0016, ADR-0017

## Context

The `search` tool with empty criteria (`{}`) fetches ALL UIDs from the
selected folder via IMAP `UID SEARCH ALL`, then evaluates each message
against the PDP sender rules. On a production mailbox with 53,000
messages in INBOX, this takes >60 seconds and causes MCP client
timeouts. Claude Code interprets the timeout as an authentication
failure, which is misleading.

A user asking "show me today's emails" is the standard use case, not
an edge case. The server must handle it within seconds.

## Nature of the weakness

1. The server does not translate MCP search criteria into IMAP SEARCH
   terms. Every search downloads the full UID set and filters
   in-process.
2. There is no way to limit result size. An agent asking for "the
   last 5 emails" still triggers a full-mailbox scan.
3. Empty criteria (`{}`) means "all messages" — there is no
   server-side default scope.

## Required changes

### 1. IMAP-side pre-filtering

Translate MCP search criteria into IMAP SEARCH terms BEFORE fetching
UIDs. The existing V1 matcher grammar (ADR 0004) maps naturally:

| MCP criteria        | IMAP SEARCH term           |
|---------------------|----------------------------|
| `from_domain`       | `FROM "@domain"`           |
| `from`              | `FROM "address"`           |
| `subject_contains`  | `SUBJECT "text"`           |
| `newer_than: 30d`   | `SINCE <date>`             |
| `older_than: 30d`   | `BEFORE <date>`            |
| `has_attachment`     | `LARGER 0` (heuristic)     |
| `size_gt`           | `LARGER n`                 |
| `size_lt`           | `SMALLER n`                |
| (empty criteria)    | `SINCE <7d ago>` (default) |

The PDP still applies sender-rule filtering after the IMAP pre-filter.
The pre-filter reduces the working set; the PDP enforces policy.

### 2. Pagination via `limit` and `offset`

Add optional `limit` (default 50) and `offset` (default 0) parameters
to the `search` tool. The server fetches UIDs from IMAP, applies PDP
filtering, then returns only the requested page. Response includes:

```json
{
  "uids": [...],
  "matched_total": 53000,
  "matched_visible": 12000,
  "filtered_out": 41000,
  "page_offset": 0,
  "page_limit": 50,
  "has_more": true
}
```

### 3. Feature file

New scenarios in a `search_pagination.feature`:

- Empty criteria on a large mailbox returns at most `limit` results
- `newer_than` criteria translates to IMAP `SINCE` (fast path)
- `offset` + `limit` pages through results
- `matched_total` reflects the full count, not the page size
- Default scope for empty criteria is last 7 days, not all time

### 4. Tool metadata update

`search` tool's `inputSchema` gains `limit` and `offset` properties.
`describe_policy` reflects the defaults. ADR-0016 tool-set doc
updated.

## Mitigations in place

- `list_accounts` and `list_folders` respond within seconds.
- `fetch_envelope` for a specific UID works correctly.
- Users can work around the issue by providing specific search
  criteria (but LLM agents typically don't know to do this).

## Triggers for revisit

- **Immediate.** This is the next work item.
