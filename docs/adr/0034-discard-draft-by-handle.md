# ADR 0034: Discard a Draft by Its Handle

- **Status:** Accepted
- **Date:** 2026-11-03
- **Deciders:** Randy Gupta
- **Amends:** [ADR 0018] (discarding stays a move), [ADR 0027] (error type), [ADR 0031] (how a draft is discarded)

## Context

[ADR 0030] identifies a draft by `draft_id` and keeps UIDs away from the
caller: `create_draft`, `get_draft`, `list_drafts` and `update_draft`
never return one. [ADR 0031] says a draft is discarded with `move` to a
trash folder, and `move` takes a UID. A caller holding only the handle
must therefore search the drafts folder, usually by subject, to find a
UID. That search can match the owner's own draft with the same subject,
and on localized Gmail accounts the trash folder has a name the caller
may not know.

Agents in production create a corrected draft instead of revising the
old one and leave the old one behind; the owner deletes it by hand.

[ADR 0018] excludes hard deletion. Discarding must remain a move to a
trash folder whose policy models retention.

## Decision

**A new tool `discard_draft(account, folder, draft_id)` moves every
message carrying the handle in the named folder to the account's trash
folder.**

- The trash folder is the folder with the RFC 6154 `\Trash` flag,
  resolved as in [ADR 0033]. There is no configuration for it.
- All revisions with the handle are moved, residue of an interrupted
  revision included ([ADR 0030] §2). Messages without the handle are
  never touched.
- Authorization is that of `move`: `move_out` on the drafts folder and
  `accept_incoming` on the trash folder ([ADR 0005]). A missing
  capability yields `DENY` with `capability_missing` and the missing
  capability named.
- Results: `discarded_count` and `moved_to` (canonical name) on
  success. Errors use the envelope of [ADR 0027]: `draft_not_found`
  when no message carries the handle, and the new error type
  `trash_unavailable` when the account has no `\Trash` folder (amends
  the closed enumeration of [ADR 0027]). Nothing is moved on any error.
- On Gmail the move is the label swap of [ADR 0019]; elsewhere it is
  `MOVE`, or `COPY` plus `\Deleted` plus `EXPUNGE` on the moved UIDs.
- The call is audited like every tool call; content is not
  ([ADR 0021]).

## Consequences

### Positive

- The handle is enough for the whole life of a draft: create, read,
  revise, discard.
- No subject search, so no risk of moving the owner's draft.
- The caller does not need to know the localized trash name.

### Negative

- One more tool on the surface ([ADR 0016]).
- An account without a `\Trash` folder cannot discard drafts through the
  server; the owner must create one or discard by hand.

### Neutral

- `move` keeps working for callers that hold a UID.

## Security Implications

- **Attack surface:** one new tool, gated by existing capabilities.
- **Trust boundaries:** unchanged.
- **Data exposure:** none; the tool returns counts and a folder name.
- **Failure modes:** a partial move (some revisions moved, then an IMAP
  error) leaves the remaining revisions in place and reports the error;
  a repeated call moves the rest. No hard deletion is possible.

## Alternatives Considered

- **Let `move` accept a `draft_id` in place of a UID.** Rejected: the
  caller still has to name the trash folder, including its localized
  name, and the intent is harder to discover.
- **Only document the search-then-move path.** Rejected: it keeps the
  risk of moving a foreign draft.
- **Configurable trash folder per account.** Rejected for now: the flag
  covers Gmail and common servers; `trash_unavailable` makes the gap
  visible.

## References

- [ADR 0005], [ADR 0016], [ADR 0018], [ADR 0019], [ADR 0021], [ADR 0027], [ADR 0030], [ADR 0031], [ADR 0033]
- `bdd/features/tool_surface/discard_draft.feature`

[ADR 0005]: 0005-per-folder-write-capabilities.md
[ADR 0016]: 0016-mcp-tool-set.md
[ADR 0018]: 0018-non-goal-tool-surface.md
[ADR 0019]: 0019-gmail-label-semantics.md
[ADR 0021]: 0021-audit-log-format.md
[ADR 0027]: 0027-error-envelope-and-tool-surface-versioning.md
[ADR 0030]: 0030-draft-identity-and-revisions.md
[ADR 0031]: 0031-draft-revision-via-rewrite-saga.md
[ADR 0033]: 0033-gmail-special-use-folder-resolution.md
