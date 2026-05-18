"""Per-message fetch handlers: envelope, body, headers, attachment."""

from __future__ import annotations

from typing import Any, Literal, NotRequired, TypedDict, TYPE_CHECKING

from ..imap_core import (
    fetch_body as imap_fetch_body,
    fetch_envelope as imap_fetch_envelope,
    fetch_full_message as imap_fetch_full_message,
)
from ..policy import evaluate_message_against_folder, level_rank
from ._common import (
    _facts_from_envelope,
    _password_for,
    _resolve_imap_folder,
)

if TYPE_CHECKING:
    from ..context import ServerContext


class AttachmentMetaEntry(TypedDict):
    index: int
    filename: str | None
    mime_type: str
    size_bytes: int


class FetchResponse(TypedDict, total=False):
    """Union shape over the four fetch handlers.

    Each handler returns a strict subset of these keys; ``total=False``
    keeps every field optional so that a single TypedDict can capture
    the union without splitting it per-tool. The non-public ``_blob*``
    keys are stripped by the dispatcher's ``_emit`` before the wire
    response is built (ADR 0021 §8).
    """

    decision: Literal["ALLOW", "DENY"]
    result: NotRequired[Literal["OK", "ERROR"]]
    error_type: NotRequired[str | None]
    reason: NotRequired[str]
    visibility_applied: NotRequired[str]
    matched_rule_index: NotRequired[int | None]
    account: str
    folder: str
    uid: int
    # Envelope payload
    from_: NotRequired[str]  # not actually used; "from" is reserved keyword
    to: NotRequired[tuple[str, ...] | list[str]]
    subject: NotRequired[str]
    message_id: NotRequired[str | None]
    date: NotRequired[str | None]
    body: NotRequired[str | None]
    text_body: NotRequired[str]
    attachments: NotRequired[list[AttachmentMetaEntry] | list[Any] | None]
    redacted_fields: NotRequired[list[str]]
    redaction_reason: NotRequired[str | None]
    # fetch_headers payload
    headers: NotRequired[dict[str, str]]
    # fetch_attachment selected-part payload
    part_id: NotRequired[int]
    mime_type: NotRequired[str]
    size_bytes: NotRequired[int]
    content_hash: NotRequired[str]
    # Dispatcher-stripped blob keys (private)
    _blob: NotRequired[str]
    _blob_mime_type: NotRequired[str]
    _blob_uri: NotRequired[str]
    # Private hint for audit sender-hashing in dispatch
    _matched_sender: NotRequired[str]


def _deny_fetch(
    *,
    reason: str,
    account: str,
    folder: str,
    uid: int,
    matched_sender: str | None = None,
) -> FetchResponse:
    response: FetchResponse = {
        "decision": "DENY",
        "reason": reason,
        "account": account,
        "folder": folder,
        "uid": uid,
    }
    if matched_sender is not None:
        response["_matched_sender"] = matched_sender
    return response


def _error_fetch(
    *,
    error_type: str,
    account: str,
    folder: str,
    uid: int,
    reason: str | None = None,
) -> FetchResponse:
    response: FetchResponse = {
        "decision": "ALLOW",
        "result": "ERROR",
        "error_type": error_type,
        "account": account,
        "folder": folder,
        "uid": uid,
    }
    if reason is not None:
        response["reason"] = reason
    return response


async def handle_fetch_envelope(
    context: "ServerContext", arguments: dict[str, Any]
) -> FetchResponse:
    account_id = str(arguments["account"])
    folder_path = str(arguments["folder"])
    uid = int(arguments["uid"])
    folder_decision = context.pdp.decide_folder_access(context.caller_id, account_id, folder_path)
    if not folder_decision.allowed:
        return _deny_fetch(
            reason=folder_decision.reason, account=account_id, folder=folder_path, uid=uid
        )
    assert folder_decision.folder_policy is not None
    account, password = await _password_for(context, account_id)
    imap_folder = await _resolve_imap_folder(context, account_id, folder_path)
    envelope = await imap_fetch_envelope(account, password, imap_folder, uid)
    if envelope is None:
        return _error_fetch(
            error_type="uid_not_found",
            account=account_id,
            folder=folder_path,
            uid=uid,
            reason=folder_decision.reason,
        )
    facts = _facts_from_envelope(envelope)
    message_decision = evaluate_message_against_folder(folder_decision.folder_policy, facts=facts)
    if not message_decision.allowed:
        return _deny_fetch(
            reason=message_decision.reason,
            account=account_id,
            folder=folder_path,
            uid=uid,
            matched_sender=facts.from_address,
        )
    minimum_for_tool = level_rank("ENVELOPE")
    if level_rank(message_decision.visibility) < minimum_for_tool:
        return _deny_fetch(
            reason="visibility_below_ENVELOPE", account=account_id, folder=folder_path, uid=uid
        )
    granted = level_rank(message_decision.visibility)
    body_visible = granted >= level_rank("BODY")
    attachments_visible = granted >= level_rank("FULL")
    redacted: list[str] = []
    if not body_visible:
        redacted.append("body")
    if not attachments_visible:
        redacted.append("attachments")
    redaction_reason = None
    if redacted:
        redaction_reason = "visibility_below_BODY" if not body_visible else "visibility_below_FULL"
    return {
        "decision": "ALLOW",
        "reason": message_decision.reason,
        "visibility_applied": message_decision.visibility,
        "matched_rule_index": message_decision.matched_rule_index,
        "account": account_id,
        "folder": folder_path,
        "uid": uid,
        "from": envelope.from_address,  # type: ignore[typeddict-unknown-key]
        "to": envelope.to_addresses,
        "subject": envelope.subject,
        "message_id": envelope.message_id,
        "date": envelope.date,
        "body": None if not body_visible else "",
        "attachments": None if not attachments_visible else [],
        "redacted_fields": redacted,
        "redaction_reason": redaction_reason,
    }


async def handle_fetch_body(context: "ServerContext", arguments: dict[str, Any]) -> FetchResponse:
    account_id = str(arguments["account"])
    folder_path = str(arguments["folder"])
    uid = int(arguments["uid"])
    folder_decision = context.pdp.decide_folder_access(context.caller_id, account_id, folder_path)
    if not folder_decision.allowed:
        return _deny_fetch(
            reason=folder_decision.reason, account=account_id, folder=folder_path, uid=uid
        )
    assert folder_decision.folder_policy is not None
    account, password = await _password_for(context, account_id)
    imap_folder = await _resolve_imap_folder(context, account_id, folder_path)
    result = await imap_fetch_body(account, password, imap_folder, uid)
    if result is None:
        return _error_fetch(
            error_type="uid_not_found",
            account=account_id,
            folder=folder_path,
            uid=uid,
            reason=folder_decision.reason,
        )
    envelope, body_text, msg = result
    facts = _facts_from_envelope(envelope)
    message_decision = evaluate_message_against_folder(folder_decision.folder_policy, facts=facts)
    if not message_decision.allowed:
        return _deny_fetch(
            reason=message_decision.reason, account=account_id, folder=folder_path, uid=uid
        )
    minimum_for_tool = level_rank("BODY")
    if level_rank(message_decision.visibility) < minimum_for_tool:
        return _deny_fetch(
            reason="visibility_below_BODY", account=account_id, folder=folder_path, uid=uid
        )
    full_visible = level_rank(message_decision.visibility) >= level_rank("FULL")
    attachments_meta: list[AttachmentMetaEntry] | None = None
    if full_visible:
        all_parts = _walk_attachment_parts(msg)
        attachments_meta = []
        for i, p in enumerate(all_parts):
            p_payload = p.get_payload(decode=True) or b""
            attachments_meta.append(
                {
                    "index": i,
                    "filename": p.get_filename(),
                    "mime_type": p.get_content_type(),
                    "size_bytes": len(p_payload),
                }
            )
    return {
        "decision": "ALLOW",
        "reason": message_decision.reason,
        "visibility_applied": message_decision.visibility,
        "account": account_id,
        "folder": folder_path,
        "uid": uid,
        "from": envelope.from_address,  # type: ignore[typeddict-unknown-key]
        "subject": envelope.subject,
        "text_body": body_text,
        "matched_rule_index": message_decision.matched_rule_index,
        "attachments": attachments_meta,
        "redacted_fields": ["attachments"] if not full_visible else [],
        "redaction_reason": "visibility_below_FULL" if not full_visible else None,
    }


async def handle_fetch_headers(
    context: "ServerContext", arguments: dict[str, Any]
) -> FetchResponse:
    import email

    account_id = str(arguments["account"])
    folder_path = str(arguments["folder"])
    uid = int(arguments["uid"])
    folder_decision = context.pdp.decide_folder_access(context.caller_id, account_id, folder_path)
    if not folder_decision.allowed:
        return _deny_fetch(
            reason=folder_decision.reason, account=account_id, folder=folder_path, uid=uid
        )
    assert folder_decision.folder_policy is not None
    account, password = await _password_for(context, account_id)
    imap_folder = await _resolve_imap_folder(context, account_id, folder_path)
    envelope = await imap_fetch_envelope(account, password, imap_folder, uid)
    if envelope is None:
        return _error_fetch(
            error_type="uid_not_found", account=account_id, folder=folder_path, uid=uid
        )
    facts = _facts_from_envelope(envelope)
    message_decision = evaluate_message_against_folder(folder_decision.folder_policy, facts=facts)
    if not message_decision.allowed:
        return _deny_fetch(
            reason=message_decision.reason, account=account_id, folder=folder_path, uid=uid
        )
    if level_rank(message_decision.visibility) < level_rank("HEADERS"):
        return _deny_fetch(
            reason="visibility_below_HEADERS", account=account_id, folder=folder_path, uid=uid
        )
    raw = await imap_fetch_full_message(account, password, imap_folder, uid)
    headers: dict[str, str] = {}
    if raw is not None:
        msg = email.message_from_bytes(raw)
        for name, value in msg.items():
            headers[name] = value
    return {
        "decision": "ALLOW",
        "reason": message_decision.reason,
        "visibility_applied": message_decision.visibility,
        "account": account_id,
        "folder": folder_path,
        "uid": uid,
        "headers": headers,
    }


def _walk_attachment_parts(msg: Any) -> list[Any]:
    """Walk the MIME tree and return parts that count as an
    attachment from the agent's perspective: any non-multipart part
    whose Content-Disposition is ``attachment``, or which is
    ``inline`` but carries a filename, or which is a non-text part
    with a filename. The same rule is used both for the meta-list
    ``part_id is None`` branch and the selection branch."""
    all_parts: list[Any] = []
    for part in msg.walk():
        if part.get_content_maintype() == "multipart":
            continue
        disposition = (part.get("Content-Disposition") or "").lower()
        is_attachment = disposition.startswith("attachment")
        is_inline_file = disposition.startswith("inline") and part.get_filename() is not None
        is_named_binary = (
            part.get_content_maintype() not in ("text", "multipart")
            and part.get_filename() is not None
        )
        if is_attachment or is_inline_file or is_named_binary:
            all_parts.append(part)
    return all_parts


def _select_attachment_part(all_parts: list[Any], part_id: int) -> Any | None:
    """Pick the MIME part at the given 0-based index. Returns None
    when out of range; callers translate that into
    ``attachment_not_found``."""
    if 0 <= part_id < len(all_parts):
        return all_parts[part_id]
    return None


def _build_attachment_blob_response(
    part: Any,
    *,
    index: int,
    message_decision: Any,
    account_id: str,
    folder_path: str,
    uid: int,
) -> FetchResponse:
    """Encode a selected MIME part into the ALLOW + blob response
    that the dispatcher's ``_emit`` will split into a TextContent
    metadata header plus an EmbeddedResource blob."""
    import base64
    import hashlib

    payload = part.get_payload(decode=True) or b""
    mime_type = part.get_content_type()
    content_hash = hashlib.sha256(payload).hexdigest()
    filename = part.get_filename() or "attachment"
    return {
        "decision": "ALLOW",
        "reason": message_decision.reason,
        "visibility_applied": message_decision.visibility,
        "account": account_id,
        "folder": folder_path,
        "uid": uid,
        "part_id": index,
        "mime_type": mime_type,
        "size_bytes": len(payload),
        "content_hash": content_hash,
        "_blob": base64.b64encode(payload).decode("ascii"),
        "_blob_mime_type": mime_type,
        "_blob_uri": f"attachment://{account_id}/{folder_path}/{uid}/{filename}",
    }


async def handle_fetch_attachment(
    context: "ServerContext", arguments: dict[str, Any]
) -> FetchResponse:
    """Orchestrator: validate, fetch the raw message, walk for
    attachment parts, then either list metadata (``part_id is None``)
    or return the selected part's bytes as a blob."""
    import email

    account_id = str(arguments["account"])
    folder_path = str(arguments["folder"])
    uid = int(arguments["uid"])
    raw_part_id = arguments.get("part_id")
    part_id: int | None = int(raw_part_id) if raw_part_id is not None else None
    folder_decision = context.pdp.decide_folder_access(context.caller_id, account_id, folder_path)
    if not folder_decision.allowed:
        return _deny_fetch(
            reason=folder_decision.reason, account=account_id, folder=folder_path, uid=uid
        )
    assert folder_decision.folder_policy is not None
    account, password = await _password_for(context, account_id)
    imap_folder = await _resolve_imap_folder(context, account_id, folder_path)
    envelope = await imap_fetch_envelope(account, password, imap_folder, uid)
    if envelope is None:
        return _error_fetch(
            error_type="uid_not_found", account=account_id, folder=folder_path, uid=uid
        )
    facts = _facts_from_envelope(envelope)
    message_decision = evaluate_message_against_folder(folder_decision.folder_policy, facts=facts)
    if not message_decision.allowed:
        return _deny_fetch(
            reason=message_decision.reason, account=account_id, folder=folder_path, uid=uid
        )
    if level_rank(message_decision.visibility) < level_rank("FULL"):
        return _deny_fetch(
            reason="visibility_below_FULL", account=account_id, folder=folder_path, uid=uid
        )
    raw = await imap_fetch_full_message(account, password, imap_folder, uid)
    if raw is None:
        return _error_fetch(
            error_type="uid_not_found", account=account_id, folder=folder_path, uid=uid
        )
    msg = email.message_from_bytes(raw)
    all_parts = _walk_attachment_parts(msg)
    if not all_parts:
        return _error_fetch(
            error_type="attachment_not_found", account=account_id, folder=folder_path, uid=uid
        )

    if part_id is None:
        attachments_meta: list[AttachmentMetaEntry] = []
        for i, p in enumerate(all_parts):
            p_payload = p.get_payload(decode=True) or b""
            attachments_meta.append(
                {
                    "index": i,
                    "filename": p.get_filename(),
                    "mime_type": p.get_content_type(),
                    "size_bytes": len(p_payload),
                }
            )
        return {
            "decision": "ALLOW",
            "reason": message_decision.reason,
            "visibility_applied": message_decision.visibility,
            "account": account_id,
            "folder": folder_path,
            "uid": uid,
            "attachments": attachments_meta,
        }

    selected_part = _select_attachment_part(all_parts, part_id)
    if selected_part is None:
        return _error_fetch(
            error_type="attachment_not_found", account=account_id, folder=folder_path, uid=uid
        )
    return _build_attachment_blob_response(
        selected_part,
        index=part_id,
        message_decision=message_decision,
        account_id=account_id,
        folder_path=folder_path,
        uid=uid,
    )
