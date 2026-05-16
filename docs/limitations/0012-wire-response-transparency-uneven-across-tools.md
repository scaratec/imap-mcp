# LIM 0012: Wire-level response transparency uneven across tools

- **Status:** Proposed
- **Resolution intent:** must-resolve (technical debt)
- **Date proposed:** 2026-05-16
- **Proposed by:** claude (post-fix for create_draft append-rejection
  visibility, commit pending)
- **Related ADRs:** ADR-0002 (visibility levels), ADR-0006 (move/copy
  saga), ADR-0016 (tool surface)
- **Related Guidelines:** BDD Guidelines §4.5 (Antwortverarbeitung
  layer), §1.1 (single source of truth for fachliche Verträge)

## Resolution intent

`must-resolve`. The pattern that was just established for `create_draft`
(surface the IMAP server's tagged NO/BAD reason text as `imap_response`
in the response, differentiate `error_type` into `append_rejected` /
`append_timeout` / `append_failed`) is the right pattern for every
tool that issues an IMAP command capable of failing on the server
side. The remaining tools have not yet adopted it. Each missing
adoption is an operator-visible diagnostic gap and is owed a paydown.

## Context

`create_draft` (server.py:_handle_create_draft) was reported on
2026-05-16 against `gupta@scaratec.com / [Gmail]/Drafts` as silently
returning `{"result": "ERROR", "error_type": "append_failed"}` for
four different RFC822 variants, with no further diagnostic information.
Investigation showed the IMAP server's tagged NO response was
discarded inside `imap_core.append_message` before the handler
returned. The fix (this commit) introduces an `AppendResult`
dataclass, captures the wire-level reason text, classifies the
failure mode via the asyncio transport state, and exposes both via
a new `imap_response` response field plus two new `error_type`
values.

The same diagnostic gap exists, by inspection of the same module,
in every other tool that wraps an IMAP command:

- `fetch_body` (imap_core.fetch_body / handler in server.py):
  returns empty text on parse failure, no surfacing of the server's
  FETCH NO/BAD reason; no distinction between "selected folder
  rejected" and "UID not found".
- `fetch_envelope`, `list_messages`, `search` (imap_core.search_uids
  and downstream): IMAP SEARCH NO is collapsed to an empty result;
  the operator cannot tell "no matches" from "server refused the
  criteria".
- `mark_seen`, `mark_tagged` (imap_core.store_flags): STORE NO is
  swallowed; the response shows `result: "OK"` paths only.
- `move` / `copy` cross-account sagas (saga.py): the saga itself
  classifies failures into rich `error_type` values
  (`target_append_failed`, `target_append_timeout`,
  `target_unreachable`), but does not surface the actual IMAP
  reason text either, only the local classification. The same
  "what did the server actually say" debugging question recurs.
- `create_reply_draft` (server.py:_handle_create_reply_draft): the
  AppendResult adapter was added to this handler in the same commit
  but the new `imap_response` field was deliberately NOT exposed
  there — that change is out of scope for the bug fix and is part
  of the paydown owed by this record.
- Attachment-mutating tools (`add_attachment`, `replace_attachment`,
  `delete_attachment`): each performs an internal FETCH +
  reconstruct + APPEND + delete saga; the APPEND step shares
  `append_message` and thus benefits from the new transparency, but
  the surrounding FETCH and delete steps do not. Partial failure
  diagnosis is still opaque.

## Nature of the weakness

Operators investigating a tool failure cannot distinguish, from the
tool response alone, between:

1. The IMAP server actively rejected the operation with a reason
   text (quota, lock, syntax, ACL, mailbox state).
2. The IMAP server did not respond within the configured timeout.
3. The connection was lost mid-command.
4. The client-side parsing produced an empty or unexpected result.

Only `create_draft` makes this distinction as of this commit. For
every other tool, all four conditions collapse into the same
response shape (typically `result: "ERROR", error_type: <one
catch-all>` or worse, `result: "OK"` with an empty payload field).

This forces the operator to read server-side IMAP logs to diagnose
even trivial failures, which is exactly the workflow this project
was built to remove. The original bug report
(`/home/randy/.config/imap-mcp/audit/2026-05-16.jsonl`) is the
canonical example: four failed `create_draft` calls produced no
information beyond `append_failed`, and the only thing the audit
log could prove was that the OAuth refresh had succeeded
beforehand — the actual rejection reason was invisible until
this fix.

## Why the clean solution is not chosen

Generalising the pattern across all tools in one change has three
substantive costs:

1. **Per-tool response contract additions.** Each tool needs an
   additive response-field schema change. Each schema change needs
   its own BDD feature file specifying the new contract per the
   guidelines (§1.1, §4.5 layer enumeration). The pattern is
   per-tool work; there is no single refactor that buys it for
   everything at once.

2. **Per-tool wire-failure classification.** Each IMAP command has
   its own set of plausible server responses (APPEND has OVERQUOTA,
   SELECT has NONEXISTENT, STORE has READONLY, etc.). The
   `error_type` enum cannot be inherited; each tool needs its own
   classification table negotiated with the spec.

3. **Per-call-site dataclass refactor.** The `AppendResult` shape
   that this fix introduced is the right shape for write
   operations. Read operations need a different shape
   (`FetchResult(outcome, lines, imap_response)`-style). Each
   `imap_core` function plus each handler plus each saga site
   would need analogous changes.

The bug that prompted this fix was driven by a real user report.
Generalising preemptively, without the corresponding user reports
to validate which tools' response contracts matter, risks shipping
schema changes that no one reads and that lock in design choices
that the first real diagnostic need would have rejected. The
project's stated discipline (BDD-first, no spec without a Then-step
that exercises it — Guidelines §1.1, §2.2) argues against
speculative generalisation.

## Mitigations in place

- `create_draft` is the most-used write tool in production and now
  has the new contract; the operational pain that drove this
  limitation is therefore the first paid down.
- The cross-account move saga (`saga.py`) has had a rich local
  classification (`target_append_failed` /
  `target_append_timeout` / `target_unreachable`) since ADR-0006
  landed. The reason text is missing, but the failure-mode
  category is preserved — operators reading saga audit entries
  can already tell timeout from unreachable from rejection.
- The audit log
  (`/home/randy/.config/imap-mcp/audit/<date>.jsonl`) carries
  `result` and `latency_ms` for every tool call. The
  `token_refresh` audit entry sits immediately before each
  failing `create_draft` and lets the operator rule out OAuth
  refresh as the cause (which is how this bug was diagnosed in
  the first place).
- OTEL tracing (ops/tracing/) spans every IMAP command. An
  operator with tracing enabled can see the wire-level command and
  response even when the tool response does not surface it.
- The append-rejection BDD pattern (proxy-injected NO/BAD/close
  modes, `imap_response equals` asserts, list_messages
  second-channel verification) established in
  `create_draft_error_visibility.feature` is reusable wholesale for
  every paydown. Each tool's paydown is therefore a known-shape
  task, not a research project.

## Residual risk

A future bug report against any non-`create_draft` tool will face
the exact diagnostic gap that 2026-05-16's bug report faced. A
realistic example: an agent calling `mark_seen` on a UID that was
silently moved out by another client. The IMAP server returns
`NO STORE: UID not found`. The current implementation returns
`{"result": "ERROR", "error_type": "store_failed"}` (or whatever
the existing catch-all is), and the operator must enable IMAP-side
logging or attach OTEL traces to discover what the server actually
said. The investigation cost for the next such bug report is
expected to be a multiple of what it would be if the tool had
already adopted the pattern.

The risk is bounded: the underlying data (server NO/BAD text) is
not lost, only unexposed. Tracing recovers it; the failure
classification cannot regress beyond "ERROR with opaque type".
There is no security implication — the visibility decision is
already enforced by policy before the IMAP command runs; this
record is about diagnostic surface, not access control.

## Triggers for revisit

This record is paid down per-tool as bug reports arrive. The
following observable events each require expanding the contract
for the named tool and marking the corresponding piece of this
record resolved:

- A bug report against `fetch_body` complaining about silent empty
  results, ambiguous "no body" vs "fetch failed" semantics, or
  visibility-vs-content confusion.
- A bug report against `list_messages` or `search` where the
  caller cannot distinguish "no matches" from "server refused the
  search criteria" (e.g. malformed `subject_contains` with quoting
  edge cases).
- A bug report against any mutating attachment tool
  (`add_attachment`, `replace_attachment`, `delete_attachment`)
  where the partial-failure mode produces an opaque
  `attachment_modify_failed` without indicating which leg of the
  internal saga failed.
- A bug report against `create_reply_draft` analogous to the
  2026-05-16 `create_draft` report (it already uses
  `AppendResult` internally but does not expose `imap_response`).
- A bug report against `mark_seen` or `mark_tagged` complaining
  about silent STORE rejections.
- An operations review concludes that the current audit-log +
  tracing fallback is not sufficient for the operator
  population — e.g. when the project ships to operators who do not
  have access to OTEL backends.

Additionally:

- If three or more such reports accumulate within a 90-day window,
  the per-tool paydown approach must be re-evaluated in favour of
  a single architectural pass — at that point the speculative-spec
  cost is justified by the proven aggregate need.

## References

- The triggering commit (`feat(create_draft): surface IMAP APPEND
  rejection reason in response`, this PR) and its feature file
  `bdd/features/tool_surface/create_draft_error_visibility.feature`.
- ADR-0002 — visibility levels (FULL/BODY/HEADERS/METADATA/ENVELOPE)
  that the response contracts already differentiate; this LIM is
  about adding orthogonal *failure-reason* transparency on top.
- ADR-0006 — cross-account move/copy saga, which has classified
  failure modes but not wire-level reason text.
- ADR-0016 — tool-set documentation; each paydown updates that ADR
  with the new response fields per tool.
- BDD Guidelines §4.5 — the layer enumeration that
  `create_draft_error_visibility.feature` used (5 enumerated /
  5 covered) is the template each per-tool paydown must follow.
- `imap_core.append_message` / `AppendResult` — the shape to mirror
  for analogous read-side and modify-side results.
