# ADR 0024: Duration Grammar Single Source

- **Status:** Accepted
- **Date:** 2026-06-05
- **Deciders:** Randy Nel Gupta

## Context

Two surfaces in the server accept a "duration" string from the caller:

- The policy file's `newer_than` / `older_than` rule predicates ([ADR 0004]),
  parsed by `policy.py::_parse_duration` with units `s/m/h/d/w/y`.
- The MCP `search` / `list_messages` / `bulk_mark_seen` tools, which forward
  a `criteria.newer_than` / `criteria.older_than` string into IMAP `SINCE` /
  `BEFORE` terms.

The two surfaces drifted. The search-handler's inline parser was written as
`int(value.rstrip("d"))` — it works only for `d`. A caller asking for
`{"newer_than": "10y"}` crashes the server with `ValueError`, while the
same string in a policy file is accepted. The two parsers must have been
one from the start; they were not.

The MCP tool surface also publishes no constraint on the duration string —
`criteria` is typed only as `{"type": "object"}`. There is no schema-level
gate, so malformed values reach the handler and turn into stack traces.

Finally, IMAP `SINCE` / `BEFORE` carry **day resolution** only
(`dd-Mon-yyyy`). Sub-day values like `60s` or `1h` cannot be expressed
on the IMAP wire. The previous handler had no story for this — it
implicitly assumed `d` everywhere, which is why no rounding question
ever surfaced.

A decision is needed now because (a) the bug is user-visible and
(b) the broader Tool-Surface refactor (ADRs 0025–0027) needs a
formalized duration grammar to embed in the tool JSON-Schema.

## Decision

We use **one parser for duration strings throughout the server**, exposed
as `policy.parse_duration`, and we **express its grammar in the MCP tool
schema** as a JSON-Schema pattern. Sub-day values are rounded by the
search handler in a direction that never under-includes; the per-message
post-filter narrows the result back to the requested granularity.

### Grammar

A duration string matches the regular expression `^[0-9]+[smhdwy]$`:

| Unit | Meaning  | Seconds      |
|------|----------|--------------|
| `s`  | seconds  | 1            |
| `m`  | minutes  | 60           |
| `h`  | hours    | 3 600        |
| `d`  | days     | 86 400       |
| `w`  | weeks    | 604 800      |
| `y`  | years    | 31 536 000   |

There is no fractional form, no compound form ("1d12h"), no signed form.
A string outside the grammar is a schema-level reject (`-32602
Invalid params`); the handler is never reached.

### Single parser

`policy.parse_duration(value: str) -> int` (renamed from
`_parse_duration` and made public) returns seconds. It is the sole
implementation. `policy._match_single_predicate`, the search-handler's
criteria translator, and any future caller of "what does `1w` mean"
all import the same function.

### IMAP day-resolution contract

IMAP `SINCE date` and `BEFORE date` accept only a date, not a datetime.
For each side we choose the rounding that **never under-includes**:

- `newer_than`: seconds are rounded **up** to whole days
  (`ceil(seconds / 86400)`), then `SINCE = today - ceil_days`. A
  caller asking for `60s` gets `SINCE = today - 1d` — strictly more
  messages than asked for at the IMAP layer.
- `older_than`: seconds are rounded **down** to whole days
  (`floor(seconds / 86400)`), then `BEFORE = today - floor_days`. A
  caller asking for `30h` (1 day, 6 hours) gets `BEFORE = today - 1d`
  — again strictly more messages than asked for.

The IMAP layer therefore always returns a **superset** of the true
match set. The per-message post-filter in `handlers/search.py`
(`_criteria_match` → `_match_single_predicate`) then evaluates the
duration at second resolution against each message's `Date:` header
and discards messages outside the true window. The caller sees only
the exact requested set.

To make the post-filter actually run, `newer_than` and `older_than`
are removed from the `criteria_needs_envelope` allow-list: any
criteria containing them forces envelope fetch + post-filter, even
on a blacklist folder with no rules where the PDP is otherwise
pre-determined.

## Consequences

### Positive

- **One grammar, one parser.** A change to the grammar is one edit, not
  two; the policy file and the MCP surface cannot drift again.
- **Schema-level rejection of garbage.** `"10x"` is refused at the
  JSON-RPC layer before the handler runs; no more handler crashes from
  malformed values.
- **Sub-day filters work.** `60s` and `1h` are now meaningful in
  `criteria.newer_than`; the IMAP-layer over-fetch is bounded to one
  day and invisible to the caller.
- **Symmetry between policy and tool surface.** A rule that says
  `newer_than: "30d"` and a search that asks `newer_than: "30d"` see
  the same time window.

### Negative

- **Over-fetch on sub-day filters.** A `60s` query against a folder
  with 10 000 messages from the last 24 hours fetches all 10 000
  envelopes before the post-filter discards them. Acceptable for V1;
  if it becomes a load issue, the post-filter can short-circuit on
  the sorted envelope list (most recent first). A `bulk_mark_seen`
  with sub-day criteria pays the same cost.
- **No fractional or compound durations.** `1.5h` and `1d12h` are not
  expressible. Operators who need finer control compose multiple
  predicates or use a precise IMAP unit; the grammar stays small.

### Neutral

- The grammar is closed (same closure rule as [ADR 0004] §Neutral).
  Adding a unit requires an ADR amendment.

## Security Implications

- **Attack surface shrinks.** A pattern at the schema layer rejects
  malformed strings before they reach Python code. Resource exhaustion
  via `int("9999...")` is bounded by the maximum length the
  JSON-Schema-validator imposes on the request envelope (MCP transport
  caps this independently).
- **No data exposure change.** The set of messages a caller sees is
  identical to the set defined by the existing predicate semantics —
  the rounding contract guarantees only over-fetch at the IMAP layer,
  never over-disclosure at the caller-visible layer.
- **Failure modes are noisier on purpose.** A schema-reject is a
  visible `-32602`, not a silent empty result. A caller who mistypes a
  unit learns immediately. The audit log records the rejection under
  the existing `unknown_tool`-style channel for schema failures.
- **Auditability.** Each search call is already audit-recorded with a
  `search_query_digest` (`dispatch.py::_sanitise_args`). The digest is
  computed over the post-validation criteria; a successful call thus
  always corresponds to a grammar-valid string.

## Alternatives Considered

- **Fix the search handler's parser to also accept `s/m/h/w/y`, leave
  the schema as `{"type":"object"}`.** Rejected: keeps two parsers in
  the codebase. The drift would reappear the next time someone added
  a unit to the policy parser only.
- **Express only `Nd` in the MCP tool surface; reject other units at
  schema level.** Rejected: artificially constrains the tool surface
  to a subset of the policy grammar. A caller cannot ask for
  questions the policy can answer.
- **Use ISO 8601 durations (`P30D`, `PT1H`).** Rejected: more
  expressive than needed, harder to type, and the existing policy
  grammar already uses the short form. The choice was forced by
  prior art; switching the policy grammar now would break every
  policy file in production.
- **Push duration resolution into the post-filter only, send `ALL` to
  IMAP.** Rejected: scales catastrophically on large mailboxes. The
  day-resolution `SINCE` / `BEFORE` cut the working set by orders of
  magnitude on typical inboxes; the post-filter only handles the
  sub-day refinement.
- **Round sub-day values to the nearest day instead of up/down.**
  Rejected: under-includes for `newer_than` (a 30-second-old message
  could be excluded from a `60s` query). The chosen rounding always
  over-includes at the IMAP layer; the post-filter never has to
  apologise for a missing message.

## References

- [ADR 0004] — sender-rule matcher grammar; this ADR refines the
  `newer_than` / `older_than` rows.
- [ADR 0017] — response transparency; the rounding contract preserves
  the `matched_total` / `matched_visible` invariants because the
  post-filter runs on the IMAP-returned set.
- [ADR 0025] — folder-path contract; cited here because the broader
  surface refactor lands together.
- [ADR 0026] — tool-surface consistency; cited here for the related
  schema-level rejection mechanism.
- RFC 3501 §6.4.4 — IMAP `SEARCH` `SINCE` / `BEFORE` accept a date,
  not a datetime.

[ADR 0004]: 0004-sender-rule-matcher-grammar.md
[ADR 0017]: 0017-response-transparency-for-filtered-data.md
[ADR 0025]: 0025-folder-path-contract-and-error-taxonomy.md
[ADR 0026]: 0026-tool-surface-consistency.md
