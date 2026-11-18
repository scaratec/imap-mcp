"""Account-discovery tool handlers: list_accounts, list_folders, list_labels.

Adheres to:
- ADR 0040: Multi-label composition and policy-filtered labels on Google accounts
- ADR 0043: Non-interference transparency — removal of hidden counts and raw message_count
- ADR 0044: Normalized response envelope
- ADR 0062: Closed reason and error code registry
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Literal, NotRequired, TypedDict

from ..imap_core import (
    build_folder_alias_map,
)
from ..imap_core import (
    gmail_list_labels as imap_gmail_list_labels,
)
from ..imap_core import (
    list_folders as imap_list_folders,
)
from ..policy import level_rank
from ._common import _is_google_provider, _password_for

if TYPE_CHECKING:
    from ..context import ServerContext


class AccountEntry(TypedDict):
    id: str
    state: str


class ListAccountsResponse(TypedDict):
    decision: Literal["ALLOW"]
    result: Literal["OK"]
    reason: Literal["authenticated"]
    accounts: list[AccountEntry]


class ListFoldersResponse(TypedDict, total=False):
    decision: Literal["ALLOW", "DENY"]
    result: NotRequired[Literal["OK", "ERROR"]]
    reason: str
    account: str
    folders: NotRequired[list[str]]


class ListLabelsResponse(TypedDict, total=False):
    decision: Literal["ALLOW", "DENY"]
    result: NotRequired[Literal["OK", "ERROR"]]
    reason: str
    account: str
    labels: NotRequired[list[str]]


def handle_list_accounts(context: ServerContext, arguments: dict[str, Any]) -> ListAccountsResponse:
    _ = arguments
    visibility = context.pdp.visible_accounts_for(context.caller_id)
    accounts: list[AccountEntry] = []
    for aid in visibility.visible_account_ids:
        state = "active"
        if context.oauth_manager.is_rebootstrap_needed(aid):
            state = "needs_rebootstrap"
        accounts.append({"id": aid, "state": state})

    return {
        "decision": "ALLOW",
        "result": "OK",
        "reason": "authenticated",
        "accounts": accounts,
    }


async def handle_list_folders(
    context: ServerContext, arguments: dict[str, Any]
) -> ListFoldersResponse:
    account_id = str(arguments["account"])
    visible = context.pdp.visible_accounts_for(context.caller_id)
    if account_id not in visible.visible_account_ids:
        return {
            "decision": "DENY",
            "reason": "account_hidden",
            "account": account_id,
        }
    if context.oauth_manager.is_rebootstrap_needed(account_id):
        return {
            "decision": "DENY",
            "reason": "needs_rebootstrap",
            "account": account_id,
        }

    account_model, password = await _password_for(context, account_id)
    folder_infos = await imap_list_folders(account_model, password)

    alias_map: dict[str, str] = {}
    if _is_google_provider(account_model):
        alias_map = build_folder_alias_map(folder_infos)
        context.pdp.set_folder_aliases(account_id, alias_map)

    reverse_map = {v: k for k, v in alias_map.items()}
    all_paths = [reverse_map.get(fi.path, fi.path) for fi in folder_infos]
    visibility = context.pdp.visible_folders_for(context.caller_id, account_id, all_paths)

    return {
        "decision": "ALLOW",
        "result": "OK",
        "reason": "folder_default_applied",
        "account": account_id,
        "folders": sorted(visibility.visible_folder_paths),
    }


async def handle_list_labels(
    context: ServerContext, arguments: dict[str, Any]
) -> ListLabelsResponse:
    account_id = str(arguments["account"])
    account = context.account_by_id(account_id)
    if account is None or not _is_google_provider(account):
        return {
            "decision": "DENY",
            "reason": "tool_not_applicable_for_provider",
            "account": account_id,
        }

    visible = context.pdp.visible_accounts_for(context.caller_id)
    if account_id not in visible.visible_account_ids:
        return {
            "decision": "DENY",
            "reason": "account_hidden",
            "account": account_id,
        }
    if context.oauth_manager.is_rebootstrap_needed(account_id):
        return {
            "decision": "DENY",
            "reason": "needs_rebootstrap",
            "account": account_id,
        }

    # Verify if caller has any folder policy with max_visibility >= COUNT (ADR 0062)
    caller = context.configuration.callers_file.callers[0]
    policy = context.configuration.policy_by_name(caller.policy)
    folder_policies = policy.accounts.get(account_id, []) if policy else []
    has_count_vis = any(
        level_rank(context.pdp.folder_max_visibility(fp)) >= level_rank("COUNT")
        for fp in folder_policies
    )
    if not has_count_vis:
        return {
            "decision": "DENY",
            "reason": "visibility_below_COUNT",
            "account": account_id,
        }

    account_model, password = await _password_for(context, account_id)
    raw_labels = await imap_gmail_list_labels(account_model, password)
    label_names = [item["name"] if isinstance(item, dict) else str(item) for item in raw_labels]
    filtered = context.pdp.filter_labels_for_caller(context.caller_id, account_id, label_names)

    return {
        "decision": "ALLOW",
        "result": "OK",
        "reason": "folder_default_applied",
        "account": account_id,
        "labels": filtered,
    }
