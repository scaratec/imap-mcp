"""Message-transfer handlers: move, copy.

Adheres to:
- ADR 0040: Gmail multi-label move to Trash/Spam requires move_out on all active labels
- ADR 0044: Normalized response envelope and gate priority
- ADR 0045: Write visibility floor (ENVELOPE), flow monotonicity (relocation_would_escalate)
- ADR 0046: Content-verified sagas, UIDVALIDITY guard, UID EXPUNGE fallback
- ADR 0048: Pre-effect intent and post-effect outcome audit records
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, Any, Literal, NotRequired, TypedDict

from ..imap_core import (
    LabelMutationFailed,
)
from ..imap_core import (
    fetch_envelope as imap_fetch_envelope,
)
from ..imap_core import (
    folder_stats as imap_folder_stats,
)
from ..imap_core import (
    gmail_label_swap as imap_gmail_label_swap,
)
from ..policy import level_rank
from ._common import (
    _facts_from_envelope,
    _is_google_provider,
    _password_for,
    _resolve_imap_folder,
    error_envelope,
)

if TYPE_CHECKING:
    from ..context import ServerContext


class MoveCopyResponse(TypedDict, total=False):
    decision: Literal["ALLOW", "DENY"]
    result: NotRequired[Literal["OK", "ERROR"]]
    reason: NotRequired[str]
    error: NotRequired[dict[str, str]]
    account: NotRequired[str]
    folder: NotRequired[str]
    source_folder: NotRequired[str]
    target_folder: NotRequired[str]
    uid: NotRequired[int]
    mechanism: NotRequired[str]
    tx_id: NotRequired[str | None]
    _audit_detail_reason: NotRequired[str]


def _deny_transfer(
    *,
    reason: str,
    account: str,
    folder: str,
    uid: int | None = None,
) -> MoveCopyResponse:
    res: MoveCopyResponse = {
        "decision": "DENY",
        "reason": reason,
        "account": account,
        "folder": folder,
    }
    if uid is not None:
        res["uid"] = uid
    return res


def _write_audit_intent(context: ServerContext, tool: str, arguments: dict[str, Any]) -> str:
    req_id = context.request_id or str(uuid.uuid4())
    if context.audit is not None:
        context.audit.write(
            {
                "request_id": req_id,
                "phase": "intent",
                "caller_id": context.caller_id,
                "tool": tool,
                "decision": "ALLOW",
                "result": "PENDING",
            }
        )
    return req_id


async def _check_transfer_gates(
    context: ServerContext,
    arguments: dict[str, Any],
    tool: str,
) -> tuple[Any, Any, Any, Any, Any, Any, Any, Any, Any, int | None]:
    src = arguments["source"]
    dst = arguments["target"]
    src_account = str(src["account"])
    src_folder = str(src["folder"])
    src_uid = int(src["uid"])
    expected_uidvalidity = src.get("uidvalidity")
    dst_account = str(dst["account"])
    dst_folder = str(dst["folder"])

    # 1. Source Account & Folder Gate
    src_dec = context.pdp.decide_folder_access(context.caller_id, src_account, src_folder)
    if not src_dec.allowed:
        raise _TransferDenial(src_dec.reason, src_account, src_folder, src_uid)
    assert src_dec.folder_policy is not None
    src_fp = src_dec.folder_policy

    # 2. Target Account & Folder Gate
    dst_dec = context.pdp.decide_folder_access(context.caller_id, dst_account, dst_folder)
    if not dst_dec.allowed:
        raise _TransferDenial(dst_dec.reason, dst_account, dst_folder)
    assert dst_dec.folder_policy is not None
    dst_fp = dst_dec.folder_policy

    # 3. Source Capability Gate
    if tool == "move" and not src_fp.move_out:
        raise _TransferDenial("capability_missing", src_account, src_folder, src_uid)
    if tool == "copy" and not src_fp.copy_out:
        raise _TransferDenial("capability_missing", src_account, src_folder, src_uid)

    # 4. Target Capability Gate
    if not dst_fp.accept_incoming:
        raise _TransferDenial("capability_missing", dst_account, dst_folder)

    # Resolve IMAP accounts & folders
    src_acct_model, src_pwd = await _password_for(context, src_account)
    dst_acct_model, dst_pwd = await _password_for(context, dst_account)
    imap_src = await _resolve_imap_folder(context, src_account, src_folder)
    imap_dst = await _resolve_imap_folder(context, dst_account, dst_folder)

    # Check mailbox existence & UIDVALIDITY
    src_stats = await imap_folder_stats(src_acct_model, src_pwd, imap_src)
    if src_stats.kind == "absent":
        raise _TransferError(
            "folder_absent", "Source folder absent", src_account, src_folder, src_uid
        )
    if src_stats.kind == "select_failed":
        raise _TransferError(
            "select_failed",
            src_stats.imap_response or "SELECT source failed",
            src_account,
            src_folder,
            src_uid,
        )

    if src_account == dst_account:
        dst_stats = await imap_folder_stats(dst_acct_model, dst_pwd, imap_dst)
        if dst_stats.kind == "absent":
            raise _TransferError("folder_absent", "Target folder absent", dst_account, dst_folder)
        if dst_stats.kind == "select_failed":
            raise _TransferError(
                "select_failed",
                dst_stats.imap_response or "SELECT target failed",
                dst_account,
                dst_folder,
            )

    session_uv = context.session_uidvalidity.get((src_account, src_folder))
    if src_stats.uidvalidity is not None:
        if expected_uidvalidity is not None and int(expected_uidvalidity) != src_stats.uidvalidity:
            raise _TransferError(
                "uidvalidity_changed",
                "Observed UIDVALIDITY changed",
                src_account,
                src_folder,
                src_uid,
            )
        if session_uv is not None and session_uv != src_stats.uidvalidity:
            raise _TransferError(
                "uidvalidity_changed",
                "Observed UIDVALIDITY changed",
                src_account,
                src_folder,
                src_uid,
            )
        context.session_uidvalidity[(src_account, src_folder)] = src_stats.uidvalidity

    # Fetch envelope for per-message evaluation
    envelope = await imap_fetch_envelope(src_acct_model, src_pwd, imap_src, src_uid)
    if envelope is None:
        raise _TransferError(
            "not_found",
            "Message not found",
            src_account,
            src_folder,
            src_uid,
            detail_reason="object_missing",
        )

    facts = _facts_from_envelope(envelope)
    eff_src_vis, eff_src_reason = context.pdp.evaluate_message_effective(
        context.caller_id, src_account, src_folder, facts
    )

    if level_rank(eff_src_vis) < level_rank("METADATA"):
        detail_cause = (
            "message_capped" if eff_src_reason == "rule_matched" else "message_not_granted"
        )
        raise _TransferError(
            "not_found",
            "Message not found",
            src_account,
            src_folder,
            src_uid,
            detail_reason=detail_cause,
        )

    if level_rank(eff_src_vis) < level_rank("ENVELOPE"):
        raise _TransferDenial("visibility_below_ENVELOPE", src_account, src_folder, src_uid)

    # Flow monotonicity: eff_target(m) <= eff_source(m)
    eff_dst_vis, _ = context.pdp.evaluate_message_effective(
        context.caller_id, dst_account, dst_folder, facts
    )
    if level_rank(eff_dst_vis) > level_rank(eff_src_vis):
        raise _TransferDenial("relocation_would_escalate", src_account, src_folder, src_uid)

    # Google [Gmail]/Trash or [Gmail]/Spam check
    if _is_google_provider(src_acct_model) and tool == "move":
        canonical_dst = context.pdp.canonical_folder(dst_account, dst_folder)
        if canonical_dst in (
            "[Gmail]/Trash",
            "[Gmail]/Spam",
        ) and not context.pdp.can_move_to_gmail_trash_or_spam(
            context.caller_id, src_account, facts
        ):
            raise _TransferDenial("capability_missing", src_account, src_folder, src_uid)

    return (
        src_acct_model,
        src_pwd,
        imap_src,
        dst_acct_model,
        dst_pwd,
        imap_dst,
        facts,
        envelope,
        eff_src_reason,
        src_stats.uidvalidity,
    )


class _TransferDenial(Exception):
    def __init__(self, reason: str, account: str, folder: str, uid: int | None = None):
        self.reason = reason
        self.account = account
        self.folder = folder
        self.uid = uid


class _TransferError(Exception):
    def __init__(
        self,
        error_type: str,
        detail: str,
        account: str,
        folder: str,
        uid: int | None = None,
        detail_reason: str | None = None,
    ):
        self.error_type = error_type
        self.detail = detail
        self.account = account
        self.folder = folder
        self.uid = uid
        self.detail_reason = detail_reason


async def handle_move(context: ServerContext, arguments: dict[str, Any]) -> MoveCopyResponse:
    src = arguments["source"]
    dst = arguments["target"]
    src_account = str(src["account"])
    src_folder = str(src["folder"])
    src_uid = int(src["uid"])
    dst_account = str(dst["account"])
    dst_folder = str(dst["folder"])

    try:
        (
            src_acct_model,
            src_pwd,
            imap_src,
            dst_acct_model,
            dst_pwd,
            imap_dst,
            _facts,
            _envelope,
            eff_src_reason,
            _uidvalidity,
        ) = await _check_transfer_gates(context, arguments, "move")
    except _TransferDenial as e:
        return _deny_transfer(reason=e.reason, account=e.account, folder=e.folder, uid=e.uid)
    except _TransferError as e:
        extra: dict[str, Any] = {"account": e.account, "folder": e.folder}
        if e.uid is not None:
            extra["uid"] = e.uid
        if e.detail_reason is not None:
            extra["_audit_detail_reason"] = e.detail_reason
        return error_envelope(  # type: ignore[return-value]
            error_type=e.error_type, detail=e.detail, extra=extra
        )

    _write_audit_intent(context, "move", arguments)

    # Branch 1: Cross-account move via Saga (ADR 0046)
    if src_account != dst_account:
        if context.saga is None:
            return error_envelope(  # type: ignore[return-value]
                error_type="transient_imap_failure",
                detail="WAL saga not configured",
                extra={
                    "account": src_account,
                    "source_folder": src_folder,
                    "target_folder": dst_folder,
                    "uid": src_uid,
                },
            )
        saga_result = await context.saga.run_cross_account_move(
            caller_id=context.caller_id,
            src_account=src_acct_model,
            src_password=src_pwd,
            src_folder=imap_src,
            src_uid=src_uid,
            dst_account=dst_acct_model,
            dst_password=dst_pwd,
            dst_folder=imap_dst,
            delete_source=True,
        )
        if saga_result.result == "ERROR":
            err_type = saga_result.error_type or "saga_aborted"
            if err_type not in (
                "not_found",
                "folder_absent",
                "select_failed",
                "uidvalidity_changed",
                "append_rejected",
                "append_timeout",
                "append_failed",
                "saga_aborted",
                "transient_imap_failure",
                "unsupported_imap_capability",
            ):
                err_type = "saga_aborted"
            return error_envelope(  # type: ignore[return-value]
                error_type=err_type,
                detail=saga_result.error_type or "Saga move failed",
                extra={
                    "account": src_account,
                    "source_folder": src_folder,
                    "target_folder": dst_folder,
                    "uid": src_uid,
                    "tx_id": saga_result.tx_id,
                    "mechanism": saga_result.mechanism,
                },
            )
        return {
            "decision": "ALLOW",
            "result": "OK",
            "reason": eff_src_reason,
            "account": src_account,
            "source_folder": src_folder,
            "target_folder": dst_folder,
            "uid": src_uid,
            "mechanism": saga_result.mechanism,
            "tx_id": saga_result.tx_id,
        }

    # Branch 2: Gmail intra-account label swap (ADR 0040)
    if _is_google_provider(src_acct_model):
        from ..imap_core import _LABEL_TO_FOLDER

        folder_to_label: dict[str, str] = {v: k for k, v in _LABEL_TO_FOLDER.items()}
        src_label = folder_to_label.get(src_folder, src_folder)
        dst_label = folder_to_label.get(dst_folder, dst_folder)
        try:
            await imap_gmail_label_swap(
                src_acct_model, src_pwd, imap_src, src_uid, src_label, dst_label
            )
        except LabelMutationFailed as exc:
            return error_envelope(  # type: ignore[return-value]
                error_type="transient_imap_failure",
                detail=exc.response_text or exc.status,
                extra={
                    "account": src_account,
                    "source_folder": src_folder,
                    "target_folder": dst_folder,
                    "uid": src_uid,
                },
            )
        except Exception:  # noqa: BLE001
            return error_envelope(  # type: ignore[return-value]
                error_type="transient_imap_failure",
                detail="Gmail label swap failed",
                extra={
                    "account": src_account,
                    "source_folder": src_folder,
                    "target_folder": dst_folder,
                    "uid": src_uid,
                },
            )
        return {
            "decision": "ALLOW",
            "result": "OK",
            "reason": eff_src_reason,
            "account": src_account,
            "source_folder": src_folder,
            "target_folder": dst_folder,
            "uid": src_uid,
            "mechanism": "gmail_label_swap",
            "tx_id": None,
        }

    # Branch 3: Standard IMAP intra-account MOVE with UID EXPUNGE fallback (ADR 0046 §3)
    from ..imap_core import move_message_with_fallback

    try:
        mechanism = await move_message_with_fallback(
            src_acct_model, src_pwd, imap_src, src_uid, imap_dst
        )
    except RuntimeError as exc:
        msg = str(exc)
        if "unsupported" in msg.lower() or "neither move nor uidplus" in msg.lower():
            return error_envelope(  # type: ignore[return-value]
                error_type="unsupported_imap_capability",
                detail="Server supports neither MOVE nor UIDPLUS",
                extra={
                    "account": src_account,
                    "source_folder": src_folder,
                    "target_folder": dst_folder,
                    "uid": src_uid,
                },
            )
        return error_envelope(  # type: ignore[return-value]
            error_type="transient_imap_failure",
            detail=msg,
            extra={
                "account": src_account,
                "source_folder": src_folder,
                "target_folder": dst_folder,
                "uid": src_uid,
            },
        )

    return {
        "decision": "ALLOW",
        "result": "OK",
        "reason": eff_src_reason,
        "account": src_account,
        "source_folder": src_folder,
        "target_folder": dst_folder,
        "uid": src_uid,
        "mechanism": mechanism,
        "tx_id": None,
    }


async def handle_copy(context: ServerContext, arguments: dict[str, Any]) -> MoveCopyResponse:
    src = arguments["source"]
    dst = arguments["target"]
    src_account = str(src["account"])
    src_folder = str(src["folder"])
    src_uid = int(src["uid"])
    dst_account = str(dst["account"])
    dst_folder = str(dst["folder"])

    try:
        (
            src_acct_model,
            src_pwd,
            imap_src,
            dst_acct_model,
            dst_pwd,
            imap_dst,
            _facts,
            _envelope,
            eff_src_reason,
            _uidvalidity,
        ) = await _check_transfer_gates(context, arguments, "copy")
    except _TransferDenial as e:
        return _deny_transfer(reason=e.reason, account=e.account, folder=e.folder, uid=e.uid)
    except _TransferError as e:
        extra: dict[str, Any] = {"account": e.account, "folder": e.folder}
        if e.uid is not None:
            extra["uid"] = e.uid
        if e.detail_reason is not None:
            extra["_audit_detail_reason"] = e.detail_reason
        return error_envelope(  # type: ignore[return-value]
            error_type=e.error_type, detail=e.detail, extra=extra
        )

    _write_audit_intent(context, "copy", arguments)

    # Cross-account copy via Saga (without delete_source)
    if src_account != dst_account:
        if context.saga is None:
            return error_envelope(  # type: ignore[return-value]
                error_type="transient_imap_failure",
                detail="WAL saga not configured",
                extra={
                    "account": src_account,
                    "source_folder": src_folder,
                    "target_folder": dst_folder,
                    "uid": src_uid,
                },
            )
        saga_result = await context.saga.run_cross_account_move(
            caller_id=context.caller_id,
            src_account=src_acct_model,
            src_password=src_pwd,
            src_folder=imap_src,
            src_uid=src_uid,
            dst_account=dst_acct_model,
            dst_password=dst_pwd,
            dst_folder=imap_dst,
            delete_source=False,
        )
        if saga_result.result == "ERROR":
            err_type = saga_result.error_type or "saga_aborted"
            if err_type not in (
                "not_found",
                "folder_absent",
                "select_failed",
                "append_rejected",
                "append_timeout",
                "append_failed",
                "saga_aborted",
                "transient_imap_failure",
            ):
                err_type = "saga_aborted"
            return error_envelope(  # type: ignore[return-value]
                error_type=err_type,
                detail=saga_result.error_type or "Saga copy failed",
                extra={
                    "account": src_account,
                    "source_folder": src_folder,
                    "target_folder": dst_folder,
                    "uid": src_uid,
                    "tx_id": saga_result.tx_id,
                    "mechanism": saga_result.mechanism,
                },
            )
        return {
            "decision": "ALLOW",
            "result": "OK",
            "reason": eff_src_reason,
            "account": src_account,
            "source_folder": src_folder,
            "target_folder": dst_folder,
            "uid": src_uid,
            "mechanism": saga_result.mechanism,
            "tx_id": saga_result.tx_id,
        }

    # Intra-account native copy
    from ..imap_core import copy_message as imap_copy_message

    ok = await imap_copy_message(src_acct_model, src_pwd, imap_src, src_uid, imap_dst)
    if not ok:
        return error_envelope(  # type: ignore[return-value]
            error_type="transient_imap_failure",
            detail="Native copy failed",
            extra={
                "account": src_account,
                "source_folder": src_folder,
                "target_folder": dst_folder,
                "uid": src_uid,
            },
        )

    return {
        "decision": "ALLOW",
        "result": "OK",
        "reason": eff_src_reason,
        "account": src_account,
        "source_folder": src_folder,
        "target_folder": dst_folder,
        "uid": src_uid,
        "mechanism": "native_copy",
    }
