"""Introspection handlers: describe_policy, get_caller_identity,
get_transaction_status, plus policy-projection helpers."""

from __future__ import annotations

from typing import Any, NotRequired, TypedDict, TYPE_CHECKING

from ..policy import level_rank
from ._common import READ_TOOL_MIN_VIS, TOOL_SET_VERSION, WRITE_TOOL_CAP

if TYPE_CHECKING:
    from ..context import ServerContext


class CallerIdentityResponse(TypedDict):
    caller_id: str


class TransactionStatusResponse(TypedDict, total=False):
    tx_id: str
    state: str
    reason: NotRequired[str]
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
    capabilities: list[str]
    sender_rules_count: int


class AccountVisibilityEntry(TypedDict):
    id: str
    semantics: str
    token_cache: Any
    folders_visible: list[FolderVisibilityEntry]
    hidden_folders_count: int


class DescribePolicyResponse(TypedDict):
    caller_id: str
    tool_set_version: str
    accounts: list[AccountVisibilityEntry]
    hidden_accounts_count: int
    tool_set_available: list[str]


def _max_visibility(fp: "Any") -> str:
    default_rank = level_rank(fp.default)
    best = default_rank
    for rule in fp.rules:
        if rule.grant is not None:
            best = max(best, level_rank(rule.grant))
    for level in ("NONE", "COUNT", "METADATA", "ENVELOPE", "HEADERS", "BODY", "FULL"):
        if level_rank(level) == best:  # type: ignore[arg-type]
            return level
    return "NONE"


def _granted_caps(fp: "Any") -> list[str]:
    caps: list[str] = []
    for key in (
        "mark_seen",
        "mark_tagged",
        "move_out",
        "accept_incoming",
        "draft_append",
        "modify_message",
    ):
        if getattr(fp, key, False):
            caps.append(key)
    return caps


def handle_get_caller_identity(context: "ServerContext") -> CallerIdentityResponse:
    return {"caller_id": context.caller_id}


async def handle_get_transaction_status(
    context: "ServerContext", arguments: dict[str, Any]
) -> TransactionStatusResponse:
    tx_id = str(arguments["tx_id"])
    if context.saga is None:
        return {"tx_id": tx_id, "state": "unknown", "reason": "saga_not_configured"}
    row = context.saga.wal.get(tx_id)
    if row is None:
        return {"tx_id": tx_id, "state": "unknown"}
    # Opportunistic recovery: if the tx is non-terminal, attempt one
    # resume pass before reporting state. ADR 0007 §recovery.
    if row["status"] in ("pending", "staged"):
        try:
            await context.saga.resume(row)
        except Exception:
            pass
        row = context.saga.wal.get(tx_id) or row
    return {
        "tx_id": tx_id,
        "state": row["status"],
        "src_account": row["src_account"],
        "src_folder": row["src_folder"],
        "src_uid": row["src_uid"],
        "dst_account": row["dst_account"],
        "dst_folder": row["dst_folder"],
        "message_id": row["message_id"],
        "retry_count": row["retry_count"],
    }


async def handle_describe_policy(
    context: "ServerContext", arguments: dict[str, Any]
) -> DescribePolicyResponse:
    _ = arguments  # extra arguments are ignored deliberately (ADR 0018)
    from ..config import Configuration

    config: Configuration = context.configuration  # type: ignore[assignment]
    caller = config.caller_by_id(context.caller_id)
    policy = config.policy_by_name(caller.policy) if caller is not None else None
    granted_accounts = set(policy.accounts.keys()) if policy is not None else set()
    all_accounts = [a.id for a in config.accounts_file.accounts]
    visible_accounts: list[AccountVisibilityEntry] = []
    for account in config.accounts_file.accounts:
        if account.id not in granted_accounts:
            continue
        folder_policies = policy.accounts.get(account.id, []) if policy else []
        folders_visible: list[FolderVisibilityEntry] = []
        for fp in folder_policies:
            folders_visible.append(
                {
                    "path": fp.path,
                    "mode": fp.mode,
                    "default_visibility": fp.default,
                    "max_visibility": _max_visibility(fp),
                    "capabilities": _granted_caps(fp),
                    "sender_rules_count": len(fp.rules),
                }
            )
        # Count hidden folders as total IMAP folders minus those in policy.
        hidden_folders = 0
        try:
            from ..imap_core import list_folders as _list_folders

            all_folder_infos = await _list_folders(
                account,
                context.secret_store.get(account.auth.password_secret_ref() if account.auth else "")
                or "",
            )
            visible_paths = {fp.path for fp in folder_policies}
            hidden_folders = len([fi for fi in all_folder_infos if fi.path not in visible_paths])
        except Exception:
            hidden_folders = 0
        visible_accounts.append(
            {
                "id": account.id,
                "semantics": "gmail-labels"
                if account.provider in ("google", "google-mock")
                else "imap-standard",
                "token_cache": account.token_cache,
                "folders_visible": folders_visible,
                "hidden_folders_count": hidden_folders,
            }
        )
    hidden_accounts = len(all_accounts) - len(visible_accounts)
    return {
        "caller_id": context.caller_id,
        "tool_set_version": TOOL_SET_VERSION,
        "accounts": visible_accounts,
        "hidden_accounts_count": hidden_accounts,
        "tool_set_available": list(READ_TOOL_MIN_VIS.keys())
        + list(WRITE_TOOL_CAP.keys())
        + [
            "describe_policy",
            "get_caller_identity",
            "get_transaction_status",
            "tool_surface_info",
        ],
    }


# --------------------------------------------------------------------- ADR 0027


_BREAKING_CHANGES_LOG = [
    {
        "version": "1.0.0",
        "summary": (
            "criteria + folder-path + envelope refactor (ADR 0024-0027): "
            "duration grammar single source, canonical folder paths with "
            "three-code error taxonomy, normalized error envelope, "
            "list_attachments split, bulk_mark_tagged, explicit scope arg."
        ),
    }
]


def handle_tool_surface_info(context: "ServerContext") -> dict[str, Any]:
    """Return the contract-version envelope per ADR 0027 §2.

    The same metadata is also injected into the MCP `serverInfo`
    instructions block at handshake time so a client can pin before any
    `tools/call`.
    """
    from ..context import _package_version

    return {
        "decision": "ALLOW",
        "result": "OK",
        "reason": "folder_default_applied",
        "tool_set_version": TOOL_SET_VERSION,
        "package_version": _package_version(),
        "protocol_revision": "2024-11-05",
        "breaking_changes_since": list(_BREAKING_CHANGES_LOG),
    }
