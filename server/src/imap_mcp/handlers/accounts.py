"""Account-discovery tool handlers: list_accounts, list_folders, list_labels."""

from __future__ import annotations

from typing import Any, TYPE_CHECKING

from ..imap_core import (
    build_folder_alias_map,
    gmail_list_labels as imap_gmail_list_labels,
    list_folders as imap_list_folders,
)
from ._common import (
    _deny,
    _is_google_provider,
    _password_for,
    _password_for_account,
)

if TYPE_CHECKING:
    from ..context import ServerContext


def handle_list_accounts(context: "ServerContext", arguments: dict[str, Any]) -> dict[str, Any]:
    _ = arguments
    visibility = context.pdp.visible_accounts_for(context.caller_id)
    accounts = []
    for aid in visibility.visible_account_ids:
        state = "active"
        if context.oauth_manager.is_rebootstrap_needed(aid):
            state = "needs_rebootstrap"
        accounts.append({"id": aid, "state": state})

    return {
        "accounts": accounts,
        "hidden_accounts_count": int(visibility.hidden_account_count),
    }


async def handle_list_folders(context: "ServerContext", arguments: dict[str, Any]) -> dict[str, Any]:
    account_id = str(arguments["account"])
    visible = context.pdp.visible_accounts_for(context.caller_id)
    if account_id not in visible.visible_account_ids:
        return _deny(
            reason="account_hidden",
            account=account_id,
            folders=[],
            hidden_folders_count=0,
        )
    if context.oauth_manager.is_rebootstrap_needed(account_id):
        return _deny(reason="needs_rebootstrap", account=account_id)
    account_model, password = await _password_for_account(context, account_id)
    folder_infos = await imap_list_folders(account_model, password)

    alias_map: dict[str, str] = {}
    if _is_google_provider(account_model):
        alias_map = build_folder_alias_map(folder_infos)
        if context._live.folder_aliases is None:
            context._live.folder_aliases = {}
        context._live.folder_aliases[account_id] = alias_map

    reverse_map = {v: k for k, v in alias_map.items()}
    all_paths = [reverse_map.get(fi.path, fi.path) for fi in folder_infos]
    count_by_path = {
        reverse_map.get(fi.path, fi.path): fi.message_count
        for fi in folder_infos
    }
    visibility = context.pdp.visible_folders_for(context.caller_id, account_id, all_paths)
    folders_result = [
        {"path": p, "message_count": count_by_path.get(p, 0)}
        for p in visibility.visible_folder_paths
    ]
    return {
        "folders": folders_result,
        "hidden_folders_count": int(visibility.hidden_folder_count),
    }


async def handle_list_labels(context: "ServerContext", arguments: dict[str, Any]) -> dict[str, Any]:
    account_id = str(arguments["account"])
    # Provider gate: list_labels is only meaningful for Google accounts.
    account = context.account_by_id(account_id)
    if account is None or not _is_google_provider(account):
        return _deny(
            reason="tool_not_applicable_for_provider",
            account=account_id,
        )
    visible = context.pdp.visible_accounts_for(context.caller_id)
    if account_id not in visible.visible_account_ids:
        return _deny(reason="account_hidden", account=account_id)
    account_model, password = await _password_for(context, account_id)
    labels = await imap_gmail_list_labels(account_model, password)
    return {
        "decision": "ALLOW",
        "account": account_id,
        "labels": labels,
    }
