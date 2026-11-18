# ADR 0045: Per-Message Write Visibility Floors and Flow Monotonicity

- **Status:** Accepted
- **Date:** 2026-11-17
- **Deciders:** Randy Nel Gupta

## Context

In [ADR 0005](0005-per-folder-write-capabilities.md), write capabilities were evaluated purely at the folder level, orthogonal to per-message read visibility:

1. **Read-up by relocation.** A caller could `move` a `NONE` message (or `copy` it from a deposit-only archive needing only `accept_incoming` on the target) into a `FULL` folder and read its body and attachments.
2. **Self-escalation via `\Flagged` and Gmail labels.** `mark_tagged` allowed setting `\Flagged` and arbitrary Gmail labels, enabling an agent to star a `NONE` message and trigger `flagged: true -> FULL`.
3. **Unrestricted outbound draft recipients.** A prompt-injected agent with `draft_append` or `draft_edit` could address `To`/`Cc`/`Bcc` to an external exfiltration address.

## Decision

We enforce **seven boolean folder capabilities**, **per-message read visibility floors on writes (`Write <= Read`)**, **monotonic visibility across relocations (`eff_target <= eff_source`)**, **read-only `\Flagged` and system flags**, and **optional `draft_recipients` allowlists**.

### 1. Seven folder capabilities and per-message floors

`modify_message` is abolished and `copy_out` is added, yielding seven folder booleans (all defaulting to `false`):

| Capability | Governing tools | Per-message floor & invariant |
|---|---|---|
| `mark_seen` | `mark_seen`, `bulk_mark_seen` | `eff_source(m) >= METADATA` |
| `mark_tagged` | `mark_tagged`, `bulk_mark_tagged` | `eff_source(m) >= ENVELOPE`; user keywords only (all `\`-prefixed system flags including `\Flagged` refused on `add`, `remove`, and `replace` with `DENY / forbidden_flag`). On Google accounts, adding a label requires a `FolderPolicy` with `accept_incoming: true` and `eff_target <= eff_source`; removing a label requires `move_out: true` |
| `move_out` | `move`, `discard_draft` | `move`: source `move_out` + target `accept_incoming`, `eff_source(m) >= ENVELOPE`, and `eff_target(m) <= eff_source(m)` (`DENY / relocation_would_escalate` otherwise). Google `\Trash`/`\Spam` requires `move_out` on all specific labels ([ADR 0040](0040-message-centric-policy-and-gmail-composition.md)) |
| `copy_out` | `copy` | Source `copy_out` + target `accept_incoming`, `eff_source(m) >= ENVELOPE`, and `eff_target(m) <= eff_source(m)` (`DENY / relocation_would_escalate` otherwise) |
| `accept_incoming` | target of `move`, `copy`, `discard_draft`, Gmail label-add | Destination folder must grant `accept_incoming: true` |
| `draft_append` | `create_draft`, `create_reply_draft` | Allowed only on `\Drafts` folders; `create_reply_draft` additionally requires `eff_source(m) >= BODY` ([ADR 0057](0057-immutable-received-mail-and-structured-drafts.md)) |
| `draft_edit` | `update_draft` | Allowed only on `\Drafts` folders for provenance-verified own drafts ([ADR 0042](0042-cryptographic-and-wal-provenance.md), [ADR 0044](0044-normalized-envelope-and-code-registry.md)) |

In `bulk_mark_seen` and `bulk_mark_tagged`, messages below `max(tool_floor, req_vis(criteria))` ([ADR 0043](0043-non-interference-transparency-and-search.md)) are untouched on IMAP and excluded from `marked_count` / `tagged_count`. Both `marked_count` and `tagged_count` report the deterministic count of authorized visible messages matching `criteria` on which the store operation was applied (idempotent across repeated identical calls).

### 2. Flow monotonicity and outbound `draft_recipients` guardrail

- **Flow monotonicity:** Before `move`, `copy`, or Gmail label addition, the PDP computes `eff_source(m)` and `eff_target(m)` (in the destination folder/account). If `eff_target(m) > eff_source(m)`, the call is refused before mutation with `DENY / relocation_would_escalate`.
- **Outbound `draft_recipients` allowlist:** A `\Drafts` `FolderPolicy` may declare `draft_recipients: { allowed_domains: [...], allowed_addresses: [...] }`. Every parsed recipient in `To ∪ Cc ∪ Bcc` of `create_draft` (structured or `rfc822`), `create_reply_draft`, and the resulting draft of `update_draft` must match; otherwise the call is denied with `DENY / recipient_not_allowed`.

## Consequences

### Positive

- **No read-up by relocation or flag mutation.** Neither `move`, `copy`, nor `mark_tagged` can elevate message visibility or set `\Flagged`.
- **Trustworthy human star signal.** Starring mail in Gmail or Thunderbird remains an unforgeable human-only grant signal.
- **One-way deposit archives and bounded draft recipients.** `copy_out: false` keeps archives write-only, and `draft_recipients` blocks `Bcc` exfiltration.

### Negative

- Policies permitting `copy` must add `copy_out: true`; triage policies moving mail must grant at least `ENVELOPE` on routable messages.

### Neutral

- Draft composition, reply quoting, and handle-based discard mechanics are specified in [ADR 0057](0057-immutable-received-mail-and-structured-drafts.md).

## Security Implications

- **Attack surface.** Blocks all `\`-system flags in `mark_tagged` across `add`, `remove`, and `replace` modes.
- **Trust boundaries.** Enforces monotone information flow across folders and accounts.
- **Data exposure.** Prevents copy-washing from archives and recipient exfiltration via `To`/`Cc`/`Bcc`.
- **Failure modes.** Violations deny cleanly before any IMAP write.
- **Auditability.** All write intents and capability/flow/recipient refusals are audited ([ADR 0048](0048-pre-effect-audit-and-test-boundary.md)).

## Alternatives Considered

- **Allow `\Flagged` in `mark_tagged` and ban `flagged` from `grant` rules.** Rejected: starring messages is the owner's primary ad-hoc approval gesture.
- **Require `FULL` visibility for every `move`.** Rejected: `ENVELOPE` suffices for triage when `eff_target <= eff_source` blocks elevation.
- **Keep `copy` gated only on target `accept_incoming`.** Rejected: breaks deposit-only archive confidentiality.

## References

- Supersedes [ADR 0005](0005-per-folder-write-capabilities.md).
- [ADR 0040](0040-message-centric-policy-and-gmail-composition.md) — effective message visibility.
- [ADR 0044](0044-normalized-envelope-and-code-registry.md) — gate priority and write-only own-draft rule.
- [ADR 0057](0057-immutable-received-mail-and-structured-drafts.md) — draft composition, reply isolation, and discard.
