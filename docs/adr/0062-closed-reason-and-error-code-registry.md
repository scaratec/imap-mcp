# ADR 0062: Closed Reason and Error Code Registry

- **Status:** Accepted
- **Date:** 2026-11-17
- **Deciders:** Randy Nel Gupta

## Context

In 2.x, reason codes and error types were listed across prose tables in multiple ADRs ([ADR 0017](0017-response-transparency-for-filtered-data.md), [ADR 0025](0025-folder-path-contract-and-error-taxonomy.md), [ADR 0027](0027-error-envelope-and-tool-surface-versioning.md)) while handlers and features accumulated over fifteen undeclared codes, and unexpected runtime exceptions lacked a specified caller-facing `error.type`.

## Decision

We maintain **one machine-readable contract registry (`docs/contract/registry.yaml`) as the closed single source of truth for all caller `ALLOW` reasons, caller `DENY` reasons, caller `error.type` codes (including `internal_error` for unexpected runtime exceptions), and internal audit/handshake reasons**.

### Complete 3.0.0 registry sets

- **Caller `ALLOW` reasons (3):** `authenticated`, `folder_default_applied`, `rule_matched`.
- **Caller `DENY` reasons (17):** `auth_failed`, `unknown_tool`, `account_hidden`, `needs_rebootstrap`, `folder_hidden`, `policy_alias_conflict`, `visibility_below_COUNT`, `visibility_below_METADATA`, `visibility_below_ENVELOPE`, `visibility_below_HEADERS`, `visibility_below_BODY`, `visibility_below_FULL`, `capability_missing`, `forbidden_flag`, `relocation_would_escalate`, `recipient_not_allowed`, `tool_not_applicable_for_provider`.
- **Caller `error.type` codes (22):** `not_found`, `folder_absent`, `select_failed`, `uidvalidity_changed`, `append_rejected`, `append_timeout`, `append_failed`, `empty_reply_text`, `missing_message_id`, `attachment_not_found`, `sink_not_configured`, `sink_not_writable`, `draft_not_editable`, `draft_revision_conflict`, `draft_ambiguous`, `trash_unavailable`, `saga_aborted`, `transient_imap_failure`, `unsupported_imap_capability`, `pool_exhausted`, `secret_unavailable`, `internal_error`.
  - When an unhandled runtime exception occurs inside an authorized tool execution, the server catches it, writes a chained `internal_error` audit record with `error_class` ([ADR 0048](0048-pre-effect-audit-and-test-boundary.md)), and returns `ALLOW + ERROR` with `error.type: "internal_error"` and a fixed generic `error.detail` (`"Internal server error"`, never echoing the exception message).
- **Audit/handshake-only internal reasons (20):** `day_roll`, `retention_delete`, `audit_file_missing`, `saga_step`, `ambiguous_target_match`, `policy_reload_ok`, `policy_reload_rejected`, `token_refresh_ok`, `token_refresh_failed`, `oauth_bootstrap_ok`, `pool_drain`, `mutf7_decode_fallback`, `identity_immutable`, `invalid_arguments`, `internal_error`, `object_missing`, `message_not_granted`, `message_capped`, `foreign_caller`, `eof_day`.

Retired 2.x codes (`folder_not_found`, `sender_not_whitelisted`, `sender_blacklisted`, `forbidden_system_flag`, `uid_not_found`, `draft_not_found`, `provider_rejected`, `rewrite_failed`) are permanently excluded.

## Consequences

### Positive

- **Machine-enforced closure.** Unit tests and BDD contract scenarios load `docs/contract/registry.yaml` directly; any undocumented code fails CI immediately.
- **Complete exception envelope.** Unexpected runtime failures conform to the normalized `ALLOW + ERROR` envelope (`error.type: "internal_error"`) without leaking exception strings.

### Negative

- Introducing a new reason or error code requires a registry update and an ADR decision.

### Neutral

- Structural token symmetry in black-box contract assertions is bounded by [LIM 0024](../limitations/0024-reason-code-symmetry-in-contract-tests.md).

## Security Implications

- **Attack surface.** Prevents ad-hoc handler error strings from creating unintended side-channels.
- **Trust boundaries.** Separates caller-facing codes from forensic `audit_detail_reason` codes (`message_not_granted`, `message_capped`, `foreign_caller`, `object_missing`).
- **Data exposure.** Fixed `"Internal server error"` detail on `internal_error` prevents stack traces or exception messages containing mail fragments from reaching callers.
- **Failure modes.** Unregistered codes fail contract tests loudly.
- **Auditability.** Every audit entry's `reason` and `error_type` belongs to `docs/contract/registry.yaml`.

## Alternatives Considered

- **Keep canonical code lists in prose tables only.** Rejected: prose tables drifted repeatedly during 2.x development.
- **Generate the registry automatically by scraping Python source enums.** Rejected: makes implementation self-validating instead of checking implementation against an independent contract artefact.
- **Surface unhandled runtime exceptions as JSON-RPC `-32603` without normalized envelope.** Rejected: breaks uniform `ALLOW + ERROR` handling for authorized tool calls ([ADR 0044](0044-normalized-envelope-and-code-registry.md)).

## References

- [ADR 0044](0044-normalized-envelope-and-code-registry.md) — normalized response envelope.
- [ADR 0048](0048-pre-effect-audit-and-test-boundary.md) — audit format and `internal_error` logging.
- [ADR 0056](0056-canonical-folder-resolution-and-code-registry.md) — canonical folder resolution.
- [LIM 0024](../limitations/0024-reason-code-symmetry-in-contract-tests.md).
