# ADR 0033: Gmail Special-Use Folders Resolve on Every Path

- **Status:** Accepted
- **Date:** 2026-11-03
- **Deciders:** Randy Gupta
- **Amends:** [ADR 0017] (reason code), [ADR 0019] (label swap), [ADR 0025] (folder names in responses)

## Context

Gmail shows its system folders under names in the account owner's
language. On a German account the wire names are `[Gmail]/Entwürfe`,
`[Gmail]/Papierkorb`, `[Gmail]/Gesendet` and `[Gmail]/Alle Nachrichten`;
`[Gmail]/Drafts` does not exist there and a `SELECT` of it fails.
Policies, tool descriptions and `list_folders` use the canonical English
names. The server maps between them through RFC 6154 special-use flags
(`\Drafts`, `\Trash`, `\Sent`, `\All`, `\Junk`, `\Flagged`, `\Important`).

That mapping is only partly applied:

1. The alias map is filled as a side effect of `list_folders` and lives
   in the process. A session whose first call is `create_draft` on
   `[Gmail]/Drafts` sends the English name to Gmail and fails.
2. The Gmail label-swap move (`_handle_move_gmail_label_swap`) never
   consults the map. It selects the source folder through a hard-coded
   English table and reports the failing `SELECT` as `uid_not_found`.
3. The cross-account saga looks up `[Gmail]/All Mail` by its English
   name and silently falls back when that fails.
4. The policy decision point compares folder strings literally. Owners
   therefore list every system folder twice, once per name, and the two
   entries can disagree; then the name a caller happens to use decides
   its rights.

Production evidence from one German account (May to October 2026): nine
failed draft discards from `[Gmail]/Drafts`, twelve failed `create_draft`
calls on `[Gmail]/Drafts`. The BDD suite did not notice because every
localized scenario called `list_folders` first and the mock accepted the
English name on a localized account, which Gmail does not.

## Decision

**Every folder name a caller sends is resolved to one canonical folder
before policy evaluation and to the account's wire name before any IMAP
command, on every code path, from the first call of a session on.**

- **Lazy, per-account alias map.** The first call that needs a Gmail
  folder on an account issues `LIST` once and builds the map from the
  special-use flags. The map is cached per account until the
  configuration is reloaded. `list_folders` refreshes it.
- **Both names are accepted.** For a Gmail special-use folder the
  caller may send the canonical name or the localized name. Both resolve
  to the same canonical folder.
- **One policy entry per physical folder.** Policy keys are normalized
  the same way. If a policy names a special-use folder only by its
  localized name, that entry governs the canonical folder. If it names
  the folder twice and the two entries differ in any setting, the
  folder is denied with the new reason code `policy_alias_conflict`
  (amends the reason catalogue of [ADR 0017]) and the denial is
  audited. Identical duplicates are accepted.
- **Responses use the canonical name** in every `folder`,
  `source_folder`, `target_folder` and `drafts_folder` field, as
  `list_folders` and `describe_policy` already do (amends [ADR 0025]).
- **The label swap resolves both folders** through the map and selects
  the wire name (amends [ADR 0019]). A source folder that cannot be
  selected is reported as `folder_absent` ([ADR 0025]), never as
  `uid_not_found`.
- **The saga resolves All Mail** through the map.
- The mock-gmail server rejects the English name on a localized
  account, as Gmail does, and a BDD scenario pins that.

## Consequences

### Positive

- Agents can use the canonical vocabulary they read in `list_folders`
  without a preceding call, and drafts can be discarded on localized
  accounts.
- A policy needs one entry per system folder. Two entries that disagree
  can no longer widen access through the choice of name.
- The error a caller sees names the actual problem.

### Negative

- One extra `LIST` per account and configuration generation.
- A policy whose duplicate entries differ stops granting access to that
  folder after the upgrade. The release notes call this out.
- A client that compared a response's folder field with the localized
  name it sent sees the canonical name instead.

### Neutral

- Non-Gmail accounts are unaffected; their folder names pass through.

## Security Implications

- **Attack surface:** unchanged; no new tool or input.
- **Trust boundaries:** unchanged.
- **Data exposure:** reduced. Before, a permissive entry under one name
  could override a restrictive entry under the other. Conflicts now fail
  closed.
- **Failure modes:** if `LIST` fails, the call fails with the IMAP error
  instead of guessing a name. A stale map after a server-side rename is
  cleared by `SIGHUP` reload or `list_folders`.

## Alternatives Considered

- **Canonical entry wins on conflict.** Rejected: a stricter localized
  entry would be ignored silently.
- **Strictest value per setting wins.** Rejected: safe but opaque; the
  effective policy would exist nowhere in writing.
- **Echo the name the caller sent.** Rejected: two vocabularies in
  responses, logs and traces for one folder.
- **Require `list_folders` first and document it.** Rejected: an
  intent-level surface ([ADR 0016]) must not depend on call order.

## References

- [ADR 0016], [ADR 0017], [ADR 0019], [ADR 0025]
- RFC 6154 (IMAP LIST special-use mailboxes)
- `bdd/features/providers/localized_gmail_resolution.feature`

[ADR 0016]: 0016-mcp-tool-set.md
[ADR 0017]: 0017-response-transparency-for-filtered-data.md
[ADR 0019]: 0019-gmail-label-semantics.md
[ADR 0025]: 0025-folder-path-contract-and-error-taxonomy.md
