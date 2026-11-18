# ADR 0040: Message-Centric Policy Evaluation

- **Status:** Accepted
- **Date:** 2026-11-17
- **Deciders:** Randy Nel Gupta

## Context

`imap-mcp` mediates access by untrusted or prompt-injectable LLM agents to IMAP mailboxes containing mixed sensitive and routine correspondence. Earlier revisions ([ADR 0001](0001-default-deny-hierarchical-policy.md), [ADR 0003](0003-whitelist-blacklist-folder-modes.md), [ADR 0019](0019-gmail-label-semantics.md)) attached authorization strictly to `(account, folder)` tuples: the caller named a folder, and the Policy Decision Point (PDP) evaluated only that folder's rules.

Folder-only evaluation fails whenever one logical message is reachable through multiple views:

1. On Google accounts (`provider: google`), a message is a single object (`X-GM-MSGID`) carrying a set of labels (`X-GM-LABELS`), and `[Gmail]/All Mail` contains every non-trashed, non-spam message. Evaluating only the caller-chosen folder makes effective visibility the maximum across reachable views, allowing `[Gmail]/All Mail` or a secondary label to bypass a strict `INBOX` whitelist.
2. Conversely, requiring an unconfigured `[Gmail]/All Mail` to grant access would force every `INBOX` rule to be duplicated onto `[Gmail]/All Mail`.
3. Moving a Gmail message to `[Gmail]/Trash` or `[Gmail]/Spam` strips all active labels at once, bypassing `move_out: false` archive protections on other labels.
4. Exposing sequential `[Gmail]/All Mail` UIDs as `canonical_all_mail_uid` leaks hidden message density.

## Decision

We evaluate authorization on the **logical message object** under a default-deny hierarchy (`AccountPolicy -> FolderPolicy -> MessageRule[]`) with account-wide `blacklist` cap-overrides and primary-label precedence on Google accounts.

### 1. Default-deny hierarchy and folder modes

- Unlisted accounts deny with `account_hidden`; unlisted folders deny with `folder_hidden` before mailbox selection.
- `mode: whitelist` requires `default: NONE` and `grant` rules (`> NONE`); single-folder level is `max(NONE, max(matching grants))`.
- `mode: blacklist` requires `default > NONE` and `cap` rules; single-folder level is `min(default, min(matching caps))`.
- Mixing `grant` and `cap`, mismatched defaults, or unreachable `grant: NONE` rules fail configuration loading closed.

### 2. Multi-label composition on Google accounts (`provider: google`)

For a message `m` carrying canonicalized Gmail labels `L(m)`:

1. **Account-wide cap-overrides:** Every matching `cap` rule in any `blacklist` `FolderPolicy` configured on the account caps `m` across all folders (`eff(c, a, m) <= C`).
2. **Primary-label precedence over `[Gmail]/All Mail` and `[Gmail]/Starred`:** Let `L_specific(m)` be `L(m)` excluding `[Gmail]/All Mail`, `[Gmail]/Starred`, and `[Gmail]/Important`.
   - Addressing a folder `f` in `L_specific(m)` yields `V_folder(f, m)` capped by all account-wide `cap` rules.
   - Addressing `[Gmail]/All Mail` or `[Gmail]/Starred` when `L_specific(m)` is non-empty caps base visibility to the maximum granted to `m` across the caller's configured policies for `L_specific(m)` (`NONE` if none is granted). Archived mail (`L_specific(m)` empty) uses `[Gmail]/All Mail`'s own policy capped by account-wide caps.
3. **Opaque deduplication (`object_id`):** Google search and listing responses emit `object_id: "gm_<24hex>"` derived via keyed HMAC-SHA256 over `(caller_id, account_id, x_gm_msgid)`. `canonical_all_mail_uid` is removed.
4. **Policy-filtered labels:** `list_labels` and `fetch_envelope.labels` disclose only labels (sorted lexicographically) for which the caller holds a `FolderPolicy` with `max_visibility >= COUNT`, without raw or hidden counts.
5. **Global label-stripping moves:** Moving to `[Gmail]/Trash` or `[Gmail]/Spam` requires `move_out: true` on **every** label in `L_specific(m)`.

On `imap-standard` accounts, each mailbox copy `(account, folder, uidvalidity, uid)` is an independent object evaluated under its folder policy.

## Consequences

### Positive

- **Cross-view protection.** `[Gmail]/All Mail`, `[Gmail]/Starred`, and secondary labels cannot bypass a strict whitelist or an account-wide `blacklist` cap.
- **Archive immutability on Gmail.** Trashing a message carrying an immutable archive label is denied unless `move_out` holds on all its active labels.
- **Deduplication without UID leaks.** Callers correlate multi-label appearances via `object_id` without learning `[Gmail]/All Mail` UID gaps.

### Negative

- Google metadata batch fetches include `X-GM-MSGID` and `X-GM-LABELS`.
- Clients expecting `canonical_all_mail_uid` must migrate to `object_id`.

### Neutral

- Standard IMAP providers retain single-mailbox object semantics with zero Gmail wire extensions.

## Security Implications

- **Attack surface.** Centralizes multi-label composition inside the pure-function PDP.
- **Trust boundaries.** Enforces account and folder gates before mailbox selection and object gates before field projection.
- **Data exposure.** Eliminates `[Gmail]/All Mail` whitelist bypasses, UID spacing leaks, and hidden-label disclosures.
- **Failure modes.** Malformed policies fail closed at load time; missing `X-GM-LABELS` on superset views fails closed to `NONE`.
- **Auditability.** Every allow and deny logs the resolved `caller_id`, canonical `folder`, and categorical `reason` ([ADR 0048](0048-pre-effect-audit-and-test-boundary.md)).

## Alternatives Considered

- **Folder-local evaluation (2.x status quo).** Rejected: permits privilege escalation through `[Gmail]/All Mail` or permissive secondary labels.
- **Unconditional minimum across all labels including unconfigured `[Gmail]/All Mail`.** Rejected: forces every `INBOX` message to `NONE` unless `[Gmail]/All Mail` duplicates all rules.
- **Keep `canonical_all_mail_uid` for deduplication.** Rejected: sequential UID gaps reveal hidden mail volume and arrival timing.

## References

- Supersedes [ADR 0001](0001-default-deny-hierarchical-policy.md), [ADR 0003](0003-whitelist-blacklist-folder-modes.md), and [ADR 0019](0019-gmail-label-semantics.md).
- [ADR 0041](0041-direction-aware-rule-grammar-and-provenance.md) — rule predicate grammar.
- [ADR 0043](0043-non-interference-transparency-and-search.md) — visibility scale and non-interference.
- [ADR 0056](0056-canonical-folder-resolution-and-code-registry.md) — canonical folder alias resolution.
