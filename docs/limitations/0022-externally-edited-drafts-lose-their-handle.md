# LIM 0022: Externally edited drafts lose their handle

- **Status:** Proposed
- **Resolution intent:** permanent (architectural boundary)
- **Date proposed:** 2026-09-18
- **Date approved:** —
- **Proposed by:** the agent
- **Approved by:** — (pending)
- **Related ADRs:** [ADR-0030](../adr/0030-draft-identity-and-revisions.md), [ADR-0031](../adr/0031-draft-revision-via-rewrite-saga.md)
- **Related Guidelines:** BDD Guidelines §4.2, §4.5

## Resolution intent

`permanent`. The limitation follows from the fact that IMAP has no
in-place message modification and no standard for message identity
across an edit. Every mail client that edits a draft composes a new
message and appends it; whether it carries over unknown headers from
the version it started with is entirely that client's decision, and no
protocol mechanism obliges it to. The project cannot close the gap by
building something, only by detecting and reporting it — which it does.

## Context

[ADR 0030] identifies a draft by an `X-SC-Draft-Id` header that the
server injects when it creates the draft, plus an `X-SC-Draft-Rev`
counter. Resolving a handle is a header search in one folder. This is
what lets a caller revise a draft repeatedly without ever seeing a UID.

The same drafts folder is normally also visible to a human, through
Thunderbird, a webmailer, or a phone. Those clients edit drafts too.
When they do, they do not `STORE` a change onto the existing message —
IMAP does not offer that. They append a message they have composed
themselves and delete the previous one.

## Nature of the weakness

If a draft created through `create_draft` is subsequently edited by any
client other than this server, the message that ends up in the folder
is one the other client composed. Two outcomes are possible, and which
one occurs depends on the foreign client, not on us:

- The client preserves unknown headers. The `X-SC-Draft-Id` survives,
  but `X-SC-Draft-Rev` is now stale: it still names the revision the
  human started from, while the content is newer. A subsequent
  `update_draft` with the matching `base_revision` will be accepted and
  will silently overwrite the human's edit.
- The client drops unknown headers. The handle resolves to nothing and
  every subsequent `update_draft`, `get_draft` and revision of that
  draft fails with `draft_not_found`, even though a draft with the same
  subject is plainly visible in the folder.

The second case is loud and correct. The first case is a lost update
that the optimistic revision check cannot catch, because the check
compares revision numbers and the foreign client did not touch the
number.

## Why the clean solution is not chosen

There is no clean solution available inside the protocol:

- **Detecting content change independently of the counter** would
  require a content hash over the previous revision, stored where the
  foreign client cannot invalidate it. Any such witness lives either in
  the message (where the foreign client rewrites or drops it) or in a
  local store (which [ADR 0030] rejects, and which cannot distinguish
  "foreign edit" from "our own append" after a crash without the very
  in-message marker that was just lost).
- **`CONDSTORE`/`MODSEQ`** tracks flag and message changes per mailbox,
  not identity across an append-and-delete pair. A foreign edit appears
  as an unrelated new message with a new `MODSEQ`; nothing links it to
  the message it replaced.
- **Obliging foreign clients to preserve the headers** is not something
  the project can do. Header preservation on draft re-composition is
  unspecified in RFC 5322 and RFC 6154 and varies by client.

The residual exposure is also bounded by deployment shape: the
recommended drafts policy ([ADR 0005]) isolates the agent's drafts
folder from the human's, in which case the two never touch the same
message.

## Mitigations in place

- **Loud failure in the common case.** A dropped handle yields
  `draft_not_found` on the next call rather than a silent no-op; the
  scenario is pinned in
  `bdd/features/tool_surface/update_draft_errors.feature`.
- **Foreign drafts are visibly marked.** `list_drafts` reports every
  message without an `X-SC-Draft-Id` as `editable: false` ([ADR 0031]),
  so a client can tell before it tries.
- **`draft_not_editable` is a distinct error type**, separate from
  `draft_not_found`, so "someone else owns this draft" and "this handle
  is dead" are distinguishable by the caller.
- **Every revision is a WAL transaction** with a `tx_id` and an audit
  record ([ADR 0021]), so an overwritten human edit is reconstructible
  after the fact from the audit log, even though it cannot be prevented.
- **Policy-level separation.** The drafts-write-isolation pattern from
  [ADR 0005] gives the agent a folder no human edits, which removes the
  exposure entirely where it is applied.

## Residual risk

A human opens the agent's draft in Thunderbird, rewrites the second
paragraph, and saves. Thunderbird preserves the `X-SC-Draft-Id` header
and the now-stale `X-SC-Draft-Rev: 3`. The agent, which has been
holding `draft_id` and `revision: 3` since before the human's edit,
calls `update_draft` with `base_revision: 3` to change the subject. The
revision check passes, the saga rewrites the message from the version
it fetched, and the human's paragraph is gone. Revision 4 exists,
nobody is notified, and the only trace is that the audit log shows an
edit at a time after the human's save. If the draft is then sent, the
human's correction never leaves the building.

## Triggers for revisit

- If a draft-editing workflow is deployed in which agent and human
  demonstrably share one drafts folder, rather than the isolated folder
  pattern of [ADR 0005].
- If an incident report attributes a lost edit to this record.
- If a future ADR introduces a server-side content witness for drafts
  (for example a per-revision hash checked before the rewrite), which
  would downgrade the first outcome from silent to detected.
- If a standard for stable message identity across an edit is adopted
  by common clients.

## References

- [ADR 0030](../adr/0030-draft-identity-and-revisions.md) — the in-message handle.
- [ADR 0031](../adr/0031-draft-revision-via-rewrite-saga.md) — the revision saga and the error types.
- [ADR 0005](../adr/0005-per-folder-write-capabilities.md) — drafts write isolation.
- [ADR 0021](../adr/0021-audit-log-format.md) — audit record that makes the loss reconstructible.
- RFC 5322 — message format; no provision for identity across an edit.
- RFC 7162 — `CONDSTORE`/`QRESYNC`, evaluated and insufficient here.
