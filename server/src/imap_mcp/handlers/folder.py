"""Folder-level aggregate handler: folder_stats.

Adheres to:
- ADR 0043: Non-interference transparency — removal of hidden_count, static completeness
- ADR 0044: Normalized response envelope
- ADR 0056: Canonical folder resolution
- ADR 0062: Closed reason and error code registry
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Literal, NotRequired, TypedDict

from ..imap_core import (
    fetch_envelopes_batch as imap_fetch_envelopes_batch,
)
from ..imap_core import (
    folder_stats as imap_folder_stats,
)
from ..imap_core import (
    search_uids as imap_search_uids,
)
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
    visible_count: NotRequired[int]
    completeness: NotRequired[str]


def _deny_folder_stats(*, reason: str, account: str, folder: str) -> FolderStatsResponse:
    return {"decision": "DENY", "reason": reason, "account": account, "folder": folder}


async def handle_folder_stats(
    context: ServerContext, arguments: dict[str, Any]
) -> FolderStatsResponse:
    account_id = str(arguments["account"])
    folder_path = str(arguments["folder"])

    folder_decision = context.pdp.decide_folder_access(context.caller_id, account_id, folder_path)
    if not folder_decision.allowed:
        return _deny_folder_stats(
            reason=folder_decision.reason, account=account_id, folder=folder_path
        )
    assert folder_decision.folder_policy is not None
    fp = folder_decision.folder_policy

    max_vis = context.pdp.folder_max_visibility(fp)
    if level_rank(max_vis) < level_rank("COUNT"):
        return _deny_folder_stats(
            reason="visibility_below_COUNT", account=account_id, folder=folder_path
        )

    account, password = await _password_for(context, account_id)
    imap_folder = await _resolve_imap_folder(context, account_id, folder_path)
    outcome = await imap_folder_stats(account, password, imap_folder)

    if outcome.kind == "absent":
        envelope = error_envelope(
            error_type="folder_absent",
            detail="Folder does not exist on IMAP server",
            reason=folder_decision.reason,
            extra={"account": account_id, "folder": folder_path},
        )
        return envelope  # type: ignore[return-value]

    if outcome.kind == "select_failed":
        envelope = error_envelope(
            error_type="select_failed",
            detail=outcome.imap_response or "SELECT mailbox failed",
            reason=folder_decision.reason,
            extra={"account": account_id, "folder": folder_path},
        )
        return envelope  # type: ignore[return-value]

    may_hide = context.pdp.folder_may_hide(account_id, folder_path, fp)
    completeness = "policy_may_hide" if may_hide else "complete"

    visible_count = outcome.exists
    if may_hide:
        if fp.mode == "whitelist" and not fp.rules:
            visible_count = 0
        else:
            raw_uids = await imap_search_uids(account, password, imap_folder, "ALL")
            if not raw_uids:
                visible_count = 0
            else:
                from ..audit import _now_utc
                from ._common import _facts_from_envelope

                now_utc = _now_utc()
                envelopes = await imap_fetch_envelopes_batch(
                    account, password, imap_folder, raw_uids
                )
                count = 0
                for env in envelopes:
                    facts = _facts_from_envelope(env)
                    eff_vis, _ = context.pdp.evaluate_message_effective(
                        context.caller_id, account_id, folder_path, facts, now_utc=now_utc
                    )
                    if level_rank(eff_vis) >= level_rank("COUNT"):
                        count += 1
                visible_count = count

    return {
        "decision": "ALLOW",
        "result": "OK",
        "reason": folder_decision.reason,
        "account": account_id,
        "folder": folder_path,
        "visible_count": visible_count,
        "completeness": completeness,
    }
