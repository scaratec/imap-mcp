"""Per-message fetch handlers: envelope, body, headers, attachment."""

from __future__ import annotations

from typing import Any, TYPE_CHECKING

from ..imap_core import (
    fetch_body as imap_fetch_body,
    fetch_envelope as imap_fetch_envelope,
    fetch_full_message as imap_fetch_full_message,
)
from ..policy import evaluate_message_against_folder, level_rank
from ._common import (
    _deny,
    _error,
    _facts_from_envelope,
    _password_for,
    _resolve_imap_folder,
)

if TYPE_CHECKING:
    from ..context import ServerContext


async def handle_fetch_envelope(
    context: "ServerContext", arguments: dict[str, Any]
) -> dict[str, Any]:
    account_id = str(arguments["account"])
    folder_path = str(arguments["folder"])
    uid = int(arguments["uid"])
    base = {"account": account_id, "folder": folder_path, "uid": uid}
    folder_decision = context.pdp.decide_folder_access(context.caller_id, account_id, folder_path)
    if not folder_decision.allowed:
        return _deny(reason=folder_decision.reason, **base)
    assert folder_decision.folder_policy is not None
    account, password = await _password_for(context, account_id)
    imap_folder = await _resolve_imap_folder(context, account_id, folder_path)
    envelope = await imap_fetch_envelope(account, password, imap_folder, uid)
    if envelope is None:
        return _error(
            error_type="uid_not_found",
            reason=folder_decision.reason,
            **base,
        )
    facts = _facts_from_envelope(envelope)
    message_decision = evaluate_message_against_folder(folder_decision.folder_policy, facts=facts)
    if not message_decision.allowed:
        return _deny(
            reason=message_decision.reason,
            _matched_sender=facts.from_address,
            **base,
        )
    minimum_for_tool = level_rank("ENVELOPE")
    if level_rank(message_decision.visibility) < minimum_for_tool:
        return _deny(reason="visibility_below_ENVELOPE", **base)
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
        "from": envelope.from_address,
        "to": envelope.to_addresses,
        "subject": envelope.subject,
        "message_id": envelope.message_id,
        "date": envelope.date,
        "body": None if not body_visible else "",
        "attachments": None if not attachments_visible else [],
        "redacted_fields": redacted,
        "redaction_reason": redaction_reason,
    }


async def handle_fetch_body(context: "ServerContext", arguments: dict[str, Any]) -> dict[str, Any]:
    account_id = str(arguments["account"])
    folder_path = str(arguments["folder"])
    uid = int(arguments["uid"])
    base = {"account": account_id, "folder": folder_path, "uid": uid}
    folder_decision = context.pdp.decide_folder_access(context.caller_id, account_id, folder_path)
    if not folder_decision.allowed:
        return _deny(reason=folder_decision.reason, **base)
    assert folder_decision.folder_policy is not None
    account, password = await _password_for(context, account_id)
    imap_folder = await _resolve_imap_folder(context, account_id, folder_path)
    result = await imap_fetch_body(account, password, imap_folder, uid)
    if result is None:
        return _error(
            error_type="uid_not_found",
            reason=folder_decision.reason,
            **base,
        )
    envelope, body_text = result
    facts = _facts_from_envelope(envelope)
    message_decision = evaluate_message_against_folder(folder_decision.folder_policy, facts=facts)
    if not message_decision.allowed:
        return _deny(reason=message_decision.reason, **base)
    minimum_for_tool = level_rank("BODY")
    if level_rank(message_decision.visibility) < minimum_for_tool:
        return _deny(reason="visibility_below_BODY", **base)
    return {
        "decision": "ALLOW",
        "reason": message_decision.reason,
        "visibility_applied": message_decision.visibility,
        "account": account_id,
        "folder": folder_path,
        "uid": uid,
        "from": envelope.from_address,
        "subject": envelope.subject,
        "text_body": body_text,
        "matched_rule_index": message_decision.matched_rule_index,
        "attachments": None if level_rank(message_decision.visibility) < level_rank("FULL") else [],
        "redacted_fields": (
            ["attachments"] if level_rank(message_decision.visibility) < level_rank("FULL") else []
        ),
        "redaction_reason": (
            "visibility_below_FULL"
            if level_rank(message_decision.visibility) < level_rank("FULL")
            else None
        ),
    }


async def handle_fetch_headers(
    context: "ServerContext", arguments: dict[str, Any]
) -> dict[str, Any]:
    import email

    account_id = str(arguments["account"])
    folder_path = str(arguments["folder"])
    uid = int(arguments["uid"])
    base = {"account": account_id, "folder": folder_path, "uid": uid}
    folder_decision = context.pdp.decide_folder_access(context.caller_id, account_id, folder_path)
    if not folder_decision.allowed:
        return _deny(reason=folder_decision.reason, **base)
    assert folder_decision.folder_policy is not None
    account, password = await _password_for(context, account_id)
    imap_folder = await _resolve_imap_folder(context, account_id, folder_path)
    envelope = await imap_fetch_envelope(account, password, imap_folder, uid)
    if envelope is None:
        return _error(error_type="uid_not_found", **base)
    facts = _facts_from_envelope(envelope)
    message_decision = evaluate_message_against_folder(folder_decision.folder_policy, facts=facts)
    if not message_decision.allowed:
        return _deny(reason=message_decision.reason, **base)
    if level_rank(message_decision.visibility) < level_rank("HEADERS"):
        return _deny(reason="visibility_below_HEADERS", **base)
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


async def handle_fetch_attachment(
    context: "ServerContext", arguments: dict[str, Any]
) -> dict[str, Any]:
    import email
    import hashlib

    account_id = str(arguments["account"])
    folder_path = str(arguments["folder"])
    uid = int(arguments["uid"])
    part_id = arguments.get("part_id")
    base = {"account": account_id, "folder": folder_path, "uid": uid}
    folder_decision = context.pdp.decide_folder_access(context.caller_id, account_id, folder_path)
    if not folder_decision.allowed:
        return _deny(reason=folder_decision.reason, **base)
    assert folder_decision.folder_policy is not None
    account, password = await _password_for(context, account_id)
    imap_folder = await _resolve_imap_folder(context, account_id, folder_path)
    envelope = await imap_fetch_envelope(account, password, imap_folder, uid)
    if envelope is None:
        return _error(error_type="uid_not_found", **base)
    facts = _facts_from_envelope(envelope)
    message_decision = evaluate_message_against_folder(folder_decision.folder_policy, facts=facts)
    if not message_decision.allowed:
        return _deny(reason=message_decision.reason, **base)
    if level_rank(message_decision.visibility) < level_rank("FULL"):
        return _deny(reason="visibility_below_FULL", **base)
    raw = await imap_fetch_full_message(account, password, imap_folder, uid)
    if raw is None:
        return _error(error_type="uid_not_found", **base)
    msg = email.message_from_bytes(raw)
    all_parts = []
    for part in msg.walk():
        if part.get_content_maintype() == "multipart":
            continue
        disposition = (part.get("Content-Disposition") or "").lower()
        is_attachment = disposition.startswith("attachment")
        is_inline_file = (
            disposition.startswith("inline")
            and part.get_filename() is not None
        )
        is_named_binary = (
            part.get_content_maintype() not in ("text", "multipart")
            and part.get_filename() is not None
        )
        if is_attachment or is_inline_file or is_named_binary:
            all_parts.append(part)

    if not all_parts:
        return _error(error_type="attachment_not_found", **base)

    if part_id is None:
        attachments_meta = []
        for p in all_parts:
            p_payload = p.get_payload(decode=True) or b""
            attachments_meta.append({
                "part_id": p.get_filename() or "attachment",
                "mime_type": p.get_content_type(),
                "size_bytes": len(p_payload),
            })
        return {
            "decision": "ALLOW",
            "reason": message_decision.reason,
            "visibility_applied": message_decision.visibility,
            "account": account_id,
            "folder": folder_path,
            "uid": uid,
            "attachments": attachments_meta,
        }

    selected_part = None
    for p in all_parts:
        if p.get_filename() == part_id:
            selected_part = p
            break
    if selected_part is None:
        return _error(error_type="attachment_not_found", **base)
    import base64

    payload = selected_part.get_payload(decode=True) or b""
    mime_type = selected_part.get_content_type()
    content_hash = hashlib.sha256(payload).hexdigest()
    filename = selected_part.get_filename() or "attachment"
    return {
        "decision": "ALLOW",
        "reason": message_decision.reason,
        "visibility_applied": message_decision.visibility,
        "account": account_id,
        "folder": folder_path,
        "uid": uid,
        "part_id": filename,
        "mime_type": mime_type,
        "size_bytes": len(payload),
        "content_hash": content_hash,
        "_blob": base64.b64encode(payload).decode("ascii"),
        "_blob_mime_type": mime_type,
        "_blob_uri": f"attachment://{account_id}/{folder_path}/{uid}/{filename}",
    }
