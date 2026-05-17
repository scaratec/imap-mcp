"""Flag/keyword mutation handlers: mark_seen, bulk_mark_seen, mark_tagged."""

from __future__ import annotations

from typing import Any, Literal, NotRequired, TypedDict, TYPE_CHECKING

from ..imap_core import (
    search_uids as imap_search_uids,
    store_flag as imap_store_flag,
    store_keywords as imap_store_keywords,
)
from ._common import (
    _FORBIDDEN_SYSTEM_FLAGS,
    _password_for,
    _resolve_imap_folder,
)

if TYPE_CHECKING:
    from ..context import ServerContext


class MarkResponse(TypedDict, total=False):
    decision: Literal["ALLOW", "DENY"]
    result: NotRequired[Literal["OK", "ERROR"]]
    error_type: NotRequired[str | None]
    reason: NotRequired[str]
    account: str
    folder: str
    uid: NotRequired[int]
    missing_capability: NotRequired[str]
    required_scope: NotRequired[str]
    granted_scope: NotRequired[str]
    forbidden_tags: NotRequired[list[str]]
    marked_count: NotRequired[int]


def _deny_mark(
    *,
    reason: str,
    account: str,
    folder: str,
    uid: int | None = None,
    missing_capability: str | None = None,
    required_scope: str | None = None,
    granted_scope: str | None = None,
    forbidden_tags: list[str] | None = None,
) -> MarkResponse:
    response: MarkResponse = {
        "decision": "DENY",
        "reason": reason,
        "account": account,
        "folder": folder,
    }
    if uid is not None:
        response["uid"] = uid
    if missing_capability is not None:
        response["missing_capability"] = missing_capability
    if required_scope is not None:
        response["required_scope"] = required_scope
    if granted_scope is not None:
        response["granted_scope"] = granted_scope
    if forbidden_tags is not None:
        response["forbidden_tags"] = forbidden_tags
    return response


def _ok_mark(
    *,
    reason: str,
    account: str,
    folder: str,
    uid: int | None = None,
    marked_count: int | None = None,
) -> MarkResponse:
    response: MarkResponse = {
        "decision": "ALLOW",
        "result": "OK",
        "error_type": None,
        "reason": reason,
        "account": account,
        "folder": folder,
    }
    if uid is not None:
        response["uid"] = uid
    if marked_count is not None:
        response["marked_count"] = marked_count
    return response


def _error_mark(
    *, error_type: str, reason: str, account: str, folder: str, uid: int
) -> MarkResponse:
    return {
        "decision": "ALLOW",
        "result": "ERROR",
        "error_type": error_type,
        "reason": reason,
        "account": account,
        "folder": folder,
        "uid": uid,
    }


async def handle_mark_seen(context: "ServerContext", arguments: dict[str, Any]) -> MarkResponse:
    account_id = str(arguments["account"])
    folder_path = str(arguments["folder"])
    uid = int(arguments["uid"])
    seen = bool(arguments["seen"])

    account = context.account_by_id(account_id)
    if account and account.auth and account.auth.type == "xoauth2":
        scope = account.auth.oauth_scope or ""
        if "readonly" in scope:
            return _deny_mark(
                reason="oauth_scope_insufficient",
                account=account_id,
                folder=folder_path,
                uid=uid,
                required_scope="https://mail.google.com/",
                granted_scope=scope,
            )

    folder_decision = context.pdp.decide_folder_access(context.caller_id, account_id, folder_path)
    if not folder_decision.allowed:
        return _deny_mark(
            reason=folder_decision.reason, account=account_id, folder=folder_path, uid=uid
        )
    assert folder_decision.folder_policy is not None
    if not folder_decision.folder_policy.mark_seen:
        return _deny_mark(
            reason="capability_missing",
            account=account_id,
            folder=folder_path,
            uid=uid,
            missing_capability="mark_seen",
        )
    account, password = await _password_for(context, account_id)
    imap_folder = await _resolve_imap_folder(context, account_id, folder_path)
    ok = await imap_store_flag(account, password, imap_folder, uid, r"\Seen", add=seen)
    if not ok:
        return _error_mark(
            error_type="uid_not_found",
            reason="rule_matched",
            account=account_id,
            folder=folder_path,
            uid=uid,
        )
    return _ok_mark(reason="rule_matched", account=account_id, folder=folder_path, uid=uid)


async def handle_bulk_mark_seen(
    context: "ServerContext", arguments: dict[str, Any]
) -> MarkResponse:
    from ..imap_core import store_flags_batch as imap_store_flags_batch
    from .search import _criteria_to_imap_search

    account_id = str(arguments["account"])
    folder_path = str(arguments["folder"])
    criteria_raw = arguments.get("criteria", {})
    seen = bool(arguments["seen"])

    folder_decision = context.pdp.decide_folder_access(context.caller_id, account_id, folder_path)
    if not folder_decision.allowed:
        return _deny_mark(reason=folder_decision.reason, account=account_id, folder=folder_path)
    assert folder_decision.folder_policy is not None
    if not folder_decision.folder_policy.mark_seen:
        return _deny_mark(
            reason="capability_missing",
            account=account_id,
            folder=folder_path,
            missing_capability="mark_seen",
        )

    imap_criteria = _criteria_to_imap_search(criteria_raw)
    account, password = await _password_for(context, account_id)
    imap_folder = await _resolve_imap_folder(context, account_id, folder_path)
    uids = await imap_search_uids(account, password, imap_folder, imap_criteria)
    if not uids:
        return _ok_mark(
            reason="rule_matched", account=account_id, folder=folder_path, marked_count=0
        )
    count = await imap_store_flags_batch(account, password, imap_folder, uids, r"\Seen", add=seen)
    return _ok_mark(
        reason="rule_matched", account=account_id, folder=folder_path, marked_count=count
    )


async def handle_mark_tagged(context: "ServerContext", arguments: dict[str, Any]) -> MarkResponse:
    account_id = str(arguments["account"])
    folder_path = str(arguments["folder"])
    uid = int(arguments["uid"])
    tags = list(arguments["tags"])
    mode = str(arguments["mode"])
    folder_decision = context.pdp.decide_folder_access(context.caller_id, account_id, folder_path)
    if not folder_decision.allowed:
        return _deny_mark(
            reason=folder_decision.reason, account=account_id, folder=folder_path, uid=uid
        )
    assert folder_decision.folder_policy is not None
    if not folder_decision.folder_policy.mark_tagged:
        return _deny_mark(
            reason="capability_missing",
            account=account_id,
            folder=folder_path,
            uid=uid,
            missing_capability="mark_tagged",
        )
    forbidden = [t for t in tags if t in _FORBIDDEN_SYSTEM_FLAGS]
    if forbidden:
        return _deny_mark(
            reason="forbidden_system_flag",
            account=account_id,
            folder=folder_path,
            uid=uid,
            forbidden_tags=forbidden,
        )
    account, password = await _password_for(context, account_id)
    imap_folder = await _resolve_imap_folder(context, account_id, folder_path)
    ok = await imap_store_keywords(account, password, imap_folder, uid, tags, mode=mode)
    # mark_tagged returns no `error_type` on failure (legacy shape).
    return {
        "decision": "ALLOW",
        "reason": "rule_matched",
        "result": "OK" if ok else "ERROR",
        "account": account_id,
        "folder": folder_path,
        "uid": uid,
    }
