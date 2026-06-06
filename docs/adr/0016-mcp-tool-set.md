# ADR 0016: MCP Tool Set

- **Status:** Superseded by [ADR-0026](0026-tool-surface-consistency.md) for the tool list and by [ADR-0025](0025-folder-path-contract-and-error-taxonomy.md) for the `folder` argument semantics
- **Date:** 2026-04-20
- **Deciders:** Randy Nel Gupta

## Context

The MCP tool surface is the concrete API a caller sees. It must:

- Cover the read levels defined in [ADR 0002] and the write
  capabilities defined in [ADR 0005] cleanly, without dead ends.
- Avoid tools that can express operations the policy layer cannot
  meaningfully authorize.
- Remain small enough that an LLM caller can reason about it and the
  security reviewer can audit it.

The goal of this ADR is to fix that surface once, so every other ADR
can reference it. Extensions are subject to a new ADR.

Tools that are deliberately *not* offered are addressed in [ADR 0018].

## Decision

V1 exposes sixteen MCP tools in three groups.

### Read-side tools (eight)

Each read tool has a minimum visibility level below which it returns
an auth-style error (`visibility_below_<level>`). A caller whose
policy does not reach that level for the target object is refused
before any IMAP call is issued.

| Tool | Minimum level | Returns |
|------|---------------|---------|
| `list_accounts` | AccountPolicy existence | IDs of accounts the caller may access, plus `hidden_accounts_count` ([ADR 0017]) |
| `list_folders(account)` | FolderPolicy existence > NONE | Visible folder paths, with mode and capabilities summary, plus `hidden_folders_count` |
| `folder_stats(account, folder)` | `COUNT` | `visible_count`, `hidden_count`, `visibility_level` |
| `search(account, folder, criteria)` | `METADATA` | List of UIDs policy-filtered per sender rules, with `matched_total`/`matched_visible`/`filtered_out` |
| `fetch_envelope(account, folder, uid)` | `ENVELOPE` | From/To/Cc/Subject/Date/Message-ID |
| `fetch_headers(account, folder, uid)` | `HEADERS` | Full RFC 5322 headers |
| `fetch_body(account, folder, uid)` | `BODY` | Text and HTML body parts |
| `fetch_attachment(account, folder, uid, part_id)` | `FULL` | Single attachment part |

The search criteria form uses the predicate grammar of [ADR 0004]
(core set only in V1).

### Write-side tools (five)

Each write tool maps to exactly one folder capability ([ADR 0005]).
Absence of the capability yields a `capability_missing` DENY at the
PDP.

| Tool | Required capability on target | Semantics |
|------|-------------------------------|-----------|
| `mark_seen(account, folder, uid, seen)` | `mark_seen` | `STORE ±FLAGS (\Seen)` |
| `mark_tagged(account, folder, uid, tags, mode)` | `mark_tagged` | `\Flagged` and user keywords; `mode: add|remove|replace` |
| `move(src, dst)` | `move_out` on src; `accept_incoming` on dst | Intra-account: RFC 6851 `MOVE`. Cross-account: saga ([ADR 0006]), returns `tx_id`. Gmail: label-swap ([ADR 0019]). |
| `copy(src, dst)` | `accept_incoming` on dst | Analogous to `move` without source delete. Cross-account: saga without the delete step. |
| `create_draft(account, folder, rfc822)` | `draft_append` on folder | `APPEND` of a newly composed RFC 5322 message. |

`move` and `copy` accept both intra- and cross-account forms; the
server decides which mechanism to use. The tool surface does not
distinguish them — the `tx_id` field in the response is `null` for
intra-account operations that are natively atomic.

### Meta tools (three)

| Tool | Zweck |
|------|-------|
| `describe_policy()` | Return the caller's own policy profile in structured form ([ADR 0017]). Callers should invoke this first in any session. |
| `get_transaction_status(tx_id)` | Report saga state: `pending`, `staged`, `committed`, `needs_operator`, `aborted`. |
| `get_caller_identity()` | Return the caller's resolved `caller_id` only. For debugging / assertion; exposes no policy or token data. |

### Transparency contract

Every response from every tool honours the transparency contract of
[ADR 0017]: redacted fields are flagged, filtered counts are exposed,
categorical reasons are included. No response silently omits data.

### Tool-set versioning

The set is versioned. An additive change (new tool) may be a minor
version; a breaking change (renamed tool, changed shape) is a major
version. Callers can inspect the version via a standard MCP
capability exchange.

## Consequences

### Positive

- **One-to-one mapping.** Every read level and write capability has
  exactly one tool. A reviewer looking at `fetch_body` knows the
  corresponding level is `BODY`; no ambiguity.
- **Small surface.** Sixteen tools fit in a caller's system prompt
  without cluttering its context window.
- **Clear authorization story.** Every tool invocation is checked
  against exactly one predicate (level for reads, capability for
  writes). No compound rules at the dispatch layer.
- **Transparency is structural.** Every tool returns the same kind
  of transparency fields; the caller does not re-discover the
  contract per tool.

### Negative

- **Callers who want "fetch whatever is permitted" must try the
  deepest tool and fall back.** They learn the permitted level by
  reading `describe_policy` or by observing refusals. Both are
  acceptable; neither is unexpected given policy-driven access.
- **`copy` vs `move` means two tools where some systems have one.**
  The distinction is semantic (source-delete or not) and visible to
  callers who may not care; policy authors, however, do care, because
  `copy` and `move` require different capabilities on the source.

### Neutral

- The `mode` parameter on `mark_tagged` (`add`/`remove`/`replace`) is
  strictly necessary: label operations are non-commutative and
  "replace" has a different meaning from "add".

## Security Implications

- **Every tool is authorization-checked before IMAP action.** The
  dispatch layer consults the PDP first; the IMAP layer never runs
  unauthorized commands.
- **Visibility gating is enforced at the tool boundary.** A bug in
  `fetch_body` cannot serve attachment bytes because the
  `fetch_attachment` code path is the only one that speaks to the
  corresponding MIME parts.
- **Write tools require explicit capability.** Absence is default-
  deny. A new capability cannot accidentally be exposed by adding a
  read tool.
- **`create_draft` is intentionally separate from any copy/move
  path.** APPEND of newly composed content is a different operation
  from reparenting existing content; conflating them would risk
  policy-bypass patterns where a caller uses a copy-like tool to
  inject new content into a folder it is only supposed to *read*.
- **`raw_imap_command` and `fetch_raw_rfc822` are out of scope.** See
  [ADR 0018]; any such back door would invalidate the authorization
  story.
- **Tool-set versioning protects policy.** A breaking tool change
  bumps the version; a caller pinned to version N cannot be
  surprised by a renamed tool that now has different capability
  requirements.

## Alternatives Considered

- **Single `fetch` tool with a requested-level parameter.** Rejected;
  it diffuses the one-tool-one-level property and makes the
  authorization story harder to reason about.
- **Combined `move_or_copy` with a `delete_source` boolean.**
  Rejected; `copy` and `move` need different capabilities and
  different saga shapes; the boolean would pretend to hide that.
- **Separate `move_intra_account` and `move_cross_account`.**
  Rejected; leaks an implementation boundary into the public
  surface. Callers should not choose the mechanism.
- **Offer `subscribe_to_folder` as a read tool** (tickle-style
  push over MCP). Rejected per [ADR 0014] and the out-of-band push
  discussion; push is not a V1 concern.
- **Admin tools on MCP** (`rotate_token`, `reload_policy`). Rejected
  per [ADR 0018] — admin is not MCP's job.

## References

- [ADR 0002] — visibility levels gating read tools.
- [ADR 0004] — search predicate grammar.
- [ADR 0005] — write capabilities gating write tools.
- [ADR 0006] — saga that powers cross-account `move`/`copy`.
- [ADR 0017] — transparency contract, `describe_policy` shape.
- [ADR 0018] — non-goal tool surface.
- [ADR 0019] — Gmail special semantics in `move` and `mark_tagged`.

[ADR 0002]: 0002-linear-visibility-levels.md
[ADR 0004]: 0004-sender-rule-matcher-grammar.md
[ADR 0005]: 0005-per-folder-write-capabilities.md
[ADR 0006]: 0006-cross-account-move-via-saga.md
[ADR 0017]: 0017-response-transparency-for-filtered-data.md
[ADR 0018]: 0018-non-goal-tool-surface.md
[ADR 0019]: 0019-gmail-label-semantics.md
