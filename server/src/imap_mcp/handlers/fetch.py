"""Per-message fetch handlers: envelope, body, headers, attachment, list_attachments.

Adheres to:
- ADR 0040: Message-centric policy evaluation, Google multi-label composition, object_id
- ADR 0041: Direction-aware predicate grammar
- ADR 0043: Non-interference transparency, field projection, and redacted_fields
- ADR 0044: Normalized response envelope and opaque not_found
- ADR 0047: Symlink-safe per-caller attachment file delivery
"""

from __future__ import annotations

import email
from typing import TYPE_CHECKING, Any, Literal, NotRequired, TypedDict

from ..imap_core import (
    fetch_body as imap_fetch_body,
)
from ..imap_core import (
    fetch_envelope as imap_fetch_envelope,
)
from ..imap_core import (
    fetch_full_message as imap_fetch_full_message,
)
from ..imap_core import (
    folder_stats as imap_folder_stats,
)
from ..imap_core import (
    gmail_fetch_msgid as imap_gmail_fetch_msgid,
)
from ..policy import MessageFacts, level_rank
from ._common import (
    _facts_from_envelope,
    _is_google_provider,
    _password_for,
    _resolve_imap_folder,
    _walk_attachment_parts,
    decode_mime_words,
    deliver_attachment_to_sink,
    error_envelope,
)

if TYPE_CHECKING:
    from ..context import ServerContext


class AttachmentMetaEntry(TypedDict):
    index: int
    filename: str | None
    mime_type: str
    size_bytes: int


FetchResponse = TypedDict(
    "FetchResponse",
    {
        "decision": Literal["ALLOW", "DENY"],
        "result": NotRequired[Literal["OK", "ERROR"]],
        "reason": NotRequired[str],
        "visibility_applied": NotRequired[str],
        "error": NotRequired[dict[str, str]],
        "account": str,
        "folder": str,
        "uid": int,
        "object_id": NotRequired[str | None],
        "labels": NotRequired[list[str]],
        # Envelope payload
        "from": NotRequired[str | None],
        "to": NotRequired[list[str] | None],
        "cc": NotRequired[list[str] | None],
        "subject": NotRequired[str | None],
        "message_id": NotRequired[str | None],
        "date": NotRequired[str | None],
        "body": NotRequired[str | None],
        "text_body": NotRequired[str | None],
        "html_body": NotRequired[str | None],
        "has_attachment": NotRequired[bool | None],
        "attachments": NotRequired[list[AttachmentMetaEntry] | list[Any] | None],
        "redacted_fields": NotRequired[list[str]],
        # fetch_headers payload
        "headers": NotRequired[dict[str, str]],
        # fetch_attachment selected-part payload
        "part_id": NotRequired[int],
        "mime_type": NotRequired[str],
        "size_bytes": NotRequired[int],
        "content_hash": NotRequired[str],
        "artifact_id": NotRequired[str],
        "saved_to": NotRequired[str],
        "saved_path": NotRequired[str],
        # Private audit fields
        "_audit_detail_reason": NotRequired[str],
        "_from_domain": NotRequired[str],
        "_artifact_id": NotRequired[str],
        "_artifact_sha256": NotRequired[str],
    },
    total=False,
)


def _deny_fetch(*, reason: str, account: str, folder: str, uid: int) -> FetchResponse:
    return {
        "decision": "DENY",
        "reason": reason,
        "account": account,
        "folder": folder,
        "uid": uid,
    }


def _not_found_fetch(
    *,
    account: str,
    folder: str,
    uid: int,
    reason: str,
    detail_reason: str,
    from_domain: str | None = None,
) -> FetchResponse:
    extra: dict[str, Any] = {
        "account": account,
        "folder": folder,
        "uid": uid,
        "_audit_detail_reason": detail_reason,
    }
    if from_domain:
        extra["_from_domain"] = from_domain
    return error_envelope(  # type: ignore[return-value]
        error_type="not_found",
        detail="Message not found",
        reason=reason,
        extra=extra,
    )


async def _fetch_and_evaluate(
    context: ServerContext, account_id: str, folder_path: str, uid: int
) -> tuple[FetchResponse | None, Any, Any, Any, Any, Any, Any, Any]:
    """Folder check, IMAP fetch envelope, and message-centric effective visibility evaluation."""
    folder_decision = context.pdp.decide_folder_access(context.caller_id, account_id, folder_path)
    if not folder_decision.allowed:
        return (
            _deny_fetch(
                reason=folder_decision.reason,
                account=account_id,
                folder=folder_path,
                uid=uid,
            ),
            None,
            None,
            None,
            None,
            None,
            None,
            None,
        )
    assert folder_decision.folder_policy is not None

    account, password = await _password_for(context, account_id)
    imap_folder = await _resolve_imap_folder(context, account_id, folder_path)
    stats = await imap_folder_stats(account, password, imap_folder)
    if stats.kind == "absent":
        return (
            error_envelope(  # type: ignore[return-value]
                error_type="folder_absent",
                detail="Folder does not exist on IMAP server",
                reason=folder_decision.reason,
                extra={"account": account_id, "folder": folder_path, "uid": uid},
            ),
            None,
            None,
            None,
            None,
            None,
            None,
            None,
        )
    if stats.kind == "select_failed":
        return (
            error_envelope(  # type: ignore[return-value]
                error_type="select_failed",
                detail=stats.imap_response or "SELECT mailbox failed",
                reason=folder_decision.reason,
                extra={"account": account_id, "folder": folder_path, "uid": uid},
            ),
            None,
            None,
            None,
            None,
            None,
            None,
            None,
        )
    envelope = await imap_fetch_envelope(account, password, imap_folder, uid)
    if envelope is None:
        return (
            _not_found_fetch(
                account=account_id,
                folder=folder_path,
                uid=uid,
                reason=folder_decision.reason,
                detail_reason="object_missing",
            ),
            None,
            None,
            None,
            None,
            None,
            None,
            None,
        )

    facts = _facts_from_envelope(envelope)

    # Provenance check for own drafts/messages
    fp = folder_decision.folder_policy
    if any("provenance" in r.match for r in fp.rules):
        from dataclasses import replace

        from ._common import resolve_provenance_own

        is_own = await resolve_provenance_own(
            context, account, password, imap_folder, uid, envelope
        )
        if is_own:
            facts = replace(facts, provenance_own=True)

    # Google label fetching for message-centric composition
    if _is_google_provider(account):
        try:
            from ..imap_core import gmail_fetch_labels

            msg_labels = await gmail_fetch_labels(account, password, imap_folder, uid)
            x_gm_msgid = await imap_gmail_fetch_msgid(account, password, imap_folder, uid)
            canonical_curr = context.pdp.canonical_folder(account_id, folder_path)
            all_labels = set(envelope.labels) | set(msg_labels)
            if canonical_curr not in ("[Gmail]/All Mail", "[Gmail]/Starred", "[Gmail]/Important"):
                all_labels.add(canonical_curr)
            facts = MessageFacts.from_envelope(
                from_raw=envelope.from_address or "",
                to_raws=envelope.to_addresses or [],
                cc_raws=getattr(envelope, "cc_addresses", None) or [],
                subject_raw=envelope.subject or "",
                has_attachment=bool(getattr(envelope, "has_attachment", False)),
                flagged="\\Flagged" in (getattr(envelope, "flags", None) or ()),
                size_bytes=int(getattr(envelope, "size_bytes", 0) or 0),
                internaldate_utc=facts.internaldate_utc,
                labels=tuple(all_labels),
                x_gm_msgid=x_gm_msgid,
            )
        except Exception:  # noqa: BLE001, S110
            pass

    eff_vis, eff_reason = context.pdp.evaluate_message_effective(
        context.caller_id, account_id, folder_path, facts
    )

    if level_rank(eff_vis) < level_rank("METADATA"):
        detail_cause = "message_capped" if eff_reason == "rule_matched" else "message_not_granted"
        return (
            _not_found_fetch(
                account=account_id,
                folder=folder_path,
                uid=uid,
                reason=eff_reason,
                detail_reason=detail_cause,
                from_domain=facts.from_domain if detail_cause == "message_capped" else None,
            ),
            None,
            None,
            None,
            None,
            None,
            None,
            None,
        )

    return None, account, password, imap_folder, eff_vis, eff_reason, facts, envelope


async def handle_fetch_envelope(context: ServerContext, arguments: dict[str, Any]) -> FetchResponse:
    account_id = str(arguments["account"])
    folder_path = str(arguments["folder"])
    uid = int(arguments["uid"])

    res = await _fetch_and_evaluate(context, account_id, folder_path, uid)
    if res[0] is not None:
        return res[0]
    _, account, _password, _imap_folder, eff_vis, eff_reason, facts, envelope = res

    if level_rank(eff_vis) < level_rank("ENVELOPE"):
        return _deny_fetch(
            reason="visibility_below_ENVELOPE",
            account=account_id,
            folder=folder_path,
            uid=uid,
        )

    payload: FetchResponse = {
        "decision": "ALLOW",
        "result": "OK",
        "reason": eff_reason,
        "visibility_applied": eff_vis,
        "account": account_id,
        "folder": folder_path,
        "uid": uid,
        "from": envelope.from_address,
        "to": envelope.to_addresses,
        "subject": envelope.subject,
        "message_id": envelope.message_id,
        "date": envelope.date,
        "body": None,
        "attachments": None,
        "redacted_fields": ["headers", "body", "attachments"],
    }

    if _is_google_provider(account):
        payload["labels"] = context.pdp.filter_labels_for_caller(
            context.caller_id, account_id, facts.labels
        )
        obj_id = context.pdp.make_google_object_id(
            context.caller_id, account_id, facts.x_gm_msgid, context.provenance_hmac_key
        )
        if obj_id is not None:
            payload["object_id"] = obj_id

    return payload


async def handle_fetch_headers(context: ServerContext, arguments: dict[str, Any]) -> FetchResponse:
    account_id = str(arguments["account"])
    folder_path = str(arguments["folder"])
    uid = int(arguments["uid"])

    res = await _fetch_and_evaluate(context, account_id, folder_path, uid)
    if res[0] is not None:
        return res[0]
    _, account, password, imap_folder, eff_vis, eff_reason, _facts, _envelope = res

    if level_rank(eff_vis) < level_rank("HEADERS"):
        return _deny_fetch(
            reason="visibility_below_HEADERS",
            account=account_id,
            folder=folder_path,
            uid=uid,
        )

    raw = await imap_fetch_full_message(account, password, imap_folder, uid)
    headers: dict[str, str] = {}
    if raw is not None:
        msg = email.message_from_bytes(raw)
        for name, value in msg.items():
            headers[name] = value

    return {
        "decision": "ALLOW",
        "result": "OK",
        "reason": eff_reason,
        "visibility_applied": eff_vis,
        "account": account_id,
        "folder": folder_path,
        "uid": uid,
        "headers": headers,
        "redacted_fields": ["body", "attachments"],
    }


async def handle_fetch_body(context: ServerContext, arguments: dict[str, Any]) -> FetchResponse:
    account_id = str(arguments["account"])
    folder_path = str(arguments["folder"])
    uid = int(arguments["uid"])

    res = await _fetch_and_evaluate(context, account_id, folder_path, uid)
    if res[0] is not None:
        return res[0]
    _, account, password, imap_folder, eff_vis, eff_reason, _facts, envelope = res

    if level_rank(eff_vis) < level_rank("BODY"):
        return _deny_fetch(
            reason="visibility_below_BODY",
            account=account_id,
            folder=folder_path,
            uid=uid,
        )

    result = await imap_fetch_body(account, password, imap_folder, uid)
    if result is None:
        return _not_found_fetch(
            account=account_id,
            folder=folder_path,
            uid=uid,
            reason=eff_reason,
            detail_reason="object_missing",
        )
    _, body_text, msg_obj = result
    msg: Any = msg_obj

    # Extract HTML body alternative if present
    html_body = None
    for part in msg.walk():
        if part.get_content_type() == "text/html":
            payload_bytes = part.get_payload(decode=True)
            if isinstance(payload_bytes, bytes):
                html_body = payload_bytes.decode(
                    part.get_content_charset("utf-8"), errors="replace"
                ).rstrip("\r\n")
                break

    all_parts = _walk_attachment_parts(msg)
    attachments_meta: list[AttachmentMetaEntry] = []
    for i, p in enumerate(all_parts):
        p_payload = p.get_payload(decode=True) or b""
        attachments_meta.append(
            {
                "index": i,
                "filename": decode_mime_words(p.get_filename() or "") or None,
                "mime_type": p.get_content_type(),
                "size_bytes": len(p_payload),
            }
        )

    is_full = level_rank(eff_vis) >= level_rank("FULL")
    return {
        "decision": "ALLOW",
        "result": "OK",
        "reason": eff_reason,
        "visibility_applied": eff_vis,
        "account": account_id,
        "folder": folder_path,
        "uid": uid,
        "from": envelope.from_address,
        "subject": envelope.subject,
        "text_body": body_text,
        "html_body": html_body,
        "has_attachment": bool(all_parts),
        "attachments": attachments_meta,
        "redacted_fields": [] if is_full else ["attachment_bytes"],
    }


async def handle_list_attachments(
    context: ServerContext, arguments: dict[str, Any]
) -> FetchResponse:
    account_id = str(arguments["account"])
    folder_path = str(arguments["folder"])
    uid = int(arguments["uid"])

    res = await _fetch_and_evaluate(context, account_id, folder_path, uid)
    if res[0] is not None:
        return res[0]
    _, account, password, imap_folder, eff_vis, eff_reason, _facts, _envelope = res

    if level_rank(eff_vis) < level_rank("BODY"):
        return _deny_fetch(
            reason="visibility_below_BODY",
            account=account_id,
            folder=folder_path,
            uid=uid,
        )

    raw = await imap_fetch_full_message(account, password, imap_folder, uid)
    if raw is None:
        return _not_found_fetch(
            account=account_id,
            folder=folder_path,
            uid=uid,
            reason=eff_reason,
            detail_reason="object_missing",
        )
    msg = email.message_from_bytes(raw)
    all_parts = _walk_attachment_parts(msg)
    attachments_meta: list[AttachmentMetaEntry] = []
    for i, p in enumerate(all_parts):
        p_payload = p.get_payload(decode=True) or b""
        attachments_meta.append(
            {
                "index": i,
                "filename": decode_mime_words(p.get_filename() or "") or None,
                "mime_type": p.get_content_type(),
                "size_bytes": len(p_payload),
            }
        )

    return {
        "decision": "ALLOW",
        "result": "OK",
        "reason": eff_reason,
        "account": account_id,
        "folder": folder_path,
        "uid": uid,
        "attachments": attachments_meta,
    }


async def handle_fetch_attachment(
    context: ServerContext, arguments: dict[str, Any]
) -> FetchResponse:
    account_id = str(arguments["account"])
    folder_path = str(arguments["folder"])
    uid = int(arguments["uid"])
    part_id = int(arguments["part_id"])

    res = await _fetch_and_evaluate(context, account_id, folder_path, uid)
    if res[0] is not None:
        return res[0]
    _, account, password, imap_folder, eff_vis, eff_reason, _facts, _envelope = res

    if level_rank(eff_vis) < level_rank("FULL"):
        return _deny_fetch(
            reason="visibility_below_FULL",
            account=account_id,
            folder=folder_path,
            uid=uid,
        )

    # Sink configuration check
    sink_dir = context.attachment_sink_directory
    if sink_dir is None:
        return error_envelope(  # type: ignore[return-value]
            error_type="sink_not_configured",
            detail="attachment sink is not configured",
            reason=eff_reason,
            extra={"account": account_id, "folder": folder_path, "uid": uid, "part_id": part_id},
        )

    raw = await imap_fetch_full_message(account, password, imap_folder, uid)
    if raw is None:
        return _not_found_fetch(
            account=account_id,
            folder=folder_path,
            uid=uid,
            reason=eff_reason,
            detail_reason="object_missing",
        )

    msg = email.message_from_bytes(raw)
    all_parts = _walk_attachment_parts(msg)
    if not (0 <= part_id < len(all_parts)):
        return error_envelope(  # type: ignore[return-value]
            error_type="attachment_not_found",
            detail="Attachment part not found",
            reason=eff_reason,
            extra={"account": account_id, "folder": folder_path, "uid": uid, "part_id": part_id},
        )

    selected_part = all_parts[part_id]
    payload_bytes = selected_part.get_payload(decode=True) or b""
    original_filename = selected_part.get_filename() or "attachment"
    mime_type = selected_part.get_content_type()

    try:
        saved_to, saved_path, artifact_id, content_hash, size_bytes = deliver_attachment_to_sink(
            base_sink_dir=sink_dir,
            caller_id=context.caller_id,
            original_filename=original_filename,
            payload=payload_bytes,
        )
    except FileNotFoundError:
        return error_envelope(  # type: ignore[return-value]
            error_type="sink_not_configured",
            detail="attachment sink directory not configured or missing",
            reason=eff_reason,
            extra={"account": account_id, "folder": folder_path, "uid": uid, "part_id": part_id},
        )
    except PermissionError as exc:
        return error_envelope(  # type: ignore[return-value]
            error_type="sink_not_writable",
            detail=str(exc),
            reason=eff_reason,
            extra={"account": account_id, "folder": folder_path, "uid": uid, "part_id": part_id},
        )
    except OSError as exc:
        return error_envelope(  # type: ignore[return-value]
            error_type="sink_not_writable",
            detail=str(exc),
            reason=eff_reason,
            extra={"account": account_id, "folder": folder_path, "uid": uid, "part_id": part_id},
        )

    return {
        "decision": "ALLOW",
        "result": "OK",
        "reason": eff_reason,
        "visibility_applied": eff_vis,
        "account": account_id,
        "folder": folder_path,
        "uid": uid,
        "part_id": part_id,
        "mime_type": mime_type,
        "size_bytes": size_bytes,
        "content_hash": content_hash,
        "artifact_id": artifact_id,
        "saved_to": saved_to,
        "saved_path": saved_path,
        "_artifact_id": artifact_id,
        "_artifact_sha256": content_hash,
    }
