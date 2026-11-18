# ADR 0044: Normalized Response Envelope

- **Status:** Accepted
- **Date:** 2026-11-17
- **Deciders:** Randy Nel Gupta

## Context

Earlier error-envelope rules ([ADR 0027](0027-error-envelope-and-tool-surface-versioning.md)) left three defects:

1. **Existence oracle via error taxonomy.** Addressing a non-existent UID returned `ALLOW + ERROR` (`uid_not_found`), whereas addressing an existing `NONE` message returned `DENY` (`sender_not_whitelisted` / `sender_blacklisted`), allowing UID enumeration in dark folders.
2. **Legacy top-level error fields.** Several handlers continued emitting flat `error_type`, `imap_response`, `missing_capability`, `required_scope`, and `granted_scope`.
3. **Caller-input reflection.** Raw upstream IMAP status lines risked echoing caller-controlled folder names or paths in `error.detail`.

## Decision

We enforce **three normalized JSON response shapes across all 27 tools**, **opaque `not_found` responses for missing, foreign, and `< METADATA` objects (with an explicit provenance-verified own-draft write exception)**, and **deterministic gate ordering**.

### 1. Three normalized response shapes

Every MCP tool response matches exactly one of three shapes, with all legacy flat error fields (`error_type`, `imap_response`, `missing_capability`, `forbidden_tags`, `required_scope`, `granted_scope`, `matched_rule_index`) forbidden:

1. **Policy refusal (`DENY`):** `decision: "DENY"`, `reason: "<deny_code>"`, plus `account` / canonical `folder` context; never `result` or `error`.
2. **Authorized success (`ALLOW + OK`):** `decision: "ALLOW"`, `result: "OK"`, `reason: "<allow_code>"`, plus tool payload; never `error`.
3. **Operational failure or opaque absence (`ALLOW + ERROR`):** `decision: "ALLOW"`, `result: "ERROR"`, `reason: "<allow_code>"`, `error: { "type": "<error_type>", "detail": "<sanitized server message <= 256 chars, never echoing caller input>" }`.

### 2. Deterministic gate order and opaque `not_found`

1. **Account gate:** Ungranted/unknown account -> `DENY` (`account_hidden`); `needs_rebootstrap` account -> `DENY` (`needs_rebootstrap`).
2. **Folder gate:** Canonical alias resolution ([ADR 0056](0056-canonical-folder-resolution-and-code-registry.md)), then `FolderPolicy` membership -> `DENY` (`folder_hidden`, identical whether the folder exists on IMAP or not). For two-folder tools (`move`, `copy`), evaluate source account/folder gate first, then target account/folder gate, then source capability, then target capability.
3. **Folder capability & visibility floor gate:** Missing capability -> `DENY` (`capability_missing`); folder `max_visibility` below tool/criteria floor -> `DENY` (`visibility_below_<LEVEL>`).
4. **Mailbox selection:** Authorized folder absent on IMAP -> `ALLOW + ERROR` (`folder_absent`); `SELECT`/`EXAMINE` rejected -> `ALLOW + ERROR` (`select_failed`).
5. **Object lookup & non-interference gate:**
   - Addressing a missing, foreign, or `eff_vis(m) < METADATA` `uid`, `draft_id`, or `tx_id` returns **`ALLOW + ERROR` with `error.type: "not_found"`** and constant `error.detail` (recording forensic cause `object_missing`, `message_not_granted`, `message_capped`, or `foreign_caller` only in the operator audit log).
   - **Provenance-verified own-draft write exception:** For `update_draft` and `discard_draft` on a `\Drafts` folder where the caller holds the required write capability (`draft_edit` or `move_out` + `\Trash` `accept_incoming`), an authentic draft owned by `caller_id` ([ADR 0042](0042-cryptographic-and-wal-provenance.md)) may be revised or discarded even when folder read visibility is `NONE` ([ADR 0045](0045-write-visibility-floors-and-flow-monotonicity.md)), without returning draft content. Reads (`get_draft`, `list_drafts`) still enforce their read visibility floors (`BODY` and `METADATA`).
6. **Object visibility & flow gate (when `eff_vis(m) >= METADATA`):** `eff_vis(m) < tool_min_vis` -> `DENY` (`visibility_below_<LEVEL>`); `eff_target(m) > eff_source(m)` -> `DENY` (`relocation_would_escalate`).

## Consequences

### Positive

- **Zero UID/handle existence oracle.** Missing, foreign, and `< METADATA` objects are indistinguishable to callers.
- **Consistent client parsing.** Every tool uses the same three envelope shapes and deterministic multi-folder priority.
- **Write-only drafts preserved.** Provenance-verified own drafts remain revisable/discardable under `NONE` folder visibility without read leakage.

### Negative

- Clients matching `uid_not_found`, `draft_not_found`, `sender_not_whitelisted`, or `missing_capability` must update to `3.0.0`.

### Neutral

- Closed code sets are maintained in `docs/contract/registry.yaml` ([ADR 0056](0056-canonical-folder-resolution-and-code-registry.md)).

## Security Implications

- **Attack surface.** Eliminates ad-hoc error fields and reflected caller strings in `error.detail`.
- **Trust boundaries.** Separates caller-facing `not_found` from operator-only `audit_detail_reason`.
- **Data exposure.** Closes UID-occupation, blacklist-membership, and foreign-handle side-channels.
- **Failure modes.** Unhandled runtime exceptions return generic error responses without stack traces while auditing `internal_error`.
- **Auditability.** Every `DENY`, `ALLOW + OK`, and `ALLOW + ERROR` writes a chained audit entry ([ADR 0048](0048-pre-effect-audit-and-test-boundary.md)).

## Alternatives Considered

- **Return `DENY` for missing UIDs as well.** Rejected: confuses legitimate callers when a message was moved concurrently by the mailbox owner.
- **Keep `sender_not_whitelisted` and `sender_blacklisted` on caller responses.** Rejected: leaks UID existence and rule classification on every probe.
- **Echo verbatim IMAP server error lines in `error.detail`.** Rejected: IMAP servers often echo mailbox names or arguments in `NO`/`BAD` text.

## References

- Supersedes [ADR 0027](0027-error-envelope-and-tool-surface-versioning.md).
- Resolves withdrawal of [LIM 0012](../limitations/0012-wire-response-transparency-uneven-across-tools.md).
- [ADR 0042](0042-cryptographic-and-wal-provenance.md) — draft provenance ownership.
- [ADR 0045](0045-write-visibility-floors-and-flow-monotonicity.md) — write visibility floors.
- [ADR 0056](0056-canonical-folder-resolution-and-code-registry.md) — canonical folder aliases and closed code registry.
