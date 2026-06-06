"""Attachment-rewrite handlers: add, replace, delete.

All three tools delegate to the shared `_attachment_modify` saga
wrapper which performs the FETCH-APPEND-DELETE rewrite through the
WAL-backed SagaManager.run_message_rewrite. Each public handler
defines the bytes-transform callback and passes it down.
"""

from __future__ import annotations

import base64
from typing import Any, Literal, NotRequired, TypedDict, TYPE_CHECKING

from ..imap_core import (
    mime_add_attachment,
    mime_delete_attachment,
    mime_replace_attachment,
)
from ._common import _password_for, _resolve_imap_folder, error_envelope

if TYPE_CHECKING:
    from ..context import ServerContext


class AttachmentModifyResponse(TypedDict, total=False):
    decision: Literal["ALLOW", "DENY"]
    result: NotRequired[Literal["OK", "ERROR"]]
    reason: NotRequired[str]
    error: NotRequired[dict[str, str]]
    account: str
    folder: str
    uid: NotRequired[int]
    old_uid: NotRequired[int]
    new_uid: NotRequired[int]
    missing_capability: NotRequired[str]
    mechanism: NotRequired[str]
    tx_id: NotRequired[str | None]


def _deny_attachment(
    *,
    reason: str,
    account: str,
    folder: str,
    uid: int,
    missing_capability: str | None = None,
) -> AttachmentModifyResponse:
    response: AttachmentModifyResponse = {
        "decision": "DENY",
        "reason": reason,
        "account": account,
        "folder": folder,
        "uid": uid,
    }
    if missing_capability is not None:
        response["missing_capability"] = missing_capability
    return response


def _error_attachment(
    *, error_type: str, account: str, folder: str, uid: int, detail: str = ""
) -> AttachmentModifyResponse:
    return error_envelope(  # type: ignore[return-value]
        error_type=error_type,
        detail=detail,
        extra={"account": account, "folder": folder, "uid": uid},
    )


async def _attachment_modify(
    context: "ServerContext",
    arguments: dict[str, Any],
    tool_name: str,
    build_transform: Any,
) -> AttachmentModifyResponse:
    account_id = str(arguments["account"])
    folder_path = str(arguments["folder"])
    uid = int(arguments["uid"])
    folder_decision = context.pdp.decide_folder_access(context.caller_id, account_id, folder_path)
    if not folder_decision.allowed:
        return _deny_attachment(
            reason=folder_decision.reason, account=account_id, folder=folder_path, uid=uid
        )
    assert folder_decision.folder_policy is not None
    if not folder_decision.folder_policy.modify_message:
        return _deny_attachment(
            reason="capability_missing",
            account=account_id,
            folder=folder_path,
            uid=uid,
            missing_capability="modify_message",
        )
    try:
        transform = build_transform(arguments)
    except (FileNotFoundError, LookupError, KeyError) as exc:
        # Build-time failures here are exclusively "the named attachment
        # isn't on the message" — the only data the build_transform
        # currently inspects.  Anything else propagates as an unhandled
        # exception so it's visible during development.
        return _error_attachment(
            error_type="attachment_not_found",
            detail=str(exc),
            account=account_id,
            folder=folder_path,
            uid=uid,
        )
    account, password = await _password_for(context, account_id)
    imap_folder = await _resolve_imap_folder(context, account_id, folder_path)
    saga_result = await context.saga.run_message_rewrite(
        caller_id=context.caller_id,
        account=account,
        password=password,
        folder=imap_folder,
        uid=uid,
        transform=transform,
    )
    # attachment_modify response keeps its tool-specific shape (mechanism,
    # tx_id, old_uid) and routes errors through the unified envelope.
    if saga_result.result == "ERROR":
        # Map the saga's free-form error string to the closed enumeration
        # of ADR 0027.  Anything unrecognized becomes `rewrite_failed`.
        et = saga_result.error_type or "rewrite_failed"
        if et not in ("uid_not_found", "attachment_not_found", "rewrite_failed"):
            et = "rewrite_failed"
        return _error_attachment(
            error_type=et,
            account=account_id,
            folder=folder_path,
            uid=uid,
            detail=saga_result.error_type or "",
        )
    result: AttachmentModifyResponse = {
        "decision": "ALLOW",
        "result": "OK",
        "mechanism": saga_result.mechanism,
        "tx_id": saga_result.tx_id,
        "account": account_id,
        "folder": folder_path,
        "old_uid": uid,
    }
    tx = context.saga.wal.get(saga_result.tx_id)
    if tx and tx.get("target_uid"):
        result["new_uid"] = tx["target_uid"]
    return result


async def handle_add_attachment(
    context: "ServerContext", arguments: dict[str, Any]
) -> AttachmentModifyResponse:
    def _build(args: dict[str, Any]) -> Any:
        content = base64.b64decode(args["content"])
        filename = args["filename"]
        mime_type = args["mime_type"]

        def transform(rfc822: bytes) -> bytes:
            return mime_add_attachment(rfc822, filename, mime_type, content)

        return transform

    return await _attachment_modify(context, arguments, "add_attachment", _build)


async def handle_replace_attachment(
    context: "ServerContext", arguments: dict[str, Any]
) -> AttachmentModifyResponse:
    def _build(args: dict[str, Any]) -> Any:
        new_content = base64.b64decode(args["new_content"])
        filename = args["filename"]
        new_mime_type = args.get("new_mime_type")
        new_filename = args.get("new_filename")

        def transform(rfc822: bytes) -> bytes:
            return mime_replace_attachment(
                rfc822,
                filename,
                new_content,
                new_mime_type=new_mime_type,
                new_filename=new_filename,
            )

        return transform

    return await _attachment_modify(context, arguments, "replace_attachment", _build)


async def handle_delete_attachment(
    context: "ServerContext", arguments: dict[str, Any]
) -> AttachmentModifyResponse:
    def _build(args: dict[str, Any]) -> Any:
        filename = args["filename"]

        def transform(rfc822: bytes) -> bytes:
            return mime_delete_attachment(rfc822, filename)

        return transform

    return await _attachment_modify(context, arguments, "delete_attachment", _build)
