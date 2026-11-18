"""Introspection handlers: describe_policy, get_caller_identity,
get_transaction_status, tool_surface_info.

Adheres to:
- ADR 0043: Non-interference transparency — removal of hidden counts and sender_rules_count
- ADR 0044: Normalized response envelope
- ADR 0046: Read-only caller-bound transaction status queries
- ADR 0052: Tool Surface 3.0.0
- ADR 0062: Closed reason and error code registry
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, NotRequired, TypedDict

from ._common import (
    META_TOOLS,
    READ_TOOL_MIN_VIS,
    TOOL_SET_VERSION,
    WRITE_TOOL_CAP,
    error_envelope,
)

if TYPE_CHECKING:
    from ..context import ServerContext


class CallerIdentityResponse(TypedDict):
    decision: str
    result: str
    reason: str
    caller_id: str


class TransactionStatusResponse(TypedDict, total=False):
    decision: str
    result: str
    reason: str
    error: NotRequired[dict[str, str]]
    tx_id: str
    status: NotRequired[str]
    state: NotRequired[str]
    src_account: NotRequired[str]
    src_folder: NotRequired[str]
    src_uid: NotRequired[int]
    dst_account: NotRequired[str]
    dst_folder: NotRequired[str]
    message_id: NotRequired[str]
    retry_count: NotRequired[int]


class FolderVisibilityEntry(TypedDict):
    path: str
    mode: str
    default_visibility: str
    max_visibility: str
    may_hide: bool
    capabilities: list[str]
    has_draft_recipient_restrictions: bool


class AccountVisibilityEntry(TypedDict):
    id: str
    semantics: str
    token_cache: Any
    folders_visible: list[FolderVisibilityEntry]


class DescribePolicyResponse(TypedDict):
    decision: str
    result: str
    reason: str
    caller_id: str
    tool_set_version: str
    accounts: list[AccountVisibilityEntry]
    tool_set_available: list[str]


def _granted_caps(fp: Any) -> list[str]:
    caps: list[str] = []
    for key in (
        "mark_seen",
        "mark_tagged",
        "move_out",
        "copy_out",
        "accept_incoming",
        "draft_append",
        "draft_edit",
    ):
        if getattr(fp, key, False):
            caps.append(key)
    return caps


def handle_get_caller_identity(context: ServerContext) -> CallerIdentityResponse:
    return {
        "decision": "ALLOW",
        "result": "OK",
        "reason": "authenticated",
        "caller_id": context.caller_id,
    }


async def handle_get_transaction_status(
    context: ServerContext, arguments: dict[str, Any]
) -> TransactionStatusResponse:
    """Read-only transaction status query bound to caller_id (ADR 0046)."""
    tx_id = str(arguments["tx_id"])
    if context.saga is None:
        return error_envelope(  # type: ignore[return-value]
            error_type="not_found",
            detail="Transaction not found",
            reason="authenticated",
            extra={"tx_id": tx_id},
        )
    row = context.saga.wal.get(tx_id)
    if row is None or row.get("caller_id") != context.caller_id:
        return error_envelope(  # type: ignore[return-value]
            error_type="not_found",
            detail="Transaction not found",
            reason="authenticated",
            extra={"tx_id": tx_id},
        )
    return {
        "decision": "ALLOW",
        "result": "OK",
        "reason": "authenticated",
        "tx_id": tx_id,
        "state": row["status"],
        "status": row["status"],
        "src_account": row["src_account"],
        "src_folder": row["src_folder"],
        "src_uid": row["src_uid"],
        "dst_account": row["dst_account"],
        "dst_folder": row["dst_folder"],
        "message_id": row["message_id"],
        "retry_count": row["retry_count"],
    }


async def handle_describe_policy(
    context: ServerContext, arguments: dict[str, Any]
) -> DescribePolicyResponse:
    _ = arguments
    config = context.configuration
    caller = config.caller_by_id(context.caller_id)
    policy = config.policy_by_name(caller.policy) if caller is not None else None
    granted_accounts = set(policy.accounts.keys()) if policy is not None else set()

    visible_accounts: list[AccountVisibilityEntry] = []
    for account in config.accounts_file.accounts:
        if account.id not in granted_accounts:
            continue
        folder_policies = policy.accounts.get(account.id, []) if policy else []
        folders_visible: list[FolderVisibilityEntry] = []
        for fp in folder_policies:
            max_vis = context.pdp.folder_max_visibility(fp)
            may_hide = context.pdp.folder_may_hide(account.id, fp.path, fp)
            has_recip_restrictions = bool(
                fp.draft_recipients
                and (fp.draft_recipients.allowed_domains or fp.draft_recipients.allowed_addresses)
            )
            folders_visible.append(
                {
                    "path": fp.path,
                    "mode": fp.mode,
                    "default_visibility": fp.default,
                    "max_visibility": max_vis,
                    "may_hide": may_hide,
                    "capabilities": _granted_caps(fp),
                    "has_draft_recipient_restrictions": has_recip_restrictions,
                }
            )

        visible_accounts.append(
            {
                "id": account.id,
                "semantics": "gmail-labels"
                if account.provider in ("google", "google-mock")
                else "imap-standard",
                "token_cache": account.token_cache,
                "folders_visible": folders_visible,
            }
        )

    all_tools = sorted(set(READ_TOOL_MIN_VIS.keys()) | set(WRITE_TOOL_CAP.keys()) | set(META_TOOLS))

    return {
        "decision": "ALLOW",
        "result": "OK",
        "reason": "authenticated",
        "caller_id": context.caller_id,
        "tool_set_version": TOOL_SET_VERSION,
        "accounts": visible_accounts,
        "tool_set_available": all_tools,
    }


_BREAKING_CHANGES_LOG = [
    {
        "version": "1.0.0",
        "summary": "V1 core grammar and envelope refactor (ADR 0024-0027).",
    },
    {
        "version": "2.0.0",
        "summary": "Attachment file sink delivery (ADR 0028).",
    },
    {
        "version": "3.0.0",
        "summary": (
            "Complete Tool Surface 3.0.0 redesign (ADR 0040-0064): message-centric "
            "policy, non-interference transparency, cryptographic draft provenance, "
            "and closed reason code registry."
        ),
    },
]


def handle_tool_surface_info(context: ServerContext) -> dict[str, Any]:
    from ..context import _package_version

    return {
        "decision": "ALLOW",
        "result": "OK",
        "reason": "authenticated",
        "tool_set_version": TOOL_SET_VERSION,
        "package_version": _package_version(),
        "protocol_revision": "2026-11-17",
        "breaking_changes_since": list(_BREAKING_CHANGES_LOG),
    }
