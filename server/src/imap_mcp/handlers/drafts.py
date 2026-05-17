"""Draft creation handlers: create_draft, create_reply_draft."""

from __future__ import annotations

import asyncio
from typing import Any, Literal, NotRequired, TypedDict, TYPE_CHECKING

from .. import reply as reply_builder
from ..imap_core import (
    append_message as imap_append_message,
    fetch_message_for_reply as imap_fetch_message_for_reply,
)
from ..policy import MessageFacts, evaluate_message_against_folder, level_rank
from ._common import _password_for, _resolve_imap_folder

if TYPE_CHECKING:
    from ..context import ServerContext


class CreateDraftResponse(TypedDict, total=False):
    decision: Literal["ALLOW", "DENY"]
    result: Literal["OK", "ERROR"]
    error_type: NotRequired[str | None]
    account: str
    folder: str
    reason: NotRequired[str]
    missing_capability: NotRequired[str]
    imap_response: NotRequired[str | None]


class CreateReplyDraftResponse(TypedDict, total=False):
    decision: Literal["ALLOW", "DENY"]
    result: NotRequired[Literal["OK", "ERROR"]]
    error_type: NotRequired[str | None]
    reason: NotRequired[str]
    account: str
    folder: NotRequired[str]
    source_folder: NotRequired[str]
    drafts_folder: NotRequired[str]
    uid: NotRequired[int]
    missing_capability: NotRequired[str]


def _ok_draft(*, account: str, folder: str, imap_response: str | None = None) -> CreateDraftResponse:
    return {
        "decision": "ALLOW",
        "result": "OK",
        "error_type": None,
        "account": account,
        "folder": folder,
        "imap_response": imap_response,
    }


def _deny_draft(
    *,
    reason: str,
    account: str,
    folder: str,
    missing_capability: str | None = None,
) -> CreateDraftResponse:
    response: CreateDraftResponse = {
        "decision": "DENY",
        "reason": reason,
        "account": account,
        "folder": folder,
    }
    if missing_capability is not None:
        response["missing_capability"] = missing_capability
    return response


def _error_draft(
    *,
    error_type: str,
    account: str,
    folder: str,
    imap_response: str | None = None,
) -> CreateDraftResponse:
    return {
        "decision": "ALLOW",
        "result": "ERROR",
        "error_type": error_type,
        "account": account,
        "folder": folder,
        "imap_response": imap_response,
    }


def _deny_reply(
    *,
    reason: str,
    account: str,
    folder: str | None = None,
    source_folder: str | None = None,
    uid: int | None = None,
    error_type: str | None = None,
    missing_capability: str | None = None,
) -> CreateReplyDraftResponse:
    response: CreateReplyDraftResponse = {"decision": "DENY", "reason": reason, "account": account}
    if folder is not None:
        response["folder"] = folder
    if source_folder is not None:
        response["source_folder"] = source_folder
    if uid is not None:
        response["uid"] = uid
    if error_type is not None:
        response["error_type"] = error_type
    if missing_capability is not None:
        response["missing_capability"] = missing_capability
    return response


def _ok_reply(
    *, account: str, source_folder: str, drafts_folder: str, uid: int
) -> CreateReplyDraftResponse:
    return {
        "decision": "ALLOW",
        "result": "OK",
        "error_type": None,
        "account": account,
        "source_folder": source_folder,
        "drafts_folder": drafts_folder,
        "uid": uid,
    }


def _error_reply(
    *,
    error_type: str,
    account: str,
    source_folder: str | None = None,
    drafts_folder: str | None = None,
    uid: int | None = None,
    folder: str | None = None,
) -> CreateReplyDraftResponse:
    response: CreateReplyDraftResponse = {
        "decision": "ALLOW",
        "result": "ERROR",
        "error_type": error_type,
        "account": account,
    }
    if folder is not None:
        response["folder"] = folder
    if source_folder is not None:
        response["source_folder"] = source_folder
    if drafts_folder is not None:
        response["drafts_folder"] = drafts_folder
    if uid is not None:
        response["uid"] = uid
    return response


async def handle_create_draft(
    context: "ServerContext", arguments: dict[str, Any]
) -> CreateDraftResponse:
    account_id = str(arguments["account"])
    folder_path = str(arguments["folder"])
    rfc822_text = str(arguments["rfc822"])

    folder_decision = context.pdp.decide_folder_access(context.caller_id, account_id, folder_path)
    if not folder_decision.allowed:
        return _deny_draft(reason=folder_decision.reason, account=account_id, folder=folder_path)
    assert folder_decision.folder_policy is not None
    if not folder_decision.folder_policy.draft_append:
        return _deny_draft(
            reason="capability_missing",
            account=account_id,
            folder=folder_path,
            missing_capability="draft_append",
        )
    account, password = await _password_for(context, account_id)
    imap_folder = await _resolve_imap_folder(context, account_id, folder_path)
    try:
        append_result = await imap_append_message(
            account, password, imap_folder, rfc822_text.encode("utf-8")
        )
    except asyncio.TimeoutError:
        return _error_draft(error_type="append_timeout", account=account_id, folder=folder_path)
    except Exception:
        return _error_draft(error_type="append_failed", account=account_id, folder=folder_path)
    if append_result.outcome == "ok":
        return _ok_draft(account=account_id, folder=folder_path)
    return _error_draft(
        error_type="append_rejected",
        account=account_id,
        folder=folder_path,
        imap_response=append_result.imap_response,
    )


async def handle_create_reply_draft(
    context: "ServerContext", arguments: dict[str, Any]
) -> CreateReplyDraftResponse:
    account_id = str(arguments["account"])
    source_folder = str(arguments["source_folder"])
    uid = int(arguments["uid"])
    drafts_folder = str(arguments["drafts_folder"])
    reply_text = str(arguments["reply_text"])

    if not reply_text.strip():
        return _deny_reply(
            reason="validation_failed",
            account=account_id,
            error_type="empty_reply_text",
        )

    src_decision = context.pdp.decide_folder_access(
        context.caller_id, account_id, source_folder
    )
    if not src_decision.allowed:
        return _deny_reply(
            reason=src_decision.reason,
            account=account_id,
            folder=source_folder,
        )

    drafts_decision = context.pdp.decide_folder_access(
        context.caller_id, account_id, drafts_folder
    )
    if not drafts_decision.allowed:
        return _deny_reply(
            reason=drafts_decision.reason,
            account=account_id,
            folder=drafts_folder,
        )
    assert drafts_decision.folder_policy is not None
    if not drafts_decision.folder_policy.draft_append:
        return _deny_reply(
            reason="capability_missing",
            account=account_id,
            folder=drafts_folder,
            missing_capability="draft_append",
        )

    account, password = await _password_for(context, account_id)

    imap_src = await _resolve_imap_folder(context, account_id, source_folder)
    result = await imap_fetch_message_for_reply(account, password, imap_src, uid)
    if result is None:
        return _error_reply(
            error_type="uid_not_found",
            account=account_id,
            folder=source_folder,
            uid=uid,
        )
    source_msg, source_body = result

    from email.utils import getaddresses as _ga

    _from_addrs = _ga(source_msg.get_all("From", []))
    _to_addrs = _ga(
        source_msg.get_all("To", []) + source_msg.get_all("Cc", [])
    )
    assert src_decision.folder_policy is not None
    src_facts = MessageFacts(
        from_address=_from_addrs[0][1] if _from_addrs else "",
        to_addresses=tuple(a for _, a in _to_addrs if a),
        subject=source_msg.get("Subject") or "",
        has_attachment=False,
        flagged=False,
        size_bytes=0,
        date_iso=None,
    )
    src_msg_decision = evaluate_message_against_folder(
        src_decision.folder_policy, facts=src_facts
    )
    if not src_msg_decision.allowed:
        return _deny_reply(
            reason=src_msg_decision.reason,
            account=account_id,
            folder=source_folder,
            uid=uid,
        )
    if level_rank(src_msg_decision.visibility) < level_rank("BODY"):
        return _deny_reply(
            reason="visibility_below_BODY",
            account=account_id,
            folder=source_folder,
            uid=uid,
        )

    src_mid = source_msg.get("Message-ID")
    if not src_mid:
        return _deny_reply(
            reason="missing_message_id",
            account=account_id,
            folder=source_folder,
            uid=uid,
        )

    if account.identity is None:
        return _deny_reply(reason="account_identity_missing", account=account_id)

    from email.header import decode_header as _dh, make_header as _mh

    src_from = str(_mh(_dh(source_msg.get("From") or "")))
    src_reply_to = source_msg.get("Reply-To")
    src_to = source_msg.get("To")
    src_cc = source_msg.get("Cc")
    src_subject = str(_mh(_dh(source_msg.get("Subject") or "")))
    src_date = source_msg.get("Date")
    src_references = source_msg.get("References")

    subject = reply_builder.build_reply_subject(src_subject)
    to = reply_builder.derive_reply_to(src_reply_to, src_from)
    cc = reply_builder.derive_reply_cc(src_to, src_cc, account.identity)
    attribution = reply_builder.build_attribution(src_date, src_from)
    quoted = reply_builder.quote_body(source_body)
    body = reply_builder.build_reply_body(reply_text, attribution, quoted)
    in_reply_to, references = reply_builder.build_threading_headers(
        src_mid, src_references
    )

    rfc822_bytes = reply_builder.build_reply_message(
        self_identity=account.identity,
        reply_to=to,
        cc=cc,
        subject=subject,
        in_reply_to=in_reply_to,
        references=references,
        body=body,
    )

    imap_dst = await _resolve_imap_folder(context, account_id, drafts_folder)
    append_result = await imap_append_message(account, password, imap_dst, rfc822_bytes)
    if append_result.outcome == "ok":
        return _ok_reply(
            account=account_id,
            source_folder=source_folder,
            drafts_folder=drafts_folder,
            uid=uid,
        )
    return _error_reply(
        error_type="append_failed",
        account=account_id,
        source_folder=source_folder,
        drafts_folder=drafts_folder,
        uid=uid,
    )
