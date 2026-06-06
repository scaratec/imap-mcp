"""Folder-level aggregate handler: folder_stats.

ADR 0025 differentiates the three folder-access outcomes (hidden /
absent / select-failed); ADR 0027 routes the latter two through the
unified ALLOW + ERROR envelope.
"""

from __future__ import annotations

from typing import Any, Literal, NotRequired, TypedDict, TYPE_CHECKING

from ..imap_core import folder_stats as imap_folder_stats
from ..policy import level_rank
from ._common import _password_for, _resolve_imap_folder, error_envelope

if TYPE_CHECKING:
    from ..context import ServerContext


class FolderStatsResponse(TypedDict, total=False):
    decision: Literal["ALLOW", "DENY"]
    result: NotRequired[Literal["OK", "ERROR"]]
    reason: NotRequired[str]
    error: NotRequired[dict[str, str]]
    account: str
    folder: str
    visibility_level: NotRequired[str]
    visible_count: NotRequired[int]
    hidden_count: NotRequired[int]


def _deny_folder_stats(*, reason: str, account: str, folder: str) -> FolderStatsResponse:
    return {"decision": "DENY", "reason": reason, "account": account, "folder": folder}


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
    outcome = await imap_folder_stats(account, password, imap_folder)
    if outcome.kind == "absent":
        envelope = error_envelope(
            error_type="folder_absent",
            reason=folder_decision.reason,
            extra={"account": account_id, "folder": folder_path},
        )
        return envelope  # type: ignore[return-value]
    if outcome.kind == "select_failed":
        envelope = error_envelope(
            error_type="select_failed",
            detail=outcome.imap_response,
            reason=folder_decision.reason,
            extra={"account": account_id, "folder": folder_path},
        )
        return envelope  # type: ignore[return-value]
    return {
        "decision": "ALLOW",
        "result": "OK",
        "reason": folder_decision.reason,
        "visibility_level": folder_decision.visibility,
        "account": account_id,
        "folder": folder_path,
        "visible_count": outcome.exists,
        "hidden_count": 0,
    }
