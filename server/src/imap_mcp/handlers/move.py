"""Message-transfer handlers: move, copy.

Both tools share folder-access validation and the intra/cross-account
branching. Gmail accounts route through label-swap instead of native
IMAP MOVE because Gmail folders are label projections.
"""

from __future__ import annotations

from typing import Any, Literal, NotRequired, TypedDict, TYPE_CHECKING

from ..imap_core import (
    LabelMutationFailed,
    TargetFolderMissing,
    UidNotFound,
    UidStale,
    copy_message as imap_copy_message,
    gmail_label_swap as imap_gmail_label_swap,
    move_message as imap_move_message,
)
from ._common import (
    _is_google_provider,
    _password_for,
    _resolve_imap_folder,
)

if TYPE_CHECKING:
    from ..context import ServerContext


class MoveCopyResponse(TypedDict, total=False):
    decision: Literal["ALLOW", "DENY"]
    result: NotRequired[Literal["OK", "ERROR"]]
    error_type: NotRequired[str | None]
    reason: NotRequired[str]
    account: NotRequired[str]
    folder: NotRequired[str]
    source_folder: NotRequired[str]
    target_folder: NotRequired[str]
    uid: NotRequired[int]
    missing_capability: NotRequired[str]
    mechanism: NotRequired[str]
    tx_id: NotRequired[str | None]
    imap_response: NotRequired[str]


def _deny_move(
    *,
    reason: str,
    account: str,
    folder: str,
    uid: int | None = None,
    missing_capability: str | None = None,
) -> MoveCopyResponse:
    response: MoveCopyResponse = {
        "decision": "DENY",
        "reason": reason,
        "account": account,
        "folder": folder,
    }
    if uid is not None:
        response["uid"] = uid
    if missing_capability is not None:
        response["missing_capability"] = missing_capability
    return response


def _ok_saga(
    *,
    mechanism: str,
    account: str,
    source_folder: str,
    target_folder: str,
    uid: int,
    tx_id: str | None,
) -> MoveCopyResponse:
    return {
        "decision": "ALLOW",
        "result": "OK",
        "error_type": None,
        "mechanism": mechanism,
        "tx_id": tx_id,
        "account": account,
        "source_folder": source_folder,
        "target_folder": target_folder,
        "uid": uid,
    }


def _error_saga(
    *,
    error_type: str,
    account: str | None = None,
    folder: str | None = None,
    source_folder: str | None = None,
    target_folder: str | None = None,
    uid: int | None = None,
    mechanism: str | None = None,
    tx_id: str | None = None,
    imap_response: str | None = None,
) -> MoveCopyResponse:
    response: MoveCopyResponse = {
        "decision": "ALLOW",
        "result": "ERROR",
        "error_type": error_type,
    }
    if mechanism is not None:
        response["mechanism"] = mechanism
        response["tx_id"] = tx_id
    if account is not None:
        response["account"] = account
    if folder is not None:
        response["folder"] = folder
    if source_folder is not None:
        response["source_folder"] = source_folder
    if target_folder is not None:
        response["target_folder"] = target_folder
    if uid is not None:
        response["uid"] = uid
    if imap_response is not None:
        response["imap_response"] = imap_response
    return response


async def _handle_move_gmail_label_swap(
    context: "ServerContext",
    *,
    src_account: str,
    src_folder: str,
    dst_folder: str,
    src_uid: int,
) -> MoveCopyResponse:
    """Gmail intra-account branch: remove the source label and add the
    target label. Gmail folders are label projections over a single
    [Gmail]/All Mail store, so this is the correct primitive instead
    of IMAP MOVE."""
    from ..imap_core import _LABEL_TO_FOLDER

    account, password = await _password_for(context, src_account)
    folder_to_label: dict[str, str] = {v: k for k, v in _LABEL_TO_FOLDER.items()}
    src_label = folder_to_label.get(src_folder, src_folder)
    dst_label = folder_to_label.get(dst_folder, dst_folder)
    try:
        await imap_gmail_label_swap(account, password, src_uid, src_label, dst_label)
    except LabelMutationFailed as e:
        return _error_saga(
            error_type="provider_rejected",
            mechanism="gmail_label_swap",
            tx_id=None,
            account=src_account,
            source_folder=src_folder,
            target_folder=dst_folder,
            uid=src_uid,
            imap_response=e.response_text or e.status,
        )
    except RuntimeError:
        return _error_saga(
            error_type="uid_not_found",
            account=src_account,
            folder=src_folder,
            uid=src_uid,
        )
    return _ok_saga(
        mechanism="gmail_label_swap",
        tx_id=None,
        account=src_account,
        source_folder=src_folder,
        target_folder=dst_folder,
        uid=src_uid,
    )


async def _handle_move_intra_account_standard(
    context: "ServerContext",
    *,
    src_account: str,
    src_folder: str,
    dst_folder: str,
    src_uid: int,
) -> MoveCopyResponse:
    """Non-Gmail intra-account branch: native IMAP MOVE, falling back
    to COPY+DELETE inside ``imap_move_message``. The four except arms
    surface each known failure mode as its own ``error_type``."""
    account, password = await _password_for(context, src_account)
    imap_src = await _resolve_imap_folder(context, src_account, src_folder)
    imap_dst = await _resolve_imap_folder(context, src_account, dst_folder)
    try:
        mechanism = await imap_move_message(account, password, imap_src, src_uid, imap_dst)
    except TargetFolderMissing:
        return _error_saga(
            error_type="target_folder_missing",
            account=src_account,
            source_folder=src_folder,
            target_folder=dst_folder,
            uid=src_uid,
        )
    except UidStale:
        return _error_saga(
            error_type="uid_stale", account=src_account, folder=src_folder, uid=src_uid
        )
    except (UidNotFound, RuntimeError):
        return _error_saga(
            error_type="uid_not_found", account=src_account, folder=src_folder, uid=src_uid
        )
    return _ok_saga(
        mechanism=mechanism,
        tx_id=None,
        account=src_account,
        source_folder=src_folder,
        target_folder=dst_folder,
        uid=src_uid,
    )


async def _handle_move_cross_account(
    context: "ServerContext",
    *,
    src_account: str,
    src_folder: str,
    src_uid: int,
    dst_account: str,
    dst_folder: str,
) -> MoveCopyResponse:
    """Cross-account branch: the saga (ADR 0006) drives BEGIN → FETCH
    src → APPEND dst → VERIFY → DELETE src → COMMIT with WAL-backed
    recovery."""
    if context.saga is None:
        return _error_saga(error_type="saga_not_configured")
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
        "account": src_account,
        "source_folder": src_folder,
        "target_folder": dst_folder,
        "uid": src_uid,
    }


async def handle_move(context: "ServerContext", arguments: dict[str, Any]) -> MoveCopyResponse:
    """Top-level orchestrator: validation gates first, then dispatch
    to one of the three branches (Gmail label-swap, native IMAP MOVE,
    cross-account saga). The validation gates are pre-policy, then
    src + dst folder-access decisions, then write capabilities."""
    src = arguments["source"]
    dst = arguments["target"]
    src_account = str(src["account"])
    src_folder = str(src["folder"])
    src_uid = int(src["uid"])
    dst_account = str(dst["account"])
    dst_folder = str(dst["folder"])

    # Pre-policy gate: a degenerate "move INBOX to INBOX" never needs
    # authorization discussion.
    if src_account == dst_account and src_folder == dst_folder:
        return _error_saga(
            error_type="same_source_and_target",
            account=src_account,
            folder=src_folder,
            uid=src_uid,
        )

    src_dec = context.pdp.decide_folder_access(context.caller_id, src_account, src_folder)
    if not src_dec.allowed:
        return _deny_move(
            reason=src_dec.reason, account=src_account, folder=src_folder, uid=src_uid
        )
    assert src_dec.folder_policy is not None
    if not src_dec.folder_policy.move_out:
        return _deny_move(
            reason="capability_missing",
            account=src_account,
            folder=src_folder,
            uid=src_uid,
            missing_capability="move_out",
        )
    dst_dec = context.pdp.decide_folder_access(context.caller_id, dst_account, dst_folder)
    if not dst_dec.allowed:
        return _deny_move(reason=dst_dec.reason, account=dst_account, folder=dst_folder)
    assert dst_dec.folder_policy is not None
    if not dst_dec.folder_policy.accept_incoming:
        return _deny_move(
            reason="capability_missing",
            account=dst_account,
            folder=dst_folder,
            missing_capability="accept_incoming",
        )

    if src_account != dst_account:
        return await _handle_move_cross_account(
            context,
            src_account=src_account,
            src_folder=src_folder,
            src_uid=src_uid,
            dst_account=dst_account,
            dst_folder=dst_folder,
        )

    account_obj = context.account_by_id(src_account)
    if account_obj is not None and _is_google_provider(account_obj):
        return await _handle_move_gmail_label_swap(
            context,
            src_account=src_account,
            src_folder=src_folder,
            dst_folder=dst_folder,
            src_uid=src_uid,
        )
    return await _handle_move_intra_account_standard(
        context,
        src_account=src_account,
        src_folder=src_folder,
        dst_folder=dst_folder,
        src_uid=src_uid,
    )


async def handle_copy(context: "ServerContext", arguments: dict[str, Any]) -> MoveCopyResponse:
    src = arguments["source"]
    dst = arguments["target"]
    src_account = str(src["account"])
    src_folder = str(src["folder"])
    src_uid = int(src["uid"])
    dst_account = str(dst["account"])
    dst_folder = str(dst["folder"])

    src_dec = context.pdp.decide_folder_access(context.caller_id, src_account, src_folder)
    if not src_dec.allowed:
        return _deny_move(
            reason=src_dec.reason, account=src_account, folder=src_folder, uid=src_uid
        )
    dst_dec = context.pdp.decide_folder_access(context.caller_id, dst_account, dst_folder)
    if not dst_dec.allowed:
        return _deny_move(reason=dst_dec.reason, account=dst_account, folder=dst_folder)
    assert dst_dec.folder_policy is not None
    if not dst_dec.folder_policy.accept_incoming:
        return _deny_move(
            reason="capability_missing",
            account=dst_account,
            folder=dst_folder,
            missing_capability="accept_incoming",
        )
    if src_account != dst_account:
        if context.saga is None:
            return _error_saga(error_type="saga_not_configured")
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
            "account": src_account,
            "source_folder": src_folder,
            "target_folder": dst_folder,
            "uid": src_uid,
        }
    account, password = await _password_for(context, src_account)
    imap_src = await _resolve_imap_folder(context, src_account, src_folder)
    imap_dst = await _resolve_imap_folder(context, src_account, dst_folder)
    ok = await imap_copy_message(account, password, imap_src, src_uid, imap_dst)
    if ok:
        return _ok_saga(
            mechanism="native_copy",
            tx_id=None,
            account=src_account,
            source_folder=src_folder,
            target_folder=dst_folder,
            uid=src_uid,
        )
    return _error_saga(
        error_type="uid_not_found",
        mechanism="native_copy",
        tx_id=None,
        account=src_account,
        source_folder=src_folder,
        target_folder=dst_folder,
        uid=src_uid,
    )
