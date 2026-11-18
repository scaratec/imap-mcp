# ADR 0057: Immutable Received Mail

- **Status:** Accepted
- **Date:** 2026-11-17
- **Deciders:** Randy Nel Gupta

## Context

During 2.x development, three message-rewriting tools (`add_attachment`, `replace_attachment`, `delete_attachment`) and a `modify_message` folder capability were added by accretion without a dedicated ADR. Executing those tools on received emails appended a modified copy and permanently expunged the original message:

1. **Evidentiary and compliance hazard.** Rewriting received invoices, contracts, or correspondence alters original MIME content, resets `INTERNALDATE`, and destroys the authentic original via `EXPUNGE`.
2. **Self-escalation vector.** Adding or removing attachments altered `has_attachment` and `size_bytes`, which participate in policy rules ([ADR 0041](0041-direction-aware-rule-grammar-and-provenance.md)).
3. **Redundant with own-draft composition.** Legitimate attachment assembly belongs exclusively on drafts being composed by the agent before a human sends them ([ADR 0063](0063-structured-own-draft-composition-and-revision.md)).

## Decision

We treat **all received mail as strictly immutable**: `modify_message`, `add_attachment`, `replace_attachment`, and `delete_attachment` are permanently abolished.

### Invariants

1. **Zero content-rewrite primitives on received messages:** No MCP tool may alter the headers, body text, HTML, MIME structure, `INTERNALDATE`, or attachments of any non-draft message.
2. **Policy loader rejection:** Declaring `modify_message` in a policy YAML file fails configuration loading closed ([ADR 0045](0045-write-visibility-floors-and-flow-monotonicity.md), [ADR 0049](0049-single-caller-boundary-and-validated-loading.md)).
3. **Protocol boundary rejection:** Invoking `add_attachment`, `replace_attachment`, or `delete_attachment` returns JSON-RPC `-32601 Unknown tool` and writes an `unknown_tool` `DENY` audit record ([ADR 0052](0052-tool-surface-3-0-0-and-non-goals.md)).

## Consequences

### Positive

- **Unbreakable record integrity.** Received correspondence and accounting documents can never be forged, stripped, or silently replaced through `imap-mcp`.
- **Smaller attack surface.** Eliminates three multi-step rewrite code paths.

### Negative

- Callers from `2.x` that added attachments to drafts via `add_attachment` must pass structured `attachments` to `create_draft` or `update_draft` ([ADR 0063](0063-structured-own-draft-composition-and-revision.md)).

### Neutral

- Non-content metadata mutations (`\Seen` via `mark_seen`, user keywords via `mark_tagged`, and folder relocation via `move`) remain governed by [ADR 0045](0045-write-visibility-floors-and-flow-monotonicity.md).

## Security Implications

- **Attack surface.** Permanently removes message-forgery and `INTERNALDATE`-reset primitives.
- **Trust boundaries.** Preserves the mailbox as an authentic record of inbound and sent communications.
- **Data exposure.** Prevents callers from manipulating `has_attachment` or `size_bytes` on received messages to trigger permissive policy rules.
- **Failure modes.** Retired capability keys fail startup/reload; retired tool names fail with `-32601`.
- **Auditability.** Attempted calls to retired rewrite tools are logged with `reason: "unknown_tool"` ([ADR 0048](0048-pre-effect-audit-and-test-boundary.md)).

## Alternatives Considered

- **Keep `add/replace/delete_attachment` gated on `FULL` + `modify_message`.** Rejected: even with `FULL` read access, a prompt-injected agent must never be able to falsify received invoices or legal mail.
- **Move original received mail to `\Trash` instead of expunging on rewrite.** Rejected: still plants a falsified message bearing original flags inside the active folder.
- **Restrict `add/replace/delete_attachment` to `\Drafts` by UID.** Rejected: drafts are addressed by opaque `draft_id` handles, not UIDs ([ADR 0042](0042-cryptographic-and-wal-provenance.md), [ADR 0063](0063-structured-own-draft-composition-and-revision.md)).

## References

- [ADR 0042](0042-cryptographic-and-wal-provenance.md) — cryptographic draft provenance.
- [ADR 0045](0045-write-visibility-floors-and-flow-monotonicity.md) — seven folder capabilities.
- [ADR 0052](0052-tool-surface-3-0-0-and-non-goals.md) — Tool Surface 3.0.0 non-goal inventory.
- [ADR 0063](0063-structured-own-draft-composition-and-revision.md) — structured own-draft composition and attachments.
