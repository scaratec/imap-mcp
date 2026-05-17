"""Flag/keyword mutation handlers: mark_seen, bulk_mark_seen, mark_tagged."""

from __future__ import annotations

from typing import Any, TYPE_CHECKING

from ..imap_core import (
    search_uids as imap_search_uids,
    store_flag as imap_store_flag,
    store_keywords as imap_store_keywords,
)
from ._common import (
    _FORBIDDEN_SYSTEM_FLAGS,
    _deny,
    _error,
    _ok,
    _password_for,
    _resolve_imap_folder,
)

if TYPE_CHECKING:
    from ..context import ServerContext


async def handle_mark_seen(context: "ServerContext", arguments: dict[str, Any]) -> dict[str, Any]:
    account_id = str(arguments["account"])
    folder_path = str(arguments["folder"])
    uid = int(arguments["uid"])
    seen = bool(arguments["seen"])
    base = {"account": account_id, "folder": folder_path, "uid": uid}

    account = context.account_by_id(account_id)
    if account and account.auth and account.auth.type == "xoauth2":
        scope = account.auth.oauth_scope or ""
        if "readonly" in scope:
            return _deny(
                reason="oauth_scope_insufficient",
                required_scope="https://mail.google.com/",
                granted_scope=scope,
                **base,
            )

    folder_decision = context.pdp.decide_folder_access(context.caller_id, account_id, folder_path)
    if not folder_decision.allowed:
        return _deny(reason=folder_decision.reason, **base)
    assert folder_decision.folder_policy is not None
    if not folder_decision.folder_policy.mark_seen:
        return _deny(
            reason="capability_missing",
            missing_capability="mark_seen",
            **base,
        )
    account, password = await _password_for(context, account_id)
    imap_folder = await _resolve_imap_folder(context, account_id, folder_path)
    ok = await imap_store_flag(account, password, imap_folder, uid, r"\Seen", add=seen)
    if not ok:
        return _error(
            error_type="uid_not_found",
            reason="rule_matched",
            **base,
        )
    return _ok(reason="rule_matched", **base)


async def handle_bulk_mark_seen(
    context: "ServerContext", arguments: dict[str, Any]
) -> dict[str, Any]:
    from ..imap_core import store_flags_batch as imap_store_flags_batch
    from .search import _criteria_to_imap_search

    account_id = str(arguments["account"])
    folder_path = str(arguments["folder"])
    criteria_raw = arguments.get("criteria", {})
    seen = bool(arguments["seen"])
    base = {"account": account_id, "folder": folder_path}

    folder_decision = context.pdp.decide_folder_access(context.caller_id, account_id, folder_path)
    if not folder_decision.allowed:
        return _deny(reason=folder_decision.reason, **base)
    assert folder_decision.folder_policy is not None
    if not folder_decision.folder_policy.mark_seen:
        return _deny(
            reason="capability_missing",
            missing_capability="mark_seen",
            **base,
        )

    imap_criteria = _criteria_to_imap_search(criteria_raw)
    account, password = await _password_for(context, account_id)
    imap_folder = await _resolve_imap_folder(context, account_id, folder_path)
    uids = await imap_search_uids(account, password, imap_folder, imap_criteria)
    if not uids:
        return _ok(reason="rule_matched", marked_count=0, **base)
    count = await imap_store_flags_batch(account, password, imap_folder, uids, r"\Seen", add=seen)
    return _ok(reason="rule_matched", marked_count=count, **base)


async def handle_mark_tagged(context: "ServerContext", arguments: dict[str, Any]) -> dict[str, Any]:
    account_id = str(arguments["account"])
    folder_path = str(arguments["folder"])
    uid = int(arguments["uid"])
    tags = list(arguments["tags"])
    mode = str(arguments["mode"])
    base = {"account": account_id, "folder": folder_path, "uid": uid}
    folder_decision = context.pdp.decide_folder_access(context.caller_id, account_id, folder_path)
    if not folder_decision.allowed:
        return _deny(reason=folder_decision.reason, **base)
    assert folder_decision.folder_policy is not None
    if not folder_decision.folder_policy.mark_tagged:
        return _deny(
            reason="capability_missing",
            missing_capability="mark_tagged",
            **base,
        )
    forbidden = [t for t in tags if t in _FORBIDDEN_SYSTEM_FLAGS]
    if forbidden:
        return _deny(
            reason="forbidden_system_flag",
            forbidden_tags=forbidden,
            **base,
        )
    account, password = await _password_for(context, account_id)
    imap_folder = await _resolve_imap_folder(context, account_id, folder_path)
    ok = await imap_store_keywords(account, password, imap_folder, uid, tags, mode=mode)
    # mark_tagged returns no `error_type` on failure (legacy shape).
    return {
        "decision": "ALLOW",
        "reason": "rule_matched",
        "result": "OK" if ok else "ERROR",
        **base,
    }
