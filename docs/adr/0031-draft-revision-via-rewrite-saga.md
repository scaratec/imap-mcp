# ADR 0031: Draft Revision via the Message-Rewrite Saga

- **Status:** Proposed
- **Date:** 2026-09-18
- **Deciders:** Randy Gupta

## Context

[ADR 0030] gives a draft a stable identity that survives the `APPEND`/`EXPUNGE` cycle. What is still missing is the operation itself: a caller has no way to change a draft it has written. The workaround available today is for the client to do the cycle by hand — `fetch_body`, re-compose the whole message, `create_draft` again, then `move` the previous version to a trash folder.

That workaround is unacceptable for three reasons, and they are the forces behind this decision:

1. **It leaks the protocol.** The client has to know that editing means re-appending, and it has to deal with the fact that its old handle is now dead. That is precisely what this server exists to hide.
2. **It is not atomic.** Three tool calls, three failure points. An interruption between them leaves two drafts, or one draft and a stranded copy in trash, and the client has no way to find out which.
3. **It does not work under drafts write isolation.** The pattern recommended by [ADR 0005] — `visibility: NONE, draft_append: true` — denies the caller both the read it needs to re-compose the message and the `move_out` it needs to clean up the old version. Under that policy the manual loop is simply impossible.

The machinery for doing it correctly already exists. `SagaManager.run_message_rewrite` performs FETCH → `transform(bytes) -> bytes` → `APPEND` (preserving flags) → locate new message → delete source → commit, journalled in the WAL ([ADR 0007]) and resumable after a crash. It is what the three attachment tools run on. A draft revision is the same shape of operation with a different transform.

One property of that path, normally a defect, is correct here: the rewrite sets `INTERNALDATE` to the time of the append, losing the original. For a draft, the time of the last edit is the right timestamp.

## Decision

**A draft revision is a field-wise change, executed as one message-rewrite saga, guarded by an optimistic revision check.**

### The tool

```text
update_draft(account, folder, draft_id, base_revision, changes) -> { draft_id, revision, mechanism, tx_id?, idempotent_replay? }
```

`draft_id`, `revision` and `mechanism` are always present. The two optional fields say which path produced the answer, and they are part of this decision rather than a handler improvising ([ADR 0027] admits no per-handler field that no decision names). `tx_id` is present exactly when a transaction ran. `idempotent_replay: true` marks the single path on which none did — the recognized retry described under *Concurrency* writes nothing, so there is no transaction to name, and `tx_id` is therefore absent rather than carrying the id of the earlier call whose result the caller is being handed. `mechanism` stays `draft_revision` on that path too: it names the operation the caller asked for, not whether it had to be executed again. On the normal path `idempotent_replay` is absent rather than `false`, so a caller that has never heard of the field reads a replay as what it is — a success at the revision it asked for.

`changes` is an object with at least one member, drawn from `subject`, `to`, `cc`, `bcc`, `body_text`, `body_html`, `in_reply_to`. The semantics are three-valued and explicit:

| Form in `changes` | Meaning |
|---|---|
| member absent | field unchanged |
| member present with a value | field set to that value |
| member present with `null` (or `[]` for an address list) | field removed from the message |

Everything not addressed by `changes` is carried over verbatim: attachment parts, unknown headers, `From`, `References`. Four headers are regenerated on every revision: `X-SC-Draft-Rev` (incremented), `Message-ID`, `Date` (the time of the edit), and `X-SC-Draft-Change-Hash`. If the last body part would be removed, an empty `text/plain` part remains rather than a message with no body.

An empty `changes` object is rejected by the tool schema (`minProperties: 1`), not by a runtime error type.

Two read-side tools complete the loop:

```text
get_draft(account, folder, draft_id)   -> { draft_id, revision, subject, to, cc, bcc, body_text, body_html, attachments }
list_drafts(account, folder)           -> { drafts: [ { draft_id, revision, subject, editable } ] }
```

`get_draft` requires `visibility >= BODY`, the same bar as `fetch_body`. `list_drafts` requires `visibility >= METADATA`, the same bar as `list_messages`, and reports messages without an `X-SC-Draft-Id` header as `editable: false`.

There is no `delete_draft` and no send path. [ADR 0018] closes deletion as a non-goal and permits `EXPUNGE` only as the terminal step of a saga; discarding a draft is `move` to a trash folder, which already exists.

### The capability

Draft revision requires a **new per-folder write capability, `draft_edit`**, defaulting to `false`, extending the set defined in [ADR 0005].

It is not `draft_append`. A caller that may deposit new content is not thereby entitled to destroy existing content — and under drafts write isolation the content being destroyed is content the caller may not even read. Granting that power retroactively to every policy that enables `draft_append` would be a silent privilege escalation on existing deployments.

It is not `modify_message` either. That capability governs rewriting arbitrary messages in arbitrary folders, which is a strictly larger power than revising a draft the caller itself created; requiring it would force operators to grant attachment-rewrite rights on the drafts folder in order to allow an edit.

`draft_edit` does not imply `draft_append`. The two are independent: `draft_edit` operates only on messages that already carry an `X-SC-Draft-Id`.

### Concurrency

`base_revision` is mandatory. Let `cur` be the highest live revision for the `draft_id`:

- `base_revision == cur` — the normal path; revision `cur + 1` is produced.
- `base_revision == cur - 1` **and** the message at `cur` carries an `X-SC-Draft-Change-Hash` equal to the hash of the submitted `changes` — this is a retry of a call whose response was lost. Nothing is written; the response reports `revision: cur` with `idempotent_replay: true`.
- anything else — `draft_revision_conflict`, carrying `current_revision: cur`.

The change hash is `sha256` over the canonical JSON encoding of the `changes` object: members sorted lexicographically, no insignificant whitespace, UTF-8, `null` members preserved. It is a hash of the *request*, not of the produced message, so it is deterministic despite the regenerated `Date`, `Message-ID` and MIME boundaries.

### The saga

`update_draft` runs `run_message_rewrite` with one step in front and one changed step inside:

1. Resolve `draft_id` to a UID by header search ([ADR 0030]) and read its revision.
2. Apply the revision check above.
3. Rewrite saga: FETCH → transform → `APPEND` (flags preserved, `\Draft` among them) → locate → delete source → commit.
4. The new message is located by `SEARCH HEADER "X-SC-Draft-Rev" "<n+1>"` within the `draft_id`, which is exact, rather than by the `Message-ID` heuristic used on the attachment path.

The response carries `mechanism: "draft_revision"` and the `tx_id`, so `get_transaction_status` works as it does for every other saga-backed tool.

**Recovery is forward-only; there is no compensating rollback.** After any interruption at most two messages share the `draft_id`, ordered by revision, and the rule "highest revision wins, lower ones are residue" ([ADR 0030]) determines the correct state from the mailbox alone. A crash after `APPEND` and before the delete is therefore invisible to the caller: the next call resolves to the new revision, and the residue is swept by the resumed transaction, or by the next `update_draft` if no transaction survived. Un-appending is never needed and is never attempted.

If the delete step cannot complete at all — a read-only folder, an `EXPUNGE` the server refuses — the new revision still stands and the caller is not blocked; the transaction retries and finally escalates to `needs_operator`.

### Error types

Four entries are added to the closed enumeration in the error envelope ([ADR 0027]):

| Error type | Raised when |
|---|---|
| `draft_not_found` | no message in the folder carries this `X-SC-Draft-Id` |
| `draft_not_editable` | the addressed message carries an `X-SC-Draft-Id` but no usable `X-SC-Draft-Rev` — typically a foreign client that preserved one header and not the other ([LIM 0022]) |
| `draft_revision_conflict` | `base_revision` does not match and is not a recognized replay |
| `draft_ambiguous` | two live messages share `draft_id` *and* revision; escalated to `needs_operator` |

`draft_ambiguous` is the one error that is also an incident ([ADR 0030]). A transaction is opened for it that never runs a step: it is escalated to `needs_operator` immediately, it records the colliding UIDs, and its `tx_id` is returned alongside the error, so the case the caller reports and the case the operator works on are demonstrably the same one.

The tool-set version goes to **2.1.0**. Every change here is additive — new tools, new response fields, new capability defaulting to `false` — so `breaking_changes_since` stays untouched ([ADR 0027]).

## Consequences

### Positive

- **One call per revision, arbitrarily often.** The client holds a `draft_id` and an integer and never learns that a message was replaced.
- **Editing works under drafts write isolation.** The old content is read inside the saga, not by the caller. A caller with `visibility: NONE, draft_edit: true` can revise a draft it can never read — the field-wise change surface is what makes that coherent.
- **No new transaction machinery.** WAL schema, state machine, `resume`, `run_pending_recovery`, the crash hooks from [ADR 0023] and `get_transaction_status` all apply unchanged.
- **The ambiguous new-UID lookup disappears on this path**, replaced by an exact revision search.
- **Lost updates are impossible silently.** A concurrent edit from another client is reported as a conflict with the current revision number, which is enough for the caller to re-read and retry.
- **A lost response costs nothing.** The retry is recognized as a replay instead of producing a second revision.

### Negative

- **A seventh write capability.** Policy files grow another boolean, and operators must set it explicitly to enable editing on folders where `draft_append` alone used to be the whole drafts story.
- **Asymmetric composition surface.** `create_draft` takes raw `rfc822`; `update_draft` takes fields. A caller composing from scratch and a caller revising use two different vocabularies for the same message. Adding a structured alternative to `create_draft` would close this and is deliberately not part of this decision.
- **Attachments are outside the change set.** Until the attachment tools learn to address a draft by `draft_id`, the same object has two addressing regimes — `draft_id` for its fields, UID for its parts — and an `update_draft` call invalidates the UID a caller was holding for the attachment tools.
- **Server-side MIME rebuild on every edit.** The whole message is parsed and re-serialized for a one-word subject change, and the full body crosses the wire twice per revision.

### Neutral

- `INTERNALDATE` moving to the time of the edit is correct for drafts, but it is the same behaviour the attachment path is criticized for on ordinary mail. The two paths are not being unified here.
- Revision numbers are per draft and monotonic, but not dense across failures: an escalated transaction can leave a gap. Callers must treat the number as an opaque comparison token, not as an edit count.

## Security Implications

- **Attack surface.** One new write tool and two new read tools, all behind the existing `decide_folder_access` check and the capability gate. No new transport, no new credential, no new external dependency.
- **Trust boundary.** Unchanged. The new capability is evaluated in the same PDP at the same point as the existing six, and the IMAP driver is never invoked when it is absent.
- **The destructive step is gated separately and defaults to closed.** This is the whole reason for `draft_edit`: `update_draft` expunges the previous revision, and under `visibility: NONE` it does so to content the caller cannot read. No existing deployment gains that power by upgrading.
- **Data exposure.** `update_draft` returns no message content, so it discloses nothing beyond the revision number even when the folder is write-only. `get_draft` does disclose content and is therefore held to the `BODY` bar rather than to the write capability.
- **Failure modes.** Partial failure leaves a deterministically ordered pair of revisions, not a split-brain state. The one unorderable case is escalated instead of guessed. A caller cannot use a stale `base_revision` to resurrect old content, because the check rejects it rather than rebasing it.
- **Malicious input.** `changes` values become header and body content. Address fields must be validated against RFC 5322 before assembly; header values must be rejected if they contain CR or LF, otherwise a `subject` of `"x\r\nBcc: attacker@example.com"` would inject a header. This is the one genuinely new injection path in the design and it is specified as a rejection, not a sanitization.
- **Auditability.** Every revision is one saga transaction with a `tx_id`, a `draft_id` and a revision number in the audit record, giving a complete, ordered edit history per draft. The message bodies themselves are not audited, consistent with [ADR 0021].
- **Known adjacent defect.** `get_transaction_status` does not check that the caller owns the transaction (ROADMAP #20). Draft transactions inherit that gap; it is not created by this decision, but a draft `tx_id` now also identifies an editing session.

## Alternatives Considered

- **Keep the manual loop and just document it.** The status quo. Rejected for the three reasons in Context — in particular because it is not merely inconvenient but impossible under the recommended drafts policy.
- **`replace_draft(draft_id, rfc822)` — full-message replacement instead of field-wise changes.** Rejected as the primary surface: it requires the caller to possess the complete current message, which under `visibility: NONE` it cannot obtain, so the isolation pattern would lose the feature. It is also all-or-nothing against concurrent edits. It remains a plausible later addition for callers that do hold `BODY` visibility.
- **Reuse `draft_append` as the authorization for editing.** Rejected. It would grant every existing drafts policy the power to expunge on upgrade, and it conflates depositing new content with destroying existing content.
- **Reuse `modify_message`.** Rejected. It is a broader right than the operation needs, and it would force operators to enable arbitrary message rewriting on the drafts folder to permit a subject change.
- **Pessimistic locking (`lock_draft` / `unlock_draft`).** Rejected: it requires server-side lease state with expiry, reintroducing exactly the local state [ADR 0030] avoids, and it does not protect against edits made outside MCP anyway. The revision counter gives comparable protection with no state.
- **No concurrency control — last write wins.** Rejected: an edit made from another client would vanish without either side noticing, which is the worst available failure mode for a document a human is also working on.
- **A dedicated draft state machine with its own journal.** Rejected: `run_message_rewrite` plus the revision ordering already provides atomicity and recovery. A second mechanism would have to be kept consistent with the first for no gain.
- **Backward compensation (delete the appended revision on failure).** Rejected on principle: the mailbox already encodes which revision is authoritative, so forward recovery converges without the risk that a compensation step destroys the only good copy.

## References

- [ADR 0005] — the write-capability set this decision extends with `draft_edit`.
- [ADR 0006] — the saga this decision reuses.
- [ADR 0007] — the WAL that journals it.
- [ADR 0018] — the non-goal surface that keeps `delete_draft` out.
- [ADR 0021] — audit log format.
- [ADR 0023] — the test-only crash hooks used to prove recovery.
- [ADR 0027] — error envelope and tool-surface versioning.
- [ADR 0030] — the draft handle and revision ordering this decision consumes.
- [LIM 0022] — externally edited drafts lose the handle.
- RFC 5322 §3.6 — header field syntax; the basis for the CR/LF rejection rule.
- RFC 3501 §6.4.4 — `SEARCH HEADER`.

[ADR 0005]: 0005-per-folder-write-capabilities.md
[ADR 0006]: 0006-cross-account-move-via-saga.md
[ADR 0007]: 0007-sqlite-as-wal-store.md
[ADR 0018]: 0018-non-goal-tool-surface.md
[ADR 0021]: 0021-audit-log-format.md
[ADR 0023]: 0023-test-only-control-surface.md
[ADR 0027]: 0027-error-envelope-and-tool-surface-versioning.md
[ADR 0030]: 0030-draft-identity-and-revisions.md
[LIM 0022]: ../limitations/0022-externally-edited-drafts-lose-their-handle.md
