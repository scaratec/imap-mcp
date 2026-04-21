# ADR 0019: Gmail Label Semantics

- **Status:** Accepted
- **Date:** 2026-04-20
- **Deciders:** Randy Nel Gupta

## Context

Gmail is the only major provider that diverges substantially from
standard IMAP semantics:

- Messages are not assigned to folders; they carry **labels**. IMAP
  represents each label as a folder, so a message with two labels
  appears in two "folders" simultaneously.
- `[Gmail]/All Mail` contains every non-trashed message, regardless
  of labels.
- An IMAP `MOVE` between "folders" is implemented as label-swap:
  remove the source label, add the target label. The message object
  itself does not move.
- Deletion via `EXPUNGE` in a regular-looking folder only removes the
  label; the message remains under `All Mail` until moved to
  `[Gmail]/Trash` explicitly.

Treating Gmail as a regular IMAP server silently produces wrong
mental models. A caller that sees the same message listed in two
folders interprets it as two messages. A caller expecting
`move` to relocate a message finds instead that the message
continues to exist under `All Mail`.

Treating Gmail specially, on the other hand, requires carrying
provider awareness into the server core, which we otherwise try to
keep provider-agnostic.

Because Gmail accounts are a primary target for V1 (the user's
personal scaratec@gmail.com, for example), the "refuse to support
Gmail" path is not a viable V1 answer.

## Decision

Gmail is supported as a **first-class provider with explicit
label semantics**. Accounts opt into this behaviour via
`provider: google` in their configuration; this flag is already set
for OAuth2 scope reasons ([ADR 0009]). The server adapts both its
read and write surfaces for such accounts.

### Read-side adaptations

- `list_folders` annotates the account with
  `semantics: "gmail-labels"` and each label-folder carries the same
  flag. Callers can use this to adjust their reasoning.
- `search` results include a `canonical_all_mail_uid` per message,
  the UID of that message under `[Gmail]/All Mail`. Callers can
  deduplicate multi-label appearances by this identifier.
- `fetch_envelope` response for a Gmail account includes
  `labels: ["INBOX", "Rechnungen", ...]` — the complete label list
  the message carries, subject to visibility policy.
- A new read tool **`list_labels(account)`** is available only on
  Gmail accounts. It enumerates labels with message counts. Not
  offered on non-Gmail accounts.

### Write-side adaptations

- **`move`** on a Gmail account implements label-swap: remove source
  label, add target label, on the same connection and within a
  single `STORE` pair. This is atomic at the Gmail server; no saga
  needed for intra-account moves.
- **Cross-account moves from a Gmail account** fetch bytes from
  `[Gmail]/All Mail` rather than from the source label folder.
  This is deterministic (every message is there exactly once) and
  independent of which label the saga thinks it is "moving out of".
- **`mark_tagged`** on a Gmail account operates on Gmail labels
  directly (ADD/REMOVE via IMAP keywords that Gmail maps to
  labels). `\Flagged` is supported identically to standard IMAP.
- **`create_draft`** uses `[Gmail]/Drafts` as the target and relies
  on Gmail's APPEND-to-Drafts semantics. `draft_append` capability
  on that folder is the sole gate.

### System folders

`[Gmail]/All Mail`, `[Gmail]/Sent Mail`, `[Gmail]/Spam`, `[Gmail]/
Starred`, `[Gmail]/Trash`, and `[Gmail]/Drafts` are addressable in
policy. The server flags each as a system folder in
`describe_policy` ([ADR 0017]) so callers understand their role.

Policy authors are free to grant or deny these folders as usual.
There is no implicit access.

### Not offered in V1

- Direct multi-label assignment via a dedicated tool (e.g.
  `add_labels(uid, [a, b, c])`). `mark_tagged` with the `add` mode
  is sufficient.
- Gmail thread semantics (`X-GM-THRID`, conversation grouping).
  Thread-view composition is a distinct feature; it is out of scope
  for V1 and will have its own ADR when needed.

## Consequences

### Positive

- **Gmail is usable with honest semantics.** Callers that understand
  the `semantics: gmail-labels` flag can deduplicate search
  results and reason correctly.
- **Intra-account moves on Gmail are fast.** Single-command label-
  swap is atomic server-side; no saga overhead.
- **Cross-account moves are deterministic.** Fetching from
  `All Mail` removes the ambiguity of "which label holds the
  canonical copy".
- **Policy authors are not burdened.** Ordinary rules
  (`from_domain`, `newer_than`, etc.) work against Gmail labels as
  they do against standard folders.

### Negative

- **Provider awareness in the IMAP core.** The server's IMAP driver
  carries a small amount of provider-specific logic. We judge the
  alternative (forcing callers to reason about this) to be worse.
- **Duplicate appearances in search results** until the caller
  deduplicates by `canonical_all_mail_uid`. This is unavoidable
  without fundamentally rewriting what Gmail presents via IMAP.
- **`list_labels` is a per-provider tool.** The tool surface is
  no longer fully uniform across accounts. Callers can query
  availability via `describe_policy`.

### Neutral

- Microsoft 365 and self-hosted Dovecot/Cyrus use standard IMAP
  folders with no label concept. They carry `semantics:
  "imap-standard"` and none of the adaptations apply.

## Security Implications

- **Same policy semantics, adapted mechanism.** Whitelist/blacklist
  and visibility levels apply unchanged. Label-swap moves are
  still subject to `move_out`/`accept_incoming` checks on the
  source and target "folders" (labels). The underlying IMAP
  mechanism is different; the authorization model is identical.
- **`canonical_all_mail_uid` is not a leak.** It names a message
  the caller is already authorized to see. It does not reveal
  anything about messages hidden from the caller.
- **`list_labels` respects policy.** Labels whose corresponding
  folder-policy grants `NONE` for the caller are not enumerated.
  Aggregate hidden-labels-count is exposed ([ADR 0017]).
- **Cross-account Gmail fetches go through `All Mail`.** This is
  a deliberate choice: it ensures the saga's idempotency lookup
  matches a stable UID, but it also means a caller who can
  `move` a message from *any* Gmail label has effective access
  to its `All Mail` content. Policy authors must be aware: a
  `move_out` capability on a Gmail label implies read access
  sufficient to copy the entire message body cross-account.
- **No hidden default grants for Gmail system folders.** Every
  `[Gmail]/*` folder default-denies like any other. An operator
  who wants agent access to drafts or trash must declare it.

## Alternatives Considered

- **Refuse to support Gmail in V1.** Rejected; removes a primary
  target user. Delaying a decision does not eliminate it.
- **Treat Gmail as ordinary IMAP, document the duplicates.**
  Rejected; stills LLM callers into wrong conclusions. "Document
  it" is not a substitute for correct semantics at the API.
- **Expose a canonical-only view** (show each message only once,
  under its "primary" label by some heuristic). Rejected as a
  synthesis of convenience: "primary label" is not a Gmail
  concept, and the heuristic would have to be committed to.
- **Offer Gmail-specific tools for every Gmail operation.**
  Rejected; breaks the one-tool-per-capability mapping of
  [ADR 0016]. `list_labels` is the one Gmail-only read tool
  because it has no meaningful standard-IMAP analogue.
- **Treat labels as tags in a flat model, ignore the folder
  projection Gmail offers.** Rejected; IMAP access is the only
  standardized mechanism Gmail offers, and we must work within
  it.

## References

- [ADR 0005] — write capabilities that Gmail operations map onto.
- [ADR 0009] — the `provider: google` flag originally introduced
  for OAuth2 scope.
- [ADR 0016] — tool surface; `list_labels` is the only Gmail-only
  tool.
- [ADR 0017] — `semantics` flag consumed via `describe_policy`.
- Gmail IMAP Extensions:
  <https://developers.google.com/gmail/imap/imap-extensions>
  (`X-GM-LABELS`, `X-GM-MSGID`, `X-GM-THRID`).

[ADR 0005]: 0005-per-folder-write-capabilities.md
[ADR 0009]: 0009-oauth2-authorization-code-with-scope-minimization.md
[ADR 0016]: 0016-mcp-tool-set.md
[ADR 0017]: 0017-response-transparency-for-filtered-data.md
