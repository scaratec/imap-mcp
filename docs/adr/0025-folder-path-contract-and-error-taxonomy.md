# ADR 0025: Folder-Path Contract and Error Taxonomy

- **Status:** Accepted
- **Date:** 2026-06-05
- **Deciders:** Randy Nel Gupta

## Context

Folder paths appear in every read and write tool as the `folder` argument
(plus `source_folder`, `drafts_folder`, the `folder` inside
`source`/`target` for move/copy). The contract around that argument has
three implicit assumptions that were never written down, and the
production server breaks all three.

**Assumption 1 — Path encoding.** Callers are expected to pass paths in
the same form `list_folders` / `list_labels` returns them. For Google
accounts those are canonical English paths (`[Gmail]/Drafts`), even
when the IMAP server has localized them (`[Gmail]/Entwürfe`). The
server's `_resolve_imap_folder` helper does the canonical→IMAP mapping
internally, but this resolution behaviour is not part of the published
contract. A caller that constructs a path itself (e.g. building
`INBOX/Sent`) cannot tell whether the canonical or the localized form
will be accepted.

**Assumption 2 — IMAP wire quoting.** Mailbox names with spaces,
brackets, or other special characters must be quoted on the IMAP wire
per RFC 3501 §4.3. The server applies `encode_mutf7` to handle non-ASCII
characters, and quotes the result for `STATUS` in one place
(`imap_core.py::_folder_message_count`), but the **18 other `imap.select`
call-sites pass the bare encoded name**. A folder named
`INBOX/BuHa - privat offene Rechnungen` (ASCII, no `&`) survives
`encode_mutf7` unchanged, and the resulting wire command
`SELECT INBOX/BuHa - privat offene Rechnungen` is rejected by the IMAP
server because the unquoted spaces are interpreted as argument
separators. The handler reads the resulting `BAD`/`NO` status, fails
silently, and surfaces `folder_not_found` — even though the folder
exists and the caller is authorized to see it.

**Assumption 3 — One error means one thing.** The current vocabulary
collapses three distinct failure modes into a single
`folder_not_found`:

- Policy refusal (folder is hidden by policy) — bug-free but
  indistinguishable from the next two.
- The folder genuinely does not exist on the IMAP server (caller typo,
  folder was deleted).
- The folder exists, the caller is authorized, but the `SELECT`
  command failed for a wire-protocol reason (the quoting bug above,
  or a transient IMAP server fault).

A caller cannot tell which case it is in. Operator triage requires
reading the server logs.

A decision is needed now because (a) the quoting bug is user-visible
and blocks real folders, (b) the path-encoding contract must be
formalized before the broader Tool-Surface refactor (ADR 0026) lands,
and (c) every new tool added by ADR 0026 inherits whatever taxonomy
this ADR fixes.

## Decision

We define a **folder-path contract** with three components: canonical
paths in tool arguments, a single mailbox-quoting helper at the IMAP
boundary, and a three-code error taxonomy that replaces
`folder_not_found`.

### 1. Canonical paths

Every tool argument that names a folder takes the **canonical path**,
defined as a path that appears in `list_folders` / `list_labels` output
for that account. The server resolves canonical → wire (`_resolve_imap_folder`)
inside the handler before any IMAP call.

- For non-Google accounts, canonical and wire are identical; this is a
  no-op.
- For Google accounts with localized names, the alias map produced by
  `list_folders` (via `build_folder_alias_map`) maps `[Gmail]/Drafts`
  → `[Gmail]/Entwürfe`.

Callers MUST NOT construct paths from external knowledge of an
account's IMAP layout. The contract is: ask `list_folders` first, use
what it returns verbatim. `list_folders` is the schema for the
`folder` argument.

### 2. Single mailbox-quoting helper

We introduce **one** function `imap_core._quote_mailbox(name: str) -> str`.
It combines Modified UTF-7 encoding and IMAP quoted-string framing in
one step:

```
_quote_mailbox(name):
    encoded = encode_mutf7(name)
    return '"' + encoded.replace('\\', '\\\\').replace('"', '\\"') + '"'
```

Every IMAP command that takes a mailbox name uses this helper. The
existing 19 `imap.select(folder)` sites are all converted; the existing
`_folder_message_count` that already quotes for `STATUS` is converted to
use the helper; the Gmail label-swap path in `imap_core.py` (which
currently calls `encode_mutf7` directly) is converted; new code in the
post-refactor handlers (ADR 0026) uses the helper from day one.

The helper is the single place where IMAP wire encoding lives.
`encode_mutf7` becomes a private implementation detail; nothing outside
`imap_core.py` calls it.

### 3. Three-code error taxonomy

`folder_not_found` is **removed** from the canonical reason-code table.
It is replaced by three codes, each with a single emission condition:

| Code            | Decision | Trigger                                                                                  |
|-----------------|----------|------------------------------------------------------------------------------------------|
| `folder_hidden` | DENY     | The PDP refuses the call because the caller has no policy entry for this folder.         |
| `folder_absent` | ALLOW + ERROR | After PDP allows, an IMAP `LIST` probe confirms the folder does not exist on the server. |
| `select_failed` | ALLOW + ERROR | After PDP allows and `LIST` confirms presence, `SELECT` returns `BAD` or `NO`.         |

`folder_hidden` remains a pure DENY (policy refusal): the caller is told
*that* a folder by this name is out of scope, never *whether* it
exists. This preserves the "policy leaks nothing about non-policy
folders" invariant of [ADR 0017].

`folder_absent` and `select_failed` are post-authorization errors,
returned with `{"decision":"ALLOW", "result":"ERROR", "error": {...}}`
under the envelope of [ADR 0027]. `select_failed` carries the raw IMAP
response status in `error.detail` so an operator can triage from the
audit log without retrieving server-side traces.

The handler order for any tool that opens a folder is:
1. PDP `decide_folder_access` — DENY → `folder_hidden`.
2. Resolve canonical → wire path.
3. Issue `LIST` probe for the wire path — empty → `folder_absent`.
4. Issue `SELECT` — non-`OK` → `select_failed` with `error.detail` =
   raw IMAP response.
5. Proceed with the tool-specific operation.

Steps 3 and 4 are kept separate intentionally; collapsing them
(treating `SELECT BAD` as "doesn't exist") is exactly the bug this
ADR closes.

## Consequences

### Positive

- **The quoting bug is structurally impossible.** No code path in
  `imap_core.py` can construct a wire mailbox name without going
  through `_quote_mailbox`. Adding a new IMAP command that takes a
  mailbox name is a single import.
- **Operator triage is fast.** `folder_absent` vs `select_failed` is
  the difference between "fix your spelling" and "investigate the
  IMAP server"; the audit log shows which one without server-side
  log diving.
- **The contract is callable.** A caller that uses `list_folders`
  output verbatim is guaranteed correct path syntax. There is no
  "did I localize the name correctly?" doubt.
- **Information disclosure stays bounded.** `folder_hidden` still
  reveals nothing about server-side state; only the two
  post-authorization codes can disclose existence, and only to
  callers who already have a policy grant for the folder.

### Negative

- **One extra IMAP round-trip on every folder-opening tool call.**
  The `LIST` probe before `SELECT` is new. We accept the cost — it
  is a single command, response is small, and the alternative is the
  conflation that produced this bug. If profiling shows it as a
  hot path, we can cache `LIST` results per session.
- **Existing callers that constructed paths themselves break.** Any
  caller relying on `[Gmail]/Entwürfe` being accepted directly must
  switch to the canonical `[Gmail]/Drafts`. Hard cut, documented as
  part of the 2.0.0 surface bump (ADR 0027).
- **`folder_not_found` disappears from the audit log.** Tooling that
  greps audit records for that string must be updated. The
  reason-code table is the contract; we update the table and any
  consumer mirrors it.

### Neutral

- The `_resolve_imap_folder` helper continues to exist; this ADR only
  formalizes what it was already doing. Its absence on non-Google
  accounts (a no-op pass-through) becomes part of the contract.

## Security Implications

- **Attack surface.** The `_quote_mailbox` helper centralizes a
  parsing-adjacent operation; a bug here would affect every IMAP
  command, but the existing per-site bug already affected every
  command's correctness — centralizing makes the bug visible and
  fixable in one place. The quoting itself escapes `"` and `\` to
  prevent any caller-controlled string from breaking out of the
  quoted-string frame on the IMAP wire (an IMAP-side injection
  primitive that the bare-name code did not defend against either,
  it just happened to fail differently).
- **Trust boundaries.** Unchanged. The canonical-path contract moves
  the canonical→wire translation across the same internal boundary
  it already used; no new component sees more data than before.
- **Data exposure.** `select_failed` returns an `error.detail`
  containing the raw IMAP status line. The IMAP status line can
  reveal the existence of the folder (e.g. "Mailbox does not exist"
  vs "Permission denied"); we guard this by emitting `folder_absent`
  before `select_failed` ever runs, so the detail is only returned
  when LIST has already confirmed presence to a caller who already
  has policy authorization. The detail is bounded to a single status
  string; full server logs remain operator-only.
- **Failure modes.** A transient IMAP-server fault surfaces as
  `select_failed`; the caller can retry. The previous behaviour
  surfaced it as `folder_not_found` and the caller was forced to
  assume permanent absence.
- **Auditability.** Every code is recorded with the same `reason`
  field as today. The new codes appear in the audit log with the
  same shape; the audit-log schema (ADR 0021) is additive-only.

## Alternatives Considered

- **Keep `folder_not_found`, just fix the quoting.** Rejected: the
  taxonomy ambiguity remains. An operator looking at the audit log
  cannot distinguish caller-typo from server-fault, and the next
  IMAP edge case (a future transient failure mode) will land under
  the same useless name.
- **Add `_quote_mailbox` but skip the `LIST` probe; treat any
  non-`OK` `SELECT` as `select_failed`.** Rejected: the LIST probe
  is what lets us return `folder_absent` honestly. Without it, a
  caller's typo is indistinguishable from an IMAP-server fault.
- **Have the schema validate folder paths against a known-set
  enumeration.** Rejected: `list_folders` output is dynamic
  (depends on the account's mailbox layout), and JSON-Schema's
  enum cannot reflect server-side state. The contract is "use what
  `list_folders` returned", verifiable at runtime, not in the
  schema.
- **Make `_resolve_imap_folder` raise on unknown canonical paths.**
  Rejected: a caller passing a canonical path that the alias map
  doesn't know about could be (a) an unrecognized canonical or (b)
  a wire path that happens to match. We let it through and let the
  `LIST` probe make the existence decision uniformly.
- **Treat canonical/localized as caller's choice; accept both.**
  Rejected: ambiguity is the source of the original bug. One
  contract surface, one resolution path.

## References

- [ADR 0016] — tool set; this ADR refines the `folder` argument
  semantics of every tool listed there.
- [ADR 0017] — reason-code table; the `folder_hidden` row stands,
  `folder_not_found` is removed, `folder_absent` and `select_failed`
  are added. ADR 0017 itself is superseded by ADR 0027 with the
  table update bundled in.
- [ADR 0019] — Gmail label semantics; the localized-path case is the
  most common reason a canonical-path contract is needed.
- [ADR 0021] — audit-log format; the new reason codes are
  additive-only against the existing schema.
- [ADR 0027] — error envelope; defines the shape that the new
  ERROR codes are returned in.
- RFC 3501 §4.3 — IMAP quoted strings and literals.
- RFC 3501 §5.1.1 — mailbox naming, including the requirement that
  names with special characters be quoted on the wire.

[ADR 0016]: 0016-mcp-tool-set.md
[ADR 0017]: 0017-response-transparency-for-filtered-data.md
[ADR 0019]: 0019-gmail-label-semantics.md
[ADR 0021]: 0021-audit-log-format.md
[ADR 0027]: 0027-error-envelope-and-tool-surface-versioning.md
