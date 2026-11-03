# ADR 0036: Search Reports What Its Window Hid and Accepts Calendar Dates

- **Status:** Accepted
- **Date:** 2026-11-03
- **Deciders:** Randy Gupta
- **Amends:** [ADR 0024] (calendar bounds beside durations), [ADR 0026] §5 (response field)

## Context

[ADR 0026] §5 makes a seven-day window the default for `search` and
`list_messages` and reports it as `applied_scope`. The default answers
"what came in recently" quickly on large mailboxes (LIM-0011). For "all
invoices from Nordlicht" it returns only the newest one, and nothing in
the response says that older matches exist; `applied_scope` names the
window, not what it hid.

Time windows can only be relative (`newer_than`, `older_than`,
[ADR 0024]). "The third quarter of 2026" must be converted into day
counts by the caller, a step agents get wrong.

## Decision

**When the default window applies, the response reports how many
matches lie before it, and criteria accept calendar dates.**

- **`outside_scope_total`** is present exactly when `applied_scope` is
  `recent_7d`. It counts the messages the same criteria match before
  the window start, before policy filtering, like `matched_total`
  ([ADR 0017]: counts are disclosed, content is not). The server issues
  one extra `UID SEARCH` without `SINCE`; no message is fetched.
- **`since`** and **`before`** are criteria members holding ISO calendar
  dates (`YYYY-MM-DD`). `since` is inclusive, `before` exclusive, both
  with IMAP `SEARCH SINCE` / `BEFORE` semantics (RFC 3501 §6.4.4). Any
  of them sets `applied_scope` to `explicit_window`.
- **Validation** before any IMAP command: a malformed date, `since` not
  earlier than `before`, or a calendar bound combined with
  `newer_than`/`older_than` is an invalid-arguments error.
- The seven-day default stays.

## Consequences

### Positive

- A caller learns from one response that the list is incomplete and
  how much is missing.
- Calendar questions map directly onto criteria.

### Negative

- One extra IMAP search per default-window call.
- Two ways to bound time; the validation rule keeps them from mixing.

### Neutral

- Searching several folders in one call is not part of this decision.

## Security Implications

- **Attack surface:** two new criteria fields, validated strictly.
- **Data exposure:** a count of messages outside the window, before
  policy filtering. The same disclosure already exists for
  `matched_total` and `filtered_out` ([ADR 0017]).
- **Failure modes:** if the extra count search fails, the call fails
  rather than omitting the field silently.

## Alternatives Considered

- **Default to all time for narrow criteria.** Rejected: changes a
  recent decision and reopens LIM-0011 for large mailboxes.
- **Only calendar dates, no count.** Rejected: the hidden matches stay
  invisible.
- **Only the count.** Rejected: date arithmetic stays with the caller.

## References

- [ADR 0017], [ADR 0024], [ADR 0026]
- `docs/limitations/0011-search-performance-large-mailbox.md`
- `bdd/features/tool_surface/search_completeness.feature`

[ADR 0017]: 0017-response-transparency-for-filtered-data.md
[ADR 0024]: 0024-duration-grammar-single-source.md
[ADR 0026]: 0026-tool-surface-consistency.md
