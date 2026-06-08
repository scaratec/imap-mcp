# ADR 0026: Tool-Surface Consistency

- **Status:** Accepted
- **Date:** 2026-06-05
- **Deciders:** Randy Nel Gupta

## Context

[ADR 0016] fixed an initial set of 16 tools; the surface has since grown
to 23 by accretion (`bulk_mark_seen`, `list_labels`, `create_reply_draft`,
the three `*_attachment` write tools). Several inconsistencies emerged
along the way that ADR 0016's "one-tool-one-level" framing did not
anticipate:

1. **`fetch_attachment` is overloaded.** Called with `part_id` it returns
   a single attachment blob; called without `part_id` it lists all
   attachments with their metadata. Two operations, one tool name, two
   different visibility profiles (the listing path returns only metadata
   that the BODY level would already disclose; the fetch path requires
   FULL). The current implementation papers over this with a runtime
   branch.

2. **`bulk_mark_seen` exists, `bulk_mark_tagged` does not.** Both are
   write tools driven by the same criteria grammar; both apply a single
   STORE-style operation across a search result set; both have the same
   per-message authorization shape. The asymmetry is an accident of
   incremental landing.

3. **Sub-object schemata are untyped.** `move` and `copy` declare
   `source` and `target` as `{"type":"object"}`; `describe_policy`
   declares `additionalProperties: true`. The MCP layer does no
   structural validation on these, so the handler must either tolerate
   malformed input (it doesn't, today) or crash. The same root cause as
   the duration bug in ADR 0024, on a different argument.

4. **The `from` field is a Python keyword.** `MessageEntry` in
   `handlers/search.py` declares the JSON key `from` as `from_` in the
   TypedDict, with a comment "placeholder; real key is `from`". The
   wire JSON is correct (a manual `dict` build does it right); the type
   annotation is a lie. A future refactor that trusts the TypedDict
   produces wrong code.

5. **Tool descriptions mix contract with guidance.** Some descriptions
   read like documentation ("THE PRIMARY TOOL for reading emails…
   Always call list_accounts first"); some read like a one-line
   summary. The two purposes pull in opposite directions: a contract
   description should be terse and stable; a guidance description
   should be discursive and refresh as user-facing copy improves.

A decision is needed now because the two recent bug reports
(ADR 0024, ADR 0025) make the case for a Hard Cut explicit. The user
has authorized breaking changes since the tool contract is reloaded by
clients each session. Patching individual tools and leaving the
inconsistencies in place wastes the opportunity.

## Decision

We **redefine the V1 tool set** (Hard Cut, tool-set version bumped to
1.0.0 by [ADR 0027]) along five mechanical changes. The tool surface
becomes 25 tools.

### 1. Split `fetch_attachment` into `list_attachments` + `fetch_attachment`

| Tool                | Min visibility | Required args                         | Returns                              |
|---------------------|----------------|---------------------------------------|--------------------------------------|
| `list_attachments`  | `BODY`         | `account`, `folder`, `uid`            | Metadata list: index, filename, mime, size |
| `fetch_attachment`  | `FULL`         | `account`, `folder`, `uid`, `part_id` | Single attachment blob               |

`list_attachments` is BODY-level because the MIME structure is part of
what BODY already discloses (Content-Type, Content-Disposition,
filenames). `fetch_attachment` keeps the FULL requirement for the
bytes themselves. `part_id` is now `required` on `fetch_attachment`;
the runtime branch is gone.

### 2. Add `bulk_mark_tagged`

| Tool                | Required capability | Required args                                         |
|---------------------|---------------------|-------------------------------------------------------|
| `bulk_mark_tagged`  | `mark_tagged`       | `account`, `folder`, `criteria`, `tags`, `mode`       |

Same criteria grammar as `search`; same `mode` values (`add`, `remove`,
`replace`) as `mark_tagged`. Returns `tagged_count` like
`bulk_mark_seen` returns `marked_count`. A `bulk_move` is **not** added;
saga semantics over a list are a non-trivial design decision and are
deferred ([LIM-0011]).

### 3. Type the sub-object schemata

`source` and `target` on `move` and `copy`:

```json
"source": {
  "type": "object",
  "properties": {
    "account": {"type": "string"},
    "folder":  {"type": "string"},
    "uid":     {"type": "integer", "minimum": 1}
  },
  "required": ["account", "folder", "uid"],
  "additionalProperties": false
}

"target": {
  "type": "object",
  "properties": {
    "account": {"type": "string"},
    "folder":  {"type": "string"}
  },
  "required": ["account", "folder"],
  "additionalProperties": false
}
```

`describe_policy` switches from `additionalProperties: true` to
`additionalProperties: false`; it took no arguments before, this
formalizes that.

The `criteria` object across `search`, `list_messages`,
`bulk_mark_seen`, and the new `bulk_mark_tagged` becomes a single
shared schema with one `properties` entry per predicate from
[ADR 0004], plus the duration pattern from [ADR 0024]:

```json
"criteria": {
  "type": "object",
  "properties": {
    "from":             {"type": "string"},
    "from_domain":      {"type": "string"},
    "to":               {"type": "string"},
    "to_contains":      {"type": "string"},
    "subject_contains": {"type": "string"},
    "has_attachment":   {"type": "boolean"},
    "flagged":          {"type": "boolean"},
    "newer_than":       {"type": "string", "pattern": "^[0-9]+[smhdwy]$"},
    "older_than":       {"type": "string", "pattern": "^[0-9]+[smhdwy]$"},
    "size_gt":          {"type": "integer", "minimum": 1},
    "size_lt":          {"type": "integer", "minimum": 1}
  },
  "additionalProperties": false
}
```

Empty `criteria` (`{}`) remains valid and triggers the default 7-day
scope per [ADR 0017].

### 4. Drop the `from_` placeholder

The Python `MessageEntry` type is reformed as a
`TypedDict("MessageEntry", { "from": str, ... })` using the functional
form (which accepts Python-keyword keys). The JSON wire format is
unchanged. The lie in the TypedDict goes away.

### 5. Explicit `scope` argument on list/search/bulk tools

Today the server silently applies a 7-day `SINCE` filter when `criteria`
is empty, and lifts it when `criteria` is non-empty. The behaviour is
documented in [ADR 0017] and exposed transparently via the
`default_scope: "newer_than_7d"` response field, but the *caller* has no
way to ask for "all messages, regardless of date" without inventing a
predicate that matches everything (`size_gt: 0` is the current trick).
The implicit toggle is also asymmetric: an empty-criteria default that
goes away once any predicate is added is surprising.

A new `scope` argument is added to `search`, `list_messages`,
`bulk_mark_seen`, and `bulk_mark_tagged`:

```json
"scope": {
  "type": "string",
  "enum": ["recent", "all"],
  "default": "recent"
}
```

Semantics:

- `scope: "recent"` (default) — preserves today's behaviour. If the
  caller did not pass an explicit `newer_than` / `older_than`
  predicate, the server applies a 7-day `SINCE` window. If they did,
  the explicit predicate wins.
- `scope: "all"` — disables the implicit window entirely. The caller
  is asking for the whole folder (subject to other `criteria` and to
  policy filtering). `newer_than` / `older_than` predicates still
  apply if present.

The response field `default_scope` is renamed to `applied_scope` and
becomes **always present**, drawn from a closed enumeration:

| `applied_scope` value | Meaning                                                                  |
|-----------------------|--------------------------------------------------------------------------|
| `"recent_7d"`         | Implicit 7-day SINCE was applied (caller used `scope: "recent"` with no time predicate). |
| `"explicit_window"`   | Caller supplied `newer_than` / `older_than` directly.                    |
| `"all_time"`          | No time filter at the IMAP layer (caller used `scope: "all"`).           |

A caller reading `applied_scope` knows exactly what time window was
in force, every time, without inferring from input. The transparency
contract of [ADR 0017] is preserved and slightly tightened (the field
is no longer conditional).

The typical "show me everything in this folder" request becomes:

```json
list_messages(account="...", folder="INBOX", scope="all")
```

The typical "all my starred mail, any date" request becomes:

```json
list_messages(account="...", folder="INBOX",
              criteria={"flagged": true}, scope="all")
```

### 6. Description discipline

Every tool description becomes:
- One short noun-phrase line stating *what* the tool does.
- A reference to the governing ADR.

Guidance text (when to call, prerequisites, troubleshooting) moves to
`describe_policy` output where it is already programmatically
introspectable, or to user-facing documentation (`README.md`,
`docs/`). The MCP `description` field is a contract surface, not a
user manual.

### Final tool set (26 tools)

| Group | Count | Tools |
|-------|-------|-------|
| Read  | 11    | `list_accounts`, `list_folders`, `list_labels`, `folder_stats`, `search`, `list_messages`, `fetch_envelope`, `fetch_headers`, `fetch_body`, `list_attachments`, `fetch_attachment` |
| Write | 11    | `mark_seen`, `bulk_mark_seen`, `mark_tagged`, `bulk_mark_tagged`, `move`, `copy`, `create_draft`, `create_reply_draft`, `add_attachment`, `replace_attachment`, `delete_attachment` |
| Meta  | 4     | `describe_policy`, `get_caller_identity`, `get_transaction_status`, `tool_surface_info` ([ADR 0027]) |

Test-only entries (`_test_run_recovery`, `_test_run_audit_rotation`)
remain gated on `TestHooks.test_mode` and are not counted as part of
the surface.

## Consequences

### Positive

- **`fetch_attachment`'s authorization story is honest.** A caller that
  is granted BODY but not FULL can list attachments and learn that
  attachments exist (Content-Type metadata is already BODY-disclosable);
  it cannot retrieve their bytes. Today the same caller gets a DENY on
  any `fetch_attachment` call and cannot even tell whether attachments
  are present.
- **`bulk_mark_tagged` closes the symmetry gap.** A caller that wanted
  to "mark all alerts as tagged" had to loop in their own code; the
  bulk variant is now a single call.
- **Schema-level rejection across the board.** Every malformed
  `criteria`, `source`, or `target` is refused by the JSON-RPC layer
  before reaching the handler. The bugs in this round disappear; the
  *class* of bug disappears.
- **The `from_` lie is gone.** Future TypedDict-aware refactors trust
  the type.
- **Descriptions stop drifting.** A short noun phrase pointing at an
  ADR has nowhere to drift to.

### Negative

- **Hard cut.** Existing callers break. The version bump to 1.0.0
  ([ADR 0027]) signals this. Migration cost is borne once.
- **More tools (26 vs 23).** Every additional tool is one more entry
  in `list_tools` and one more line in the dispatch table. We judge
  the clarity gain (one operation per tool) to outweigh the surface
  growth.
- **`list_attachments` looks redundant.** A caller could in principle
  call `fetch_body` and parse the MIME tree itself. We keep the
  dedicated tool because (a) it has a different authorization profile
  (BODY vs whatever fetch_body returns under the caller's grant) and
  (b) parsing MIME on the caller side is exactly the kind of thing
  the server-side surface should obviate.

### Neutral

- The tool-set version bumps to 1.0.0 with this ADR + [ADR 0027].
  Subsequent additive changes (a new tool) bump minor; further
  breaking changes bump major.

## Security Implications

- **Attack surface.** Strict `additionalProperties: false` on every
  argument object closes the smuggling vector where a caller could
  pass unrecognized fields hoping a future handler version would
  honour them. The `criteria` schema's predicate enumeration limits
  the search-side attack surface to the [ADR 0004] grammar.
- **Trust boundaries.** Unchanged. The split of `fetch_attachment`
  does not move the FULL boundary; it makes the BODY/FULL split
  visible in the tool surface instead of inside the handler.
- **Data exposure.** `list_attachments` discloses filenames and
  Content-Types at BODY level. This is intentional and matches what
  `fetch_body` already returns for inline-rendered messages; the
  metadata was never the secret. The bytes remain FULL-gated.
- **Failure modes.** Schema-rejects surface as `-32602 Invalid params`,
  same as the existing unknown-tool path. The audit log records the
  attempt under the existing `unknown_tool`-style channel.
- **Auditability.** `bulk_mark_tagged` adds an audit record per
  invocation with a `search_query_digest` (same shape as
  `bulk_mark_seen`); the per-message decisions remain operator-
  forensic via existing `mark_tagged` audit records.

## Alternatives Considered

- **Keep `fetch_attachment` overloaded; document the two modes.**
  Rejected: the visibility-level mismatch makes "document it" insufficient.
  A reviewer reading the tool surface should be able to tell the
  authorization model from the tool name and shape; an overloaded
  tool hides it.
- **Split only `fetch_attachment`, skip the rest of the consistency
  fixes.** Rejected: every inconsistency identified above is small in
  isolation, but together they compound into a surface that requires
  reading multiple modules to understand. One Hard Cut now is cheaper
  than four small breaking changes later.
- **Add `bulk_move` alongside `bulk_mark_tagged`.** Rejected for V1:
  saga semantics applied to a list (atomic? per-message? skip on
  partial failure?) need a design decision of their own. Recorded
  in [LIM-0011] and revisited when a real caller asks for it.
- **Allow `additionalProperties: true` for forward-compat ("agents
  may pass extra hints").** Rejected: forward-compat via silent
  acceptance is a footgun. A future field that means something is
  better signalled by a version bump than by silent acceptance of
  unknown keys.
- **Keep `from_` and call it a "Python-side type annotation
  detail".** Rejected: a TypedDict whose Python attribute name does
  not match its JSON wire key is a lie that costs future
  refactors. The functional `TypedDict(name, {"from": str})` form
  is the right fix.
- **Make tool descriptions all-discursive (user-manual style).**
  Rejected: descriptions are part of the JSON-RPC contract. They
  must be tight enough that an agent's prompt-budget is not
  consumed by them. User-manual content goes in `docs/`.
- **For "show me everything", keep the implicit toggle (empty
  `criteria` = 7d, any predicate = all-time) and document it.**
  Rejected: the behaviour is surprising in both directions. A
  caller that adds `subject_contains: "x"` to a "last 7 days"
  query silently widens the window to all time; a caller that
  wants the whole folder must invent a no-op predicate. The
  caller's intent ("recent" vs "all") deserves a first-class
  argument, not an inference from the shape of `criteria`.
- **Add a sentinel predicate `criteria: {"all_time": true}` for
  the "show me everything" case.** Rejected: pollutes the
  `criteria` grammar with a non-predicate. Every other key in
  `criteria` is a per-message filter; `all_time` would be a
  meta-toggle. Mixing the two axes makes the schema harder to
  reason about (does `{all_time: true, from: "x@y"}` mean
  anything different from `{from: "x@y"}` alone?) and breaks the
  one-grammar-one-meaning principle inherited from [ADR 0004].
- **Make `scope: "all"` the default and require `scope: "recent"`
  opt-in.** Rejected: the implicit 7-day default exists for a
  reason (LLM caller hits "list_messages on INBOX" with no
  thought and gets back a manageable result, not 50 000 UIDs).
  Flipping the default would regress that protection on every
  caller that didn't read the changelog.

## References

- [ADR 0004] — predicate grammar that `criteria` reflects.
- [ADR 0016] — superseded by this ADR for the tool-list section.
- [ADR 0017] — transparency contract still applies tool-for-tool;
  also superseded by [ADR 0027] for the response-envelope section.
- [ADR 0024] — duration grammar embedded in `criteria.newer_than` /
  `older_than`.
- [ADR 0025] — folder-path contract that every `folder`-typed
  argument now follows.
- [ADR 0027] — error envelope, `tool_surface_info` meta-tool, and
  the version bump to 1.0.0.
- [LIM-0011] — `bulk_move` deferred.

[ADR 0004]: 0004-sender-rule-matcher-grammar.md
[ADR 0016]: 0016-mcp-tool-set.md
[ADR 0017]: 0017-response-transparency-for-filtered-data.md
[ADR 0024]: 0024-duration-grammar-single-source.md
[ADR 0025]: 0025-folder-path-contract-and-error-taxonomy.md
[ADR 0027]: 0027-error-envelope-and-tool-surface-versioning.md
[LIM-0011]: ../limitations/0011-bulk-move-deferred.md
