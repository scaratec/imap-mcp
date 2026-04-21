# ADR 0005: Per-Folder Write Capabilities

- **Status:** Accepted
- **Date:** 2026-04-20
- **Deciders:** Randy Nel Gupta

## Context

[ADR 0002] defines what a caller may *read* from a folder. Callers also
need to modify mailbox state in specific, auditable ways: mark a message
as read, tag it as handled, move it to another folder, accept incoming
moves, and save drafts.

Collapsing all of these into a single `write: bool` is too coarse. The
most valuable real-world patterns require finer distinctions:

- **Archive pattern.** A folder where the agent may *deposit* messages
  moved from elsewhere, but may neither read them again nor move them
  out. This is the standard pattern for steuerrelevante Belege and for
  DSGVO-retention archives.
- **Drafts pattern.** A folder where the agent writes new messages but
  cannot read existing ones — including its own earlier drafts.
- **Standard processing pattern.** A folder where the agent reads, marks
  as read, tags as handled, and moves the message out, but cannot itself
  receive messages moved in (only the upstream SMTP delivery does that).

IMAP itself is append-only on message content: messages cannot be
modified in place. All mutations are flag operations (`STORE`), copies,
moves, or appends. The capability set must therefore map to these IMAP
primitives, not to a generic "write" abstraction.

## Decision

Every folder policy declares, independently of its read-side `visibility`,
a set of **five boolean write capabilities**:

| Capability        | IMAP mechanism                                  | Meaning |
|-------------------|--------------------------------------------------|---------|
| `mark_seen`       | `STORE ±FLAGS (\Seen)`                          | Caller may toggle the `\Seen` flag on messages in this folder. |
| `mark_tagged`     | `STORE ±FLAGS (\Flagged keyword …)`             | Caller may set/remove `\Flagged` and user-defined keywords/labels. No other system flags. |
| `move_out`        | `MOVE` / `COPY+STORE \Deleted+EXPUNGE`          | Caller may remove messages from this folder (moving them into another, including a trash folder). |
| `accept_incoming` | target of `MOVE`/`COPY`/`APPEND` (existing msg) | Folder may be the *destination* of a move/copy from another folder or account. |
| `draft_append`    | `APPEND` of a new RFC822 message                | Caller may deposit a newly composed message here. Intended for drafts folders. |

All capabilities default to `false`. A folder policy that enables a
capability is the only way a caller gets to invoke the corresponding tool
([ADR 0016]) against that folder.

Capabilities live on the **folder level**, not the sender-rule level. A
per-sender write permission would mean that the agent had to inspect the
message before acting on it, but the inspection itself is what triggers
the authorization check; the ordering makes per-sender write semantics
inconsistent.

There is no `delete` capability. Deletion is expressed as `move_out` with a
target folder that has `accept_incoming: true` and (typically) no
`move_out`. The trash folder is thus a policy object, not a hardcoded
special case.

## Consequences

### Positive

- **Archive pattern is trivial.**
  `visibility: NONE, accept_incoming: true, everything else false` —
  agent deposits and never reads.
- **Drafts pattern is trivial.**
  `visibility: NONE, draft_append: true, everything else false` —
  agent writes new messages but cannot read its own drafts again.
- **No hardcoded special folders.** Trash, Archive, Drafts are ordinary
  folders with specific capability patterns.
- **Capabilities map 1:1 to MCP tools** ([ADR 0016]). Each tool invocation
  can be checked against a single boolean, no compound logic.
- **Audit records capability usage directly.** "Capability `mark_tagged`
  was exercised on folder X by caller Y" is a primitive log record.

### Negative

- **Five booleans per folder** is more to write than one. Concrete policy
  files become longer, though the values are usually `true`/`false`
  defaults (`false` by default, `true` only where intended).
- **No per-sender write differentiation.** A caller that can `move_out`
  one message from a folder can move all messages from it. If per-sender
  write semantics become necessary, they need a distinct ADR and a
  different evaluation order than read rules.

### Neutral

- Capability semantics are IMAP-standard except where [ADR 0019]
  re-maps them for Gmail (label-swap for `move`, etc.). The mapping is
  inside the IMAP core, invisible to policy.

## Security Implications

- **Explicit allow-list per capability.** A policy with `mark_seen: false`
  makes `STORE \Seen` rejected *before* it reaches the server. A bug in
  the IMAP driver cannot produce unauthorized flag changes because the
  driver is never invoked.
- **Destruction requires two capabilities.** Deletion = `move_out` on the
  source plus `accept_incoming` on the target. An accidentally-permissive
  policy on one side is blocked by the other.
- **Drafts write isolation.** `draft_append: true` with `visibility: NONE`
  means the agent can write drafts it cannot re-read. This prevents
  prompt-injection loops in which a compromised agent reads a previously
  planted draft as instructions.
- **Archive immutability.** A correctly configured archive has
  `accept_incoming: true` and nothing else. Once a message is in the
  archive, no caller with this policy can retrieve or remove it, short of
  operator intervention outside MCP.
- **No `\Deleted` flag tool.** Callers cannot mark messages deleted except
  indirectly via `move_out`. This prevents split-brain states where a
  message is flagged-deleted-but-not-expunged, which are a common source
  of recovery ambiguity.

## Alternatives Considered

- **Single `write: bool`.** Rejected; loses the archive/drafts patterns,
  which are the hardest-to-express and most valuable.
- **Two booleans (`can_append`, `can_modify`).** An earlier revision of
  this design. Rejected because `can_modify` conflated three genuinely
  separate operations (flag-seen, flag-tag, move-out), and the draft case
  (`can_append` of *new* content) collided with the move-target case
  (`can_append` of *moved* content) in a way that obscured the
  drafts-without-read pattern.
- **Capability-per-IMAP-command.** Too granular (we would be exposing
  `STORE`, `COPY`, `MOVE`, `APPEND` separately), and the semantic grouping
  done above is exactly what operators want to reason about.
- **Integrating write rights into the visibility scale** (e.g. `WRITE`
  level above `FULL`). Rejected in the A1 discussion: visibility and
  write are orthogonal axes, and a combined enum requires either product
  explosion (7 × 2^5 = 224 combinations) or awkward shortcuts.

## References

- [ADR 0002] — visibility scale that this capability set is orthogonal to.
- [ADR 0016] — tool surface that maps one-to-one onto these capabilities.
- [ADR 0019] — Gmail label semantics that re-map the underlying IMAP
  mechanisms without changing the capability surface.
- RFC 3501 §6.4 — IMAP `STORE`, `COPY`, `APPEND`.
- RFC 6851 — the `MOVE` extension referenced by `move_out` / `accept_incoming`.

[ADR 0002]: 0002-linear-visibility-levels.md
[ADR 0016]: 0016-mcp-tool-set.md
[ADR 0019]: 0019-gmail-label-semantics.md
