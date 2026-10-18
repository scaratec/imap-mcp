# ADR 0030: Draft Identity via In-Message Handle and Revision Counter

- **Status:** Proposed
- **Date:** 2026-09-18
- **Deciders:** Randy Gupta

## Context

A client that composes mail through this server needs to revise a draft repeatedly: write a first version, read it back, change the subject, add a paragraph, change a recipient. IMAP offers no mechanism for this. Message content is immutable; the only in-place mutation is `STORE` on flags. Every content change is an `APPEND` of a new message plus the removal of the old one, and every such cycle assigns a new UID.

Today the client cannot even start that loop. `create_draft` returns `{decision, result, account, folder}` and nothing else — no UID, no identifier of any kind. The caller has just written a message it has no way to name afterwards. Even if the UID were returned, it would be the wrong handle: it changes with every revision, it is invalidated by a `UIDVALIDITY` change, and it is exactly the protocol detail this server exists to hide ([ADR 0016] describes the tool set as an intent-level surface, not an IMAP shell).

Three further facts constrain the choice of handle:

- The server evaluates no `UIDPLUS` capability anywhere. `APPENDUID` is mentioned as a fast path in [ADR 0006] and [ADR 0008] but is not implemented, and `docs/error_path_analysis.md` records "server supports only plain (no UIDPLUS)" as a deferred path. A design that requires `APPENDUID` would narrow the set of usable IMAP servers.
- The existing rewrite saga locates the newly appended message with `SEARCH HEADER Message-Id` and then picks the first hit that is not the source UID. With several revisions of one logical draft in the same folder, that heuristic becomes ambiguous.
- A drafts folder is commonly configured `visibility: NONE, draft_append: true` ([ADR 0005], "drafts write isolation"). The handle mechanism must not require the caller to be able to read the folder.

## Decision

**A draft is identified by a stable identifier that travels inside the message, not by its UID.**

The server injects two headers into every draft it creates, and sets the `\Draft` and `\Seen` flags on the `APPEND`:

```text
X-SC-Draft-Id:  <uuid4, lowercase hex with dashes>   stable across all revisions
X-SC-Draft-Rev: <integer >= 1>                       incremented on every revision
```

`create_draft` and `create_reply_draft` return `draft_id` and `revision: 1` alongside their existing fields. Resolving a handle is `UID SEARCH HEADER "X-SC-Draft-Id" "<id>"` within the single folder named by the caller.

The mailbox is the only store of this mapping. There is no local draft index.

Three rules follow from the counter:

1. **Invariant.** At most one live message per `(account, folder, draft_id)`. The live one is the message with the highest `X-SC-Draft-Rev`.
2. **Forward resolution.** If resolution finds several messages for one `draft_id`, the highest revision is authoritative and the lower ones are residue of an interrupted revision. Residue is removed by the resuming transaction, or — if no transaction survives to resume — by the next `update_draft` on that handle, which holds the capability to delete. Reads never delete: `get_draft` and `list_drafts` report the highest revision and leave the mailbox untouched.
3. **Ambiguity is an incident.** Two messages carrying the *same* `draft_id` *and* the same `X-SC-Draft-Rev` cannot be ordered. This is not resolved by guessing; the transaction is escalated (`needs_operator`, [ADR 0007]) and the caller receives `draft_ambiguous`.

Each revision carries a fresh `Message-ID`. Draft identity is the `X-SC-Draft-Id` header; the `Message-ID` remains what [ADR 0008] uses it for — the per-transaction idempotency key. Keeping it stable across revisions would make the saga's idempotency search ambiguous for exactly the case this ADR is built for.

A message in a drafts folder without an `X-SC-Draft-Id` header — deposited by Thunderbird, by a webmailer, or by an earlier version of this server — is a draft that this server did not create. It is listed, it is readable under the folder's visibility, and it is not editable. `list_drafts` reports it with `editable: false`.

## Consequences

### Positive

- **The client never sees a UID.** Create, read, revise, and revise again all address the same opaque string. The `APPEND`/`EXPUNGE` cycle underneath is invisible.
- **No local state to desynchronize.** Server restart, policy reload, a second MCP instance against the same mailbox, or an operator moving the mailbox all leave the handle intact, because the handle is in the message.
- **No `UIDPLUS` requirement.** `SEARCH HEADER` is RFC 3501 baseline. The mechanism works against every server the rest of the tool set works against.
- **The rewrite saga gets an exact search key.** Locating the new message by `HEADER "X-SC-Draft-Rev" "<n+1>"` within one `draft_id` is unambiguous, replacing the "first hit that is not the source UID" heuristic on this path.
- **Interrupted revisions are self-describing.** The set of messages sharing a `draft_id` is totally ordered by revision, so the correct state after any crash is computable from the mailbox alone, without reading the WAL.
- **Works under drafts write isolation.** Resolution is a `SEARCH`, performed by the server inside the transaction. It does not require the caller to hold read visibility on the folder.

### Negative

- **The headers travel with the message if it is sent.** This server has no send path, but a client that hands the last revision to an SMTP path unchanged will emit `X-SC-Draft-Id` and `X-SC-Draft-Rev` to the recipient. The values are opaque random identifiers, but they are internal artifacts appearing on the wire.
- **Drafts written by other clients cannot be revised.** A draft that a human edits in a mail client is re-composed by that client, which does not preserve our headers. The handle then resolves to nothing. This is recorded as [LIM 0022].
- **`SEARCH HEADER` is a full-text header scan on some servers.** In a drafts folder — bounded by how many drafts a human or agent keeps open — the cost is negligible. In a folder with 100k messages it would not be.
- **Two non-standard headers in every draft the server creates.**

### Neutral

- `\Draft` and `\Seen` are now set on append, which the current `create_draft` does not do. Clients that display drafts folders will show the message as a draft rather than as unread incoming mail. This is a correction, not a feature of this ADR.
- The identifier is generated server-side. A caller cannot choose it, and cannot use it to encode anything.

## Security Implications

- **Attack surface.** One new header-based `SEARCH`, scoped to the folder the caller already named and already passed the policy check for. No new endpoint, no new credential, no new data flow.
- **The handle is not a capability.** Knowing a `draft_id` grants nothing. Every resolution is preceded by the ordinary `decide_folder_access` check plus the write capability check; a caller that guesses a valid identifier for a folder it has no grant on is denied before the search runs. Identifiers are `uuid4`, so guessing is not a practical path anyway.
- **Folder-scoped resolution.** The search runs in exactly one folder, the one in the call. A handle cannot be used to reach into another folder or another account, so it cannot be used to enumerate the mailbox.
- **Data exposure.** The headers expose no caller identity, no account, no policy information — only a random identifier and a counter. The exposure discussed under Consequences is limited to recipients of a message that a *client* chooses to send.
- **Failure modes.** A partially completed revision leaves two messages with the same `draft_id`; the highest revision wins deterministically, so there is no state in which a caller silently reads or overwrites an outdated version. The one unorderable case — identical `draft_id` and identical revision — is escalated rather than guessed.
- **Auditability.** `draft_id` and `revision` are ordinary response fields and are therefore in the audit record of every draft tool call, giving a complete per-draft history in the audit log without correlating UIDs that no longer exist.
- **Prompt-injection posture is preserved.** The drafts-write-isolation pattern from [ADR 0005] survives: under `visibility: NONE` a caller can still revise, because revision is field-wise and the old content is read only inside the server. The caller never gets the content of a draft it may not read. Whether that caller should be allowed to *destroy* content it cannot read is the separate question answered by the `draft_edit` capability in [ADR 0031].

## Alternatives Considered

- **Return the UID from `create_draft` and let the client hold it.** Rejected. The UID changes on every revision, so the client would have to be told the new UID after each edit and would have to understand why — which is the protocol detail we are hiding. It also dies on `UIDVALIDITY` change and on any external modification, without the client being able to tell those cases apart.
- **`UIDPLUS` / `APPENDUID` as the source of the handle.** Rejected as a *requirement*, for the same UID-churn reason plus the fact that the server evaluates no capabilities today and would silently lose the feature against servers without the extension. `APPENDUID` remains available as a later optimization for locating the appended message faster; it is not the identity.
- **The `Message-ID` as the draft handle.** Rejected. [ADR 0008] uses `Message-ID` as the saga's idempotency key; making it stable across revisions would make "is this the message I just appended, or the previous revision?" unanswerable by the existing search. It also collides with any client that copies a draft, producing two messages with one identity.
- **A local SQLite table mapping `draft_id` to UID.** Rejected. It introduces a second source of truth that desynchronizes on every external change, on every `UIDVALIDITY` change, and whenever a second instance touches the same mailbox. The failure is silent: the table says the draft is at UID 42, the mailbox disagrees. The header-based design fails loudly instead, with `draft_not_found`.
- **IMAP keywords instead of headers** (`$SCDraftId9f2c…`, `$SCDraftRev2`). Genuinely attractive: keywords are preserved by the rewrite saga, they are searchable with `SEARCH KEYWORD`, and they never leave the mailbox, which removes the header-leak drawback entirely. Rejected because Gmail maps IMAP keywords to user-visible labels ([ADR 0019]), so every draft would litter the user's label list with a UUID; because keyword syntax restricts the character set and length in ways that vary by server; and because a numeric counter encoded as a flag name is a poor fit for an ordered value.
- **Reuse the existing `Message-ID` search and accept ambiguity.** Rejected outright: the whole point of this design is that a partially completed revision must be resolvable deterministically.

## References

- [ADR 0005] — per-folder write capabilities, including the drafts-write-isolation pattern this design must not break.
- [ADR 0006] — the saga whose rewrite path this identity scheme feeds.
- [ADR 0008] — `Message-ID` as idempotency key, the reason draft identity is a separate header.
- [ADR 0016] — the tool set that `draft_id` and `revision` extend.
- [ADR 0018] — non-goal surface; deletion stays out, which is why there is no `delete_draft`.
- [ADR 0031] — the revision saga that consumes this handle.
- [LIM 0022] — externally edited drafts lose the handle.
- RFC 3501 §6.4.4 — `SEARCH HEADER`.
- RFC 4315 — `UIDPLUS`, referenced as the rejected identity source.
- RFC 4122 §4.4 — `uuid4`.

[ADR 0005]: 0005-per-folder-write-capabilities.md
[ADR 0006]: 0006-cross-account-move-via-saga.md
[ADR 0008]: 0008-idempotency-via-message-id-and-hash.md
[ADR 0016]: 0016-mcp-tool-set.md
[ADR 0018]: 0018-non-goal-tool-surface.md
[ADR 0019]: 0019-gmail-label-semantics.md
[ADR 0031]: 0031-draft-revision-via-rewrite-saga.md
[LIM 0022]: ../limitations/0022-externally-edited-drafts-lose-their-handle.md
