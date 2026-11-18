# LIM 0022: Externally edited drafts become non-editable by handle

- **Status:** Accepted
- **Resolution intent:** permanent (architectural boundary)
- **Date proposed:** 2026-09-18
- **Date approved:** 2026-11-17
- **Proposed by:** Architecture review
- **Approved by:** Randy Nel Gupta
- **Scope:** server/src/imap_mcp/handlers/drafts.py
- **Related ADRs:** [ADR-0042](../adr/0042-cryptographic-and-wal-provenance.md), [ADR-0045](../adr/0045-write-visibility-floors-and-flow-monotonicity.md)
- **Related Guidelines:** BDD Guidelines §4.2, §4.5

## Resolution intent

`permanent`. IMAP provides no in-place edit primitive and no standard cross-client draft identity. In `3.0.0`, [ADR-0042] closes the earlier silent-lost-update hazard completely: if an external mail client edits a draft, either it drops `X-SC-*` headers (`not_found`) or it alters the message content/recipients so `X-SC-Provenance`'s `canonical_digest` HMAC no longer matches (`draft_not_editable`). In neither case can `update_draft` silently overwrite a human's edit; the architectural boundary is solely that an externally re-saved draft can no longer be revised under the old `draft_id`.

## Context

[ADR-0042] binds every server-authored draft to `(caller_id, account_id, draft_id, revision, canonical_digest)` via `X-SC-Provenance` and the WAL provenance registry. When a human opens that draft in Gmail Web, Thunderbird, or Apple Mail and saves an edit, the foreign client appends a newly composed RFC 5322 message and deletes the previous one.

## Nature of the weakness

Once a human edits and saves an agent-created draft in an external mail client:
1. If the external client strips unknown `X-SC-*` headers (common in webmailers), `update_draft` and `discard_draft` for that `draft_id` return `ALLOW + ERROR` (`error.type: "not_found"`), and `list_drafts` shows the human-saved draft with `editable: false`.
2. If the external client preserves `X-SC-*` headers while changing body text, subject, or recipients, `X-SC-Provenance` HMAC verification fails because `canonical_digest` changed; `list_drafts` reports `editable: false` and `update_draft` returns `ALLOW + ERROR` (`error.type: "draft_not_editable"`).

## Why the clean solution is not chosen

No RFC 3501 / RFC 5322 mechanism obliges third-party MUAs to compute our server's HMAC or link a newly appended draft to a deleted predecessor. Refusing to overwrite a human-modified draft (`draft_not_editable` / `editable: false`) is the safe, data-preserving behaviour; an agent with `BODY` visibility can read the human-edited draft and create a fresh draft if asked.

## Mitigations in place

- **Silent lost updates eliminated ([ADR-0042]).** `X-SC-Provenance` covers `canonical_digest` (normalized headers + MIME part hashes), catching external edits even when `X-SC-Draft-Id` and `X-SC-Draft-Rev` survive intact.
- **Clear visibility in `list_drafts`.** Externally created or externally modified drafts appear with `editable: false`.
- **Deterministic error codes.** Stripped headers return `not_found`; preserved headers with modified content return `draft_not_editable` (`bdd/features/tool_surface/update_draft_errors.feature`).

## Residual risk

When a human edits an agent's draft in Gmail and then asks the agent to tweak another sentence of that same draft, the agent cannot edit the human-saved draft in place via `update_draft`; it must read the current draft (if policy grants `BODY`) and create a new draft via `create_draft`, leaving the human to delete the superseded draft manually.

## Triggers for revisit

- Standardization of cross-client mutable draft identity in JMAP/IMAP extensions adopted by Gmail and Thunderbird.

## References

- [ADR-0042](../adr/0042-cryptographic-and-wal-provenance.md) — `X-SC-Provenance` HMAC over `canonical_digest`.
- [ADR-0045](../adr/0045-write-visibility-floors-and-flow-monotonicity.md) — `update_draft` and `discard_draft` rules.
- RFC 3501, RFC 5322.
