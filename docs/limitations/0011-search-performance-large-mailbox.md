# LIM 0011: Search performance on large mailboxes

- **Status:** Accepted
- **Resolution intent:** must-resolve (user-facing)
- **Date proposed:** 2026-05-07
- **Proposed by:** Production deployment test
- **Related ADRs:** ADR-0004, ADR-0017

## Context

The `search` tool with empty criteria (`{}`) fetches ALL UIDs from the
selected folder via IMAP `UID SEARCH ALL`, then evaluates each message
against the PDP sender rules. On a production mailbox with 53,000
messages in INBOX, this takes >60 seconds and causes MCP client
timeouts. Claude Code interprets the timeout as an authentication
failure, which is misleading.

## Nature of the weakness

The server does not translate MCP search criteria into IMAP SEARCH
terms. Every search downloads the full UID set and filters in-process.
For a whitelist folder with a small rule set matching a few senders,
this means downloading and discarding 99%+ of UIDs.

## Proposed fix

1. **IMAP-side pre-filtering:** Translate sender-rule predicates
   (`from_domain`, `subject_contains`, etc.) into IMAP SEARCH terms
   so the IMAP server filters first.
2. **Pagination:** Add `limit` and `offset` parameters to the
   `search` tool so callers can request pages of results.
3. **RECENT/SINCE shortcut:** When criteria are empty, default to
   `SEARCH SINCE <30d ago>` rather than `ALL`.

## Mitigations in place

- `list_accounts` and `list_folders` work correctly and respond
  within seconds.
- `fetch_envelope` for a specific UID works correctly.
- The issue only manifests on large mailboxes with empty search
  criteria.

## Triggers for revisit

- Any user deploying against a mailbox with >10,000 messages.
- Feature request for search pagination.
