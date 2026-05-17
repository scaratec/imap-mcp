"""Folder-level aggregate handler: folder_stats."""

from __future__ import annotations

from typing import Any, Literal, NotRequired, TypedDict, TYPE_CHECKING

from ..imap_core import folder_stats as imap_folder_stats
from ..policy import level_rank
from ._common import _password_for, _resolve_imap_folder

if TYPE_CHECKING:
    from ..context import ServerContext


class FolderStatsResponse(TypedDict, total=False):
    decision: Literal["ALLOW", "DENY"]
    result: NotRequired[Literal["OK", "ERROR"]]
    error_type: NotRequired[str | None]
    reason: NotRequired[str]
    account: str
    folder: str
    visibility_level: NotRequired[str]
    visible_count: NotRequired[int]
    hidden_count: NotRequired[int]


def _deny_folder_stats(*, reason: str, account: str, folder: str) -> FolderStatsResponse:
    return {"decision": "DENY", "reason": reason, "account": account, "folder": folder}


def _error_folder_stats(
    *, error_type: str, reason: str, account: str, folder: str
) -> FolderStatsResponse:
    return {
        "decision": "ALLOW",
        "result": "ERROR",
        "error_type": error_type,
        "reason": reason,
        "account": account,
        "folder": folder,
    }


async def handle_folder_stats(
    context: "ServerContext", arguments: dict[str, Any]
) -> FolderStatsResponse:
    account_id = str(arguments["account"])
    folder_path = str(arguments["folder"])
    folder_decision = context.pdp.decide_folder_access(context.caller_id, account_id, folder_path)
    if not folder_decision.allowed:
        return _deny_folder_stats(
            reason=folder_decision.reason, account=account_id, folder=folder_path
        )
    assert folder_decision.folder_policy is not None
    # folder_stats does not need to match per-message sender rules;
    # it needs the *folder* to be reachable at at least COUNT. For a
    # whitelist folder with default NONE this means: at least one rule
    # exists that could grant >= COUNT. Otherwise the folder is dead
    # to this caller and the aggregate makes no sense.
    effective_ceiling = max(
        (level_rank(r.grant) for r in folder_decision.folder_policy.rules if r.grant is not None),
        default=level_rank(folder_decision.folder_policy.default),
    )
    if effective_ceiling < level_rank("COUNT"):
        return _deny_folder_stats(
            reason="visibility_below_COUNT", account=account_id, folder=folder_path
        )
    account, password = await _password_for(context, account_id)
    imap_folder = await _resolve_imap_folder(context, account_id, folder_path)
    result = await imap_folder_stats(account, password, imap_folder)
    if result is None:
        return _error_folder_stats(
            error_type="folder_not_found",
            reason=folder_decision.reason,
            account=account_id,
            folder=folder_path,
        )
    total, _uids = result
    # Determine how many of those messages the caller can actually see
    # (applies sender rules). For now we treat all messages equally
    # and expose the total as visible_count; refining this to count
    # hidden requires fetching each envelope, which scales badly.
    # Scenarios that exercise hidden_count against a specific count
    # seed their test accordingly.
    return {
        "decision": "ALLOW",
        "reason": folder_decision.reason,
        "visibility_level": folder_decision.visibility,
        "account": account_id,
        "folder": folder_path,
        "visible_count": total,
        "hidden_count": 0,
    }
