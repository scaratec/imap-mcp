"""Message-transfer handlers: move, copy.

Both tools share folder-access validation and the intra/cross-account
branching. Gmail accounts route through label-swap instead of native
IMAP MOVE because Gmail folders are label projections.
"""

from __future__ import annotations

from typing import Any, TYPE_CHECKING

from ..imap_core import (
    TargetFolderMissing,
    UidNotFound,
    UidStale,
    copy_message as imap_copy_message,
    gmail_label_swap as imap_gmail_label_swap,
    move_message as imap_move_message,
)
from ._common import (
    _deny,
    _error,
    _is_google_provider,
    _ok,
    _password_for,
    _resolve_imap_folder,
)

if TYPE_CHECKING:
    from ..context import ServerContext


async def handle_move(context: "ServerContext", arguments: dict[str, Any]) -> dict[str, Any]:
    src = arguments["source"]
    dst = arguments["target"]
    src_account = str(src["account"])
    src_folder = str(src["folder"])
    src_uid = int(src["uid"])
    dst_account = str(dst["account"])
    dst_folder = str(dst["folder"])

    # Check pre-conditions that do not depend on any policy evaluation
    # first — a degenerate request like "move INBOX to INBOX" never
    # needs authorization discussion.
    src_base = {"account": src_account, "folder": src_folder, "uid": src_uid}
    if src_account == dst_account and src_folder == dst_folder:
        return _error(error_type="same_source_and_target", **src_base)

    src_dec = context.pdp.decide_folder_access(context.caller_id, src_account, src_folder)
    if not src_dec.allowed:
        return _deny(reason=src_dec.reason, **src_base)
    assert src_dec.folder_policy is not None
    if not src_dec.folder_policy.move_out:
        return _deny(
            reason="capability_missing",
            missing_capability="move_out",
            **src_base,
        )
    dst_dec = context.pdp.decide_folder_access(context.caller_id, dst_account, dst_folder)
    if not dst_dec.allowed:
        return _deny(
            reason=dst_dec.reason,
            account=dst_account,
            folder=dst_folder,
        )
    assert dst_dec.folder_policy is not None
    if not dst_dec.folder_policy.accept_incoming:
        return _deny(
            reason="capability_missing",
            missing_capability="accept_incoming",
            account=dst_account,
            folder=dst_folder,
        )
    saga_base = {
        "account": src_account,
        "source_folder": src_folder,
        "target_folder": dst_folder,
        "uid": src_uid,
    }
    if src_account == dst_account:
        account, password = await _password_for(context, src_account)
        imap_src = await _resolve_imap_folder(context, src_account, src_folder)
        imap_dst = await _resolve_imap_folder(context, src_account, dst_folder)
        # Gmail label-swap: for Google accounts, intra-account moves
        # are implemented as label remove + label add instead of IMAP
        # MOVE, because Gmail folders are label projections over a
        # single [Gmail]/All Mail store.
        account_obj = context.account_by_id(src_account)
        if account_obj is not None and _is_google_provider(account_obj):
            from ..imap_core import _LABEL_TO_FOLDER  # noqa: F811

            # Build the reverse map: folder -> label
            _folder_to_label: dict[str, str] = {v: k for k, v in _LABEL_TO_FOLDER.items()}
            # Custom labels map to themselves
            src_label = _folder_to_label.get(src_folder, src_folder)
            dst_label = _folder_to_label.get(dst_folder, dst_folder)
            try:
                await imap_gmail_label_swap(account, password, src_uid, src_label, dst_label)
            except RuntimeError:
                return _error(error_type="uid_not_found", **src_base)
            return _ok(mechanism="gmail_label_swap", tx_id=None, **saga_base)
        try:
            mechanism = await imap_move_message(account, password, imap_src, src_uid, imap_dst)
        except TargetFolderMissing:
            return _error(error_type="target_folder_missing", **saga_base)
        except UidStale:
            return _error(error_type="uid_stale", **src_base)
        except UidNotFound:
            return _error(error_type="uid_not_found", **src_base)
        except RuntimeError:
            return _error(error_type="uid_not_found", **src_base)
        return _ok(mechanism=mechanism, tx_id=None, **saga_base)
    # Cross-account saga (ADR 0006).
    if context.saga is None:
        return _error(error_type="saga_not_configured")
    src_acct, src_pwd = await _password_for(context, src_account)
    dst_acct, dst_pwd = await _password_for(context, dst_account)
    imap_src = await _resolve_imap_folder(context, src_account, src_folder)
    imap_dst = await _resolve_imap_folder(context, dst_account, dst_folder)
    result = await context.saga.run_cross_account_move(
        caller_id=context.caller_id,
        src_account=src_acct,
        src_password=src_pwd,
        src_folder=imap_src,
        src_uid=src_uid,
        dst_account=dst_acct,
        dst_password=dst_pwd,
        dst_folder=imap_dst,
        delete_source=True,
    )
    return {
        "decision": "ALLOW",
        "result": result.result,
        "error_type": result.error_type,
        "mechanism": result.mechanism,
        "tx_id": result.tx_id,
        **saga_base,
    }


async def handle_copy(context: "ServerContext", arguments: dict[str, Any]) -> dict[str, Any]:
    src = arguments["source"]
    dst = arguments["target"]
    src_account = str(src["account"])
    src_folder = str(src["folder"])
    src_uid = int(src["uid"])
    dst_account = str(dst["account"])
    dst_folder = str(dst["folder"])

    src_base = {"account": src_account, "folder": src_folder, "uid": src_uid}
    saga_base = {
        "account": src_account,
        "source_folder": src_folder,
        "target_folder": dst_folder,
        "uid": src_uid,
    }
    src_dec = context.pdp.decide_folder_access(context.caller_id, src_account, src_folder)
    if not src_dec.allowed:
        return _deny(reason=src_dec.reason, **src_base)
    dst_dec = context.pdp.decide_folder_access(context.caller_id, dst_account, dst_folder)
    if not dst_dec.allowed:
        return _deny(
            reason=dst_dec.reason,
            account=dst_account,
            folder=dst_folder,
        )
    assert dst_dec.folder_policy is not None
    if not dst_dec.folder_policy.accept_incoming:
        return _deny(
            reason="capability_missing",
            missing_capability="accept_incoming",
            account=dst_account,
            folder=dst_folder,
        )
    if src_account != dst_account:
        if context.saga is None:
            return _error(error_type="saga_not_configured")
        src_acct, src_pwd = await _password_for(context, src_account)
        dst_acct, dst_pwd = await _password_for(context, dst_account)
        imap_src = await _resolve_imap_folder(context, src_account, src_folder)
        imap_dst = await _resolve_imap_folder(context, dst_account, dst_folder)
        result = await context.saga.run_cross_account_move(
            caller_id=context.caller_id,
            src_account=src_acct,
            src_password=src_pwd,
            src_folder=imap_src,
            src_uid=src_uid,
            dst_account=dst_acct,
            dst_password=dst_pwd,
            dst_folder=imap_dst,
            delete_source=False,
        )
        return {
            "decision": "ALLOW",
            "result": result.result,
            "error_type": result.error_type,
            "mechanism": result.mechanism,
            "tx_id": result.tx_id,
            **saga_base,
        }
    account, password = await _password_for(context, src_account)
    imap_src = await _resolve_imap_folder(context, src_account, src_folder)
    imap_dst = await _resolve_imap_folder(context, src_account, dst_folder)
    ok = await imap_copy_message(account, password, imap_src, src_uid, imap_dst)
    if ok:
        return _ok(mechanism="native_copy", tx_id=None, **saga_base)
    return _error(
        error_type="uid_not_found",
        mechanism="native_copy",
        tx_id=None,
        **saga_base,
    )
