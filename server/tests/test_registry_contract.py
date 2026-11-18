"""Unit tests for docs/contract/registry.yaml closure and contract integrity (ADR 0062)."""

from __future__ import annotations

from pathlib import Path

import yaml

REGISTRY_PATH = Path(__file__).resolve().parents[2] / "docs" / "contract" / "registry.yaml"

EXPECTED_ALLOW_REASONS = [
    "authenticated",
    "folder_default_applied",
    "rule_matched",
]

EXPECTED_DENY_REASONS = [
    "auth_failed",
    "unknown_tool",
    "account_hidden",
    "needs_rebootstrap",
    "folder_hidden",
    "policy_alias_conflict",
    "visibility_below_COUNT",
    "visibility_below_METADATA",
    "visibility_below_ENVELOPE",
    "visibility_below_HEADERS",
    "visibility_below_BODY",
    "visibility_below_FULL",
    "capability_missing",
    "forbidden_flag",
    "relocation_would_escalate",
    "recipient_not_allowed",
    "tool_not_applicable_for_provider",
]

EXPECTED_ERROR_TYPES = [
    "not_found",
    "folder_absent",
    "select_failed",
    "uidvalidity_changed",
    "append_rejected",
    "append_timeout",
    "append_failed",
    "empty_reply_text",
    "missing_message_id",
    "attachment_not_found",
    "sink_not_configured",
    "sink_not_writable",
    "draft_not_editable",
    "draft_revision_conflict",
    "draft_ambiguous",
    "trash_unavailable",
    "saga_aborted",
    "transient_imap_failure",
    "unsupported_imap_capability",
    "pool_exhausted",
    "secret_unavailable",
    "internal_error",
]

EXPECTED_AUDIT_INTERNAL_REASONS = [
    "day_roll",
    "retention_delete",
    "audit_file_missing",
    "saga_step",
    "ambiguous_target_match",
    "policy_reload_ok",
    "policy_reload_rejected",
    "token_refresh_ok",
    "token_refresh_failed",
    "oauth_bootstrap_ok",
    "pool_drain",
    "mutf7_decode_fallback",
    "identity_immutable",
    "invalid_arguments",
    "internal_error",
    "object_missing",
    "message_not_granted",
    "message_capped",
    "foreign_caller",
    "eof_day",
]

RETIRED_CODES = [
    "folder_not_found",
    "sender_not_whitelisted",
    "sender_blacklisted",
    "forbidden_system_flag",
    "uid_not_found",
    "draft_not_found",
    "provider_rejected",
    "rewrite_failed",
]


def test_registry_file_exists_and_declares_exact_sets() -> None:
    assert REGISTRY_PATH.is_file(), f"Registry file missing: {REGISTRY_PATH}"
    data = yaml.safe_load(REGISTRY_PATH.read_text(encoding="utf-8"))

    assert data.get("version") == "3.0.0"
    assert data.get("caller_allow_reasons") == EXPECTED_ALLOW_REASONS
    assert data.get("caller_deny_reasons") == EXPECTED_DENY_REASONS
    assert data.get("caller_error_types") == EXPECTED_ERROR_TYPES
    assert data.get("audit_internal_reasons") == EXPECTED_AUDIT_INTERNAL_REASONS

    all_declared = (
        set(data["caller_allow_reasons"])
        | set(data["caller_deny_reasons"])
        | set(data["caller_error_types"])
        | set(data["audit_internal_reasons"])
    )
    for retired in RETIRED_CODES:
        assert retired not in all_declared, f"Retired code {retired!r} found in registry"
