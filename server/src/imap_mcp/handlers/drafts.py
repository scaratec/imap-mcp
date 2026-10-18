"""Draft handlers: create_draft, create_reply_draft, get_draft,
list_drafts, update_draft.

Draft identity follows ADR 0030: the server injects `X-SC-Draft-Id`
and `X-SC-Draft-Rev` into every draft it appends and flags the append
`\\Draft \\Seen`. The mailbox is the only store of that mapping, so
resolving a handle is a header SEARCH inside the one folder the caller
named.

A revision (ADR 0031) is a field-wise change executed as one
message-rewrite saga under an optimistic check on `base_revision`. The
request itself is hashed into `X-SC-Draft-Change-Hash` so that a retry
of a call whose response was lost can be told apart from a genuine
second edit.
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any, Literal, NamedTuple, NotRequired, TypedDict, TYPE_CHECKING

from .. import reply as reply_builder
from ..imap_core import (
    append_message as imap_append_message,
    expunge_uids as imap_expunge_uids,
    fetch_body as imap_fetch_body,
    fetch_envelopes_batch as imap_fetch_envelopes_batch,
    fetch_message_for_reply as imap_fetch_message_for_reply,
    fetch_raw_headers_batch as imap_fetch_raw_headers_batch,
    folder_stats as imap_folder_stats,
    search_uids as imap_search_uids,
)
from ..policy import MessageFacts, evaluate_message_against_folder, level_rank
from ._common import (
    _facts_from_envelope,
    _password_for,
    _resolve_imap_folder,
    _walk_attachment_parts,
    decode_mime_words,
    error_envelope,
)

if TYPE_CHECKING:
    from ..context import ServerContext


DRAFT_ID_HEADER = "X-SC-Draft-Id"
DRAFT_REV_HEADER = "X-SC-Draft-Rev"
DRAFT_CHANGE_HASH_HEADER = "X-SC-Draft-Change-Hash"
_DRAFT_HEADER_PREFIX = b"x-sc-draft-"
FIRST_REVISION = 1
# ADR 0030: a draft the server writes is a draft the owner has already
# seen — it was composed through this server, not delivered to it.
DRAFT_APPEND_FLAGS = ("\\Draft", "\\Seen")


def _split_header_block(rfc822: bytes) -> tuple[bytes, bytes]:
    """Split raw message bytes into (header block, remainder).

    The header block keeps its own trailing line terminator; the
    remainder starts with the empty line that separates headers from
    body. A message without a separator is all headers.
    """
    candidates: list[tuple[int, int]] = []
    for separator in (b"\r\n\r\n", b"\n\n"):
        index = rfc822.find(separator)
        if index != -1:
            candidates.append((index, len(separator) // 2))
    if not candidates:
        return rfc822, b""
    index, keep = min(candidates)
    return rfc822[: index + keep], rfc822[index + keep :]


def _strip_draft_headers(header_block: bytes) -> bytes:
    """Drop every `X-SC-Draft-*` field, folded continuations included.

    Draft identity is assigned by the server (ADR 0030), so a value the
    caller put there is not carried over. Every other header the caller
    supplied is left byte-for-byte as it was.
    """
    kept: list[bytes] = []
    dropping = False
    for line in header_block.splitlines(keepends=True):
        if line[:1] in (b" ", b"\t"):
            if not dropping:
                kept.append(line)
            continue
        dropping = line.lower().startswith(_DRAFT_HEADER_PREFIX)
        if not dropping:
            kept.append(line)
    return b"".join(kept)


def _inject_draft_headers(rfc822: bytes, *, draft_id: str, revision: int) -> bytes:
    """Prepend the draft handle headers to a message about to be appended."""
    header_block, remainder = _split_header_block(rfc822)
    header_block = _strip_draft_headers(header_block)
    newline = b"\r\n" if b"\r\n" in rfc822 else b"\n"
    prefix = (
        f"{DRAFT_ID_HEADER}: {draft_id}".encode("ascii")
        + newline
        + f"{DRAFT_REV_HEADER}: {revision}".encode("ascii")
        + newline
    )
    return prefix + header_block + remainder


def _new_draft_id() -> str:
    return str(uuid.uuid4())


class _DraftHeaders(NamedTuple):
    """The draft handle as it was found on one message."""

    uid: int
    draft_id: str | None
    revision: int | None
    change_hash: str | None = None


def _parse_draft_headers(uid: int, raw_header: bytes) -> _DraftHeaders:
    """Read the handle headers off a raw header block.

    `revision` is None when the field is missing, empty, or not a
    positive integer — the condition ADR 0030 calls "not editable".
    `change_hash` carries the request hash of the revision that
    produced this message (ADR 0031); it is absent on revision 1 and on
    anything a foreign client wrote.
    """
    import email

    message = email.message_from_bytes(raw_header)
    raw_id = message.get(DRAFT_ID_HEADER)
    draft_id = raw_id.strip() if raw_id else None
    if not draft_id:
        return _DraftHeaders(uid=uid, draft_id=None, revision=None)
    raw_rev = message.get(DRAFT_REV_HEADER)
    revision: int | None = None
    if raw_rev:
        try:
            parsed = int(raw_rev.strip())
        except ValueError:
            parsed = 0
        if parsed >= 1:
            revision = parsed
    raw_hash = message.get(DRAFT_CHANGE_HASH_HEADER)
    change_hash = raw_hash.strip() if raw_hash else None
    return _DraftHeaders(uid=uid, draft_id=draft_id, revision=revision, change_hash=change_hash)


class _Resolution(NamedTuple):
    """Outcome of turning a draft_id into a UID.

    Exactly one of `record` and `error_type` is set. `candidates` names
    the UIDs that could not be told apart on an ambiguous resolution;
    it is what the escalated transaction hands to the operator.
    """

    record: _DraftHeaders | None
    error_type: str | None
    candidates: tuple[int, ...] = ()


async def _resolve_draft_id(
    account: Any, password: str, imap_folder: str, draft_id: str
) -> _Resolution:
    """Resolve a draft handle to the live message inside one folder.

    Follows ADR 0030: the highest `X-SC-Draft-Rev` is authoritative,
    lower revisions are residue and are left untouched, and two
    messages carrying the same id *and* the same revision cannot be
    ordered and are reported as `draft_ambiguous`.
    """
    criteria = f'HEADER "{DRAFT_ID_HEADER}" "{draft_id}"'
    uids = await imap_search_uids(account, password, imap_folder, criteria)
    if not uids:
        return _Resolution(record=None, error_type="draft_not_found")
    raw_headers = await imap_fetch_raw_headers_batch(account, password, imap_folder, uids)
    # SEARCH HEADER matches a substring of the field value (RFC 3501
    # §6.4.4), so every hit is re-checked for exact equality here.
    matches: list[_DraftHeaders] = []
    for uid, raw in sorted(raw_headers.items()):
        record = _parse_draft_headers(uid, raw)
        if record.draft_id == draft_id:
            matches.append(record)
    if not matches:
        return _Resolution(record=None, error_type="draft_not_found")
    ordered = [record for record in matches if record.revision is not None]
    if not ordered:
        return _Resolution(record=None, error_type="draft_not_editable")
    top_revision = max(record.revision or 0 for record in ordered)
    live = [record for record in ordered if record.revision == top_revision]
    if len(live) > 1:
        return _Resolution(
            record=None,
            error_type="draft_ambiguous",
            candidates=tuple(record.uid for record in live),
        )
    return _Resolution(record=live[0], error_type=None)


async def _resolve_revision_uid(
    account: Any, password: str, imap_folder: str, draft_id: str, revision: int
) -> int | None:
    """Find the UID of one specific revision of one draft.

    ADR 0031 §4: the message an `update_draft` just appended is located
    by its revision number rather than by the `Message-ID` heuristic the
    attachment path uses. SEARCH HEADER matches substrings, so both hits
    are re-checked for exact equality on id *and* revision.
    """
    criteria = f'HEADER "{DRAFT_ID_HEADER}" "{draft_id}" HEADER "{DRAFT_REV_HEADER}" "{revision}"'
    uids = await imap_search_uids(account, password, imap_folder, criteria)
    if not uids:
        return None
    raw_headers = await imap_fetch_raw_headers_batch(account, password, imap_folder, uids)
    for uid, raw in sorted(raw_headers.items()):
        record = _parse_draft_headers(uid, raw)
        if record.draft_id == draft_id and record.revision == revision:
            return uid
    return None


class CreateDraftResponse(TypedDict, total=False):
    decision: Literal["ALLOW", "DENY"]
    result: NotRequired[Literal["OK", "ERROR"]]
    account: str
    folder: str
    reason: NotRequired[str]
    error: NotRequired[dict[str, str]]
    missing_capability: NotRequired[str]
    draft_id: NotRequired[str]
    revision: NotRequired[int]


class CreateReplyDraftResponse(TypedDict, total=False):
    decision: Literal["ALLOW", "DENY"]
    result: NotRequired[Literal["OK", "ERROR"]]
    reason: NotRequired[str]
    error: NotRequired[dict[str, str]]
    account: str
    folder: NotRequired[str]
    source_folder: NotRequired[str]
    drafts_folder: NotRequired[str]
    uid: NotRequired[int]
    missing_capability: NotRequired[str]
    draft_id: NotRequired[str]
    revision: NotRequired[int]


def _ok_draft(*, account: str, folder: str, draft_id: str, revision: int) -> CreateDraftResponse:
    return {
        "decision": "ALLOW",
        "result": "OK",
        "account": account,
        "folder": folder,
        "draft_id": draft_id,
        "revision": revision,
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
    detail: str = "",
) -> CreateDraftResponse:
    return error_envelope(  # type: ignore[return-value]
        error_type=error_type,
        detail=detail,
        extra={"account": account, "folder": folder},
    )


def _deny_reply(
    *,
    reason: str,
    account: str,
    folder: str | None = None,
    source_folder: str | None = None,
    uid: int | None = None,
    missing_capability: str | None = None,
) -> CreateReplyDraftResponse:
    response: CreateReplyDraftResponse = {"decision": "DENY", "reason": reason, "account": account}
    if folder is not None:
        response["folder"] = folder
    if source_folder is not None:
        response["source_folder"] = source_folder
    if uid is not None:
        response["uid"] = uid
    if missing_capability is not None:
        response["missing_capability"] = missing_capability
    return response


def _ok_reply(
    *,
    account: str,
    source_folder: str,
    drafts_folder: str,
    uid: int,
    draft_id: str,
    revision: int,
) -> CreateReplyDraftResponse:
    return {
        "decision": "ALLOW",
        "result": "OK",
        "account": account,
        "source_folder": source_folder,
        "drafts_folder": drafts_folder,
        "uid": uid,
        "draft_id": draft_id,
        "revision": revision,
    }


def _error_reply(
    *,
    error_type: str,
    account: str,
    source_folder: str | None = None,
    drafts_folder: str | None = None,
    uid: int | None = None,
    folder: str | None = None,
    detail: str = "",
) -> CreateReplyDraftResponse:
    extra: dict[str, Any] = {"account": account}
    if folder is not None:
        extra["folder"] = folder
    if source_folder is not None:
        extra["source_folder"] = source_folder
    if drafts_folder is not None:
        extra["drafts_folder"] = drafts_folder
    if uid is not None:
        extra["uid"] = uid
    return error_envelope(  # type: ignore[return-value]
        error_type=error_type, detail=detail, extra=extra
    )


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
    draft_id = _new_draft_id()
    rfc822_bytes = _inject_draft_headers(
        rfc822_text.encode("utf-8"), draft_id=draft_id, revision=FIRST_REVISION
    )
    try:
        append_result = await imap_append_message(
            account, password, imap_folder, rfc822_bytes, flags=DRAFT_APPEND_FLAGS
        )
    except asyncio.TimeoutError:
        return _error_draft(error_type="append_timeout", account=account_id, folder=folder_path)
    except Exception:
        return _error_draft(error_type="append_failed", account=account_id, folder=folder_path)
    if append_result.outcome == "ok":
        return _ok_draft(
            account=account_id,
            folder=folder_path,
            draft_id=draft_id,
            revision=FIRST_REVISION,
        )
    return _error_draft(
        error_type="append_rejected",
        account=account_id,
        folder=folder_path,
        detail=append_result.imap_response or "",
    )


def _validate_reply_preconditions(
    context: "ServerContext",
    *,
    account_id: str,
    source_folder: str,
    drafts_folder: str,
    reply_text: str,
) -> "tuple[CreateReplyDraftResponse | None, Any, Any]":
    """Run pre-fetch validation chain. Returns
    ``(deny_response, src_decision, drafts_decision)``: when
    ``deny_response`` is non-None the caller short-circuits; otherwise
    the two PDP decisions are returned for later message-level checks.
    """
    if not reply_text.strip():
        return (
            _error_reply(
                error_type="empty_reply_text",
                account=account_id,
                source_folder=source_folder,
                drafts_folder=drafts_folder,
            ),
            None,
            None,
        )

    src_decision = context.pdp.decide_folder_access(context.caller_id, account_id, source_folder)
    if not src_decision.allowed:
        return (
            _deny_reply(reason=src_decision.reason, account=account_id, folder=source_folder),
            None,
            None,
        )

    drafts_decision = context.pdp.decide_folder_access(context.caller_id, account_id, drafts_folder)
    if not drafts_decision.allowed:
        return (
            _deny_reply(reason=drafts_decision.reason, account=account_id, folder=drafts_folder),
            None,
            None,
        )
    assert drafts_decision.folder_policy is not None
    if not drafts_decision.folder_policy.draft_append:
        return (
            _deny_reply(
                reason="capability_missing",
                account=account_id,
                folder=drafts_folder,
                missing_capability="draft_append",
            ),
            None,
            None,
        )
    return None, src_decision, drafts_decision


def _assemble_reply_rfc822(
    *,
    source_msg: Any,
    source_body: str,
    reply_text: str,
    identity: Any,
) -> bytes:
    """Take the validated source message + agent-supplied reply_text
    and produce the RFC822 bytes for the draft APPEND. Handles
    subject/recipient derivation, attribution, body quoting, and
    threading headers via ``reply_builder``.
    """
    from email.header import decode_header as _dh, make_header as _mh

    src_from = str(_mh(_dh(source_msg.get("From") or "")))
    src_reply_to = source_msg.get("Reply-To")
    src_to = source_msg.get("To")
    src_cc = source_msg.get("Cc")
    src_subject = str(_mh(_dh(source_msg.get("Subject") or "")))
    src_date = source_msg.get("Date")
    src_references = source_msg.get("References")
    src_mid = source_msg.get("Message-ID")

    subject = reply_builder.build_reply_subject(src_subject)
    to = reply_builder.derive_reply_to(src_reply_to, src_from)
    cc = reply_builder.derive_reply_cc(src_to, src_cc, identity)
    attribution = reply_builder.build_attribution(src_date, src_from)
    quoted = reply_builder.quote_body(source_body)
    body = reply_builder.build_reply_body(reply_text, attribution, quoted)
    in_reply_to, references = reply_builder.build_threading_headers(src_mid, src_references)

    return reply_builder.build_reply_message(
        self_identity=identity,
        reply_to=to,
        cc=cc,
        subject=subject,
        in_reply_to=in_reply_to,
        references=references,
        body=body,
    )


async def handle_create_reply_draft(
    context: "ServerContext", arguments: dict[str, Any]
) -> CreateReplyDraftResponse:
    account_id = str(arguments["account"])
    source_folder = str(arguments["source_folder"])
    uid = int(arguments["uid"])
    drafts_folder = str(arguments["drafts_folder"])
    reply_text = str(arguments["reply_text"])

    deny, src_decision, _drafts_decision = _validate_reply_preconditions(
        context,
        account_id=account_id,
        source_folder=source_folder,
        drafts_folder=drafts_folder,
        reply_text=reply_text,
    )
    if deny is not None:
        return deny

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
    _to_addrs = _ga(source_msg.get_all("To", []) + source_msg.get_all("Cc", []))
    assert src_decision is not None and src_decision.folder_policy is not None
    src_facts = MessageFacts(
        from_address=_from_addrs[0][1] if _from_addrs else "",
        to_addresses=tuple(a for _, a in _to_addrs if a),
        subject=source_msg.get("Subject") or "",
        has_attachment=False,
        flagged=False,
        size_bytes=0,
        date_iso=None,
    )
    src_msg_decision = evaluate_message_against_folder(src_decision.folder_policy, facts=src_facts)
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

    if not source_msg.get("Message-ID"):
        return _deny_reply(
            reason="missing_message_id",
            account=account_id,
            folder=source_folder,
            uid=uid,
        )

    if account.identity is None:
        return _deny_reply(reason="account_identity_missing", account=account_id)

    rfc822_bytes = _assemble_reply_rfc822(
        source_msg=source_msg,
        source_body=source_body,
        reply_text=reply_text,
        identity=account.identity,
    )

    draft_id = _new_draft_id()
    rfc822_bytes = _inject_draft_headers(rfc822_bytes, draft_id=draft_id, revision=FIRST_REVISION)

    imap_dst = await _resolve_imap_folder(context, account_id, drafts_folder)
    append_result = await imap_append_message(
        account, password, imap_dst, rfc822_bytes, flags=DRAFT_APPEND_FLAGS
    )
    if append_result.outcome == "ok":
        return _ok_reply(
            account=account_id,
            source_folder=source_folder,
            drafts_folder=drafts_folder,
            uid=uid,
            draft_id=draft_id,
            revision=FIRST_REVISION,
        )
    return _error_reply(
        error_type="append_failed",
        account=account_id,
        source_folder=source_folder,
        drafts_folder=drafts_folder,
        uid=uid,
    )


# ------------------------------------------------------------------ reads


class AttachmentMetaEntry(TypedDict):
    index: int
    filename: str | None
    mime_type: str
    size_bytes: int


class GetDraftResponse(TypedDict, total=False):
    decision: Literal["ALLOW", "DENY"]
    result: NotRequired[Literal["OK", "ERROR"]]
    reason: NotRequired[str]
    error: NotRequired[dict[str, str]]
    visibility_applied: NotRequired[str]
    account: str
    folder: str
    draft_id: str
    revision: NotRequired[int]
    subject: NotRequired[str]
    to: NotRequired[list[str]]
    cc: NotRequired[list[str]]
    bcc: NotRequired[list[str]]
    body_text: NotRequired[str]
    body_html: NotRequired[str | None]
    attachments: NotRequired[list[AttachmentMetaEntry]]


class DraftListEntry(TypedDict, total=False):
    draft_id: NotRequired[str]
    revision: NotRequired[int]
    subject: str
    editable: bool


class ListDraftsResponse(TypedDict, total=False):
    decision: Literal["ALLOW", "DENY"]
    result: NotRequired[Literal["OK", "ERROR"]]
    reason: NotRequired[str]
    error: NotRequired[dict[str, str]]
    account: str
    folder: str
    drafts: NotRequired[list[DraftListEntry]]


def _folder_ceiling_below(folder_policy: Any, minimum_rank: int) -> bool:
    """True when no grant in this folder can ever reach `minimum_rank`.

    Mirrors the gate `search` applies (handlers/search.py): only a
    whitelist folder answers with a denial, because there the default is
    the floor and the rules are the only way up. A blacklist folder
    always starts above NONE and its rules subtract rather than add, so
    its messages are filtered individually further down.
    """
    if getattr(folder_policy, "mode", "") != "whitelist":
        return False
    if level_rank(folder_policy.default) >= minimum_rank:
        return False
    return not any(
        level_rank(rule.grant) >= minimum_rank
        for rule in folder_policy.rules
        if rule.grant is not None
    )


def _deny_draft_read(
    *, reason: str, account: str, folder: str, draft_id: str | None = None
) -> dict[str, Any]:
    response: dict[str, Any] = {
        "decision": "DENY",
        "reason": reason,
        "account": account,
        "folder": folder,
    }
    if draft_id is not None:
        response["draft_id"] = draft_id
    return response


def _addresses(message: Any, field: str) -> list[str]:
    """Return the bare addresses of one recipient header."""
    from email.utils import getaddresses

    return [address for _, address in getaddresses(message.get_all(field, [])) if address]


def _html_body(message: Any) -> str | None:
    """Return the text/html alternative of a message, or None."""
    for part in message.walk():
        if part.get_content_type() != "text/html":
            continue
        payload = part.get_payload(decode=True)
        if not isinstance(payload, bytes):
            continue
        return payload.decode(part.get_content_charset("utf-8"), errors="replace").rstrip("\r\n")
    return None


def _attachment_meta(message: Any) -> list[AttachmentMetaEntry]:
    entries: list[AttachmentMetaEntry] = []
    for index, part in enumerate(_walk_attachment_parts(message)):
        payload = part.get_payload(decode=True) or b""
        entries.append(
            {
                "index": index,
                "filename": decode_mime_words(part.get_filename() or "") or None,
                "mime_type": part.get_content_type(),
                "size_bytes": len(payload),
            }
        )
    return entries


async def handle_get_draft(context: "ServerContext", arguments: dict[str, Any]) -> GetDraftResponse:
    """Return the composed fields of one draft, addressed by its handle."""
    account_id = str(arguments["account"])
    folder_path = str(arguments["folder"])
    draft_id = str(arguments["draft_id"])

    folder_decision = context.pdp.decide_folder_access(context.caller_id, account_id, folder_path)
    if not folder_decision.allowed:
        return _deny_draft_read(  # type: ignore[return-value]
            reason=folder_decision.reason,
            account=account_id,
            folder=folder_path,
            draft_id=draft_id,
        )
    assert folder_decision.folder_policy is not None
    # A folder whose ceiling stays below BODY can never answer this tool,
    # whoever the sender is. The denial is a visibility statement, not a
    # per-message verdict, so it is taken before any IMAP read.
    if _folder_ceiling_below(folder_decision.folder_policy, level_rank("BODY")):
        return _deny_draft_read(  # type: ignore[return-value]
            reason="visibility_below_BODY",
            account=account_id,
            folder=folder_path,
            draft_id=draft_id,
        )

    account, password = await _password_for(context, account_id)
    imap_folder = await _resolve_imap_folder(context, account_id, folder_path)
    resolution = await _resolve_draft_id(account, password, imap_folder, draft_id)
    if resolution.error_type is not None:
        return error_envelope(  # type: ignore[return-value]
            error_type=resolution.error_type,
            extra={"account": account_id, "folder": folder_path, "draft_id": draft_id},
        )
    assert resolution.record is not None
    record = resolution.record

    result = await imap_fetch_body(account, password, imap_folder, record.uid)
    if result is None:
        return error_envelope(  # type: ignore[return-value]
            error_type="draft_not_found",
            extra={"account": account_id, "folder": folder_path, "draft_id": draft_id},
        )
    envelope, body_text, message = result

    facts = _facts_from_envelope(envelope)
    message_decision = evaluate_message_against_folder(folder_decision.folder_policy, facts=facts)
    if not message_decision.allowed:
        return _deny_draft_read(  # type: ignore[return-value]
            reason=message_decision.reason,
            account=account_id,
            folder=folder_path,
            draft_id=draft_id,
        )
    if level_rank(message_decision.visibility) < level_rank("BODY"):
        return _deny_draft_read(  # type: ignore[return-value]
            reason="visibility_below_BODY",
            account=account_id,
            folder=folder_path,
            draft_id=draft_id,
        )

    return {
        "decision": "ALLOW",
        "result": "OK",
        "reason": message_decision.reason,
        "visibility_applied": message_decision.visibility,
        "account": account_id,
        "folder": folder_path,
        "draft_id": draft_id,
        "revision": record.revision,  # type: ignore[typeddict-item]
        "subject": envelope.subject,
        "to": _addresses(message, "To"),
        "cc": _addresses(message, "Cc"),
        "bcc": _addresses(message, "Bcc"),
        "body_text": body_text,
        "body_html": _html_body(message),
        "attachments": _attachment_meta(message),
    }


async def handle_list_drafts(
    context: "ServerContext", arguments: dict[str, Any]
) -> ListDraftsResponse:
    """List the drafts in one folder, editable ones under their handle.

    A message without an `X-SC-Draft-Id` was written by some other
    client; it is listed but cannot be revised through this server
    (ADR 0030). Where several messages share a handle, the highest
    revision is reported and the residue is left untouched.
    """
    account_id = str(arguments["account"])
    folder_path = str(arguments["folder"])

    folder_decision = context.pdp.decide_folder_access(context.caller_id, account_id, folder_path)
    if not folder_decision.allowed:
        return _deny_draft_read(  # type: ignore[return-value]
            reason=folder_decision.reason, account=account_id, folder=folder_path
        )
    folder_policy = folder_decision.folder_policy
    assert folder_policy is not None
    minimum_for_tool = level_rank("METADATA")
    # ADR 0031: list_drafts sits at the same bar as list_messages. When
    # no rule in the folder can ever reach METADATA the answer is a
    # denial, not an empty list — an empty list would assert that the
    # folder holds no drafts, which the caller is not entitled to know.
    if _folder_ceiling_below(folder_policy, minimum_for_tool):
        return _deny_draft_read(  # type: ignore[return-value]
            reason="visibility_below_METADATA", account=account_id, folder=folder_path
        )

    account, password = await _password_for(context, account_id)
    imap_folder = await _resolve_imap_folder(context, account_id, folder_path)
    uids = await imap_search_uids(account, password, imap_folder, "ALL")
    if not uids:
        return {
            "decision": "ALLOW",
            "result": "OK",
            "reason": folder_decision.reason,
            "account": account_id,
            "folder": folder_path,
            "drafts": [],
        }

    envelopes = await imap_fetch_envelopes_batch(account, password, imap_folder, uids)
    envelope_by_uid = {envelope.uid: envelope for envelope in envelopes}
    raw_headers = await imap_fetch_raw_headers_batch(account, password, imap_folder, uids)

    entries: list[DraftListEntry] = []
    live_by_handle: dict[str, int] = {}
    for uid in sorted(uids):
        envelope = envelope_by_uid.get(uid)
        if envelope is None:
            continue
        facts = _facts_from_envelope(envelope)
        message_decision = evaluate_message_against_folder(folder_policy, facts=facts)
        if not message_decision.allowed:
            continue
        if level_rank(message_decision.visibility) < minimum_for_tool:
            continue
        raw = raw_headers.get(uid)
        record = _parse_draft_headers(uid, raw) if raw is not None else None
        entry: DraftListEntry = {
            "subject": envelope.subject,
            "editable": bool(record is not None and record.revision is not None),
        }
        if record is not None and record.draft_id is not None:
            entry["draft_id"] = record.draft_id
            if record.revision is not None:
                entry["revision"] = record.revision
                # Residue of an interrupted revision: only the highest
                # revision of a handle is live (ADR 0030, rule 2).
                position = live_by_handle.get(record.draft_id)
                if position is None:
                    live_by_handle[record.draft_id] = len(entries)
                else:
                    previous = entries[position].get("revision") or 0
                    if record.revision > previous:
                        entries[position] = entry
                    continue
        entries.append(entry)

    return {
        "decision": "ALLOW",
        "result": "OK",
        "reason": folder_decision.reason,
        "account": account_id,
        "folder": folder_path,
        "drafts": entries,
    }


# ------------------------------------------------------------- revisions


# The closed set of members `changes` may carry (ADR 0031). Address
# fields take a list, everything else a string; every member also
# accepts `null` (and `[]` for an address list) to remove the field.
_ADDRESS_CHANGE_FIELDS = ("to", "cc", "bcc")
_TEXT_CHANGE_FIELDS = ("subject", "in_reply_to")
_BODY_CHANGE_FIELDS = ("body_text", "body_html")
CHANGE_FIELDS = _TEXT_CHANGE_FIELDS + _ADDRESS_CHANGE_FIELDS + _BODY_CHANGE_FIELDS
_HEADER_FOR_CHANGE_FIELD = {
    "subject": "Subject",
    "in_reply_to": "In-Reply-To",
    "to": "To",
    "cc": "Cc",
    "bcc": "Bcc",
}
_ADDRESS_PATTERN = r"^[^@\s<>,;:\"]+@[A-Za-z0-9](?:[A-Za-z0-9.\-]*[A-Za-z0-9])?\.[A-Za-z]{2,}$"


def _is_rfc5322_address(value: str) -> bool:
    """True when `value` is a single address this server will put on a
    recipient header. Deliberately narrower than RFC 5322 permits: a
    display name is allowed, the addr-spec must be an ordinary
    dot-atom@domain."""
    import re
    from email.utils import getaddresses

    parsed = getaddresses([value])
    if len(parsed) != 1:
        return False
    _, addr_spec = parsed[0]
    return bool(re.match(_ADDRESS_PATTERN, addr_spec))


def validate_changes(changes: Any) -> str | None:
    """Validate the `changes` object of `update_draft` (ADR 0031).

    Returns a caller-facing message when the object is malformed and
    None when it may proceed. Everything rejected here is a malformed
    call rather than a mailbox condition, so the dispatcher turns it
    into a JSON-RPC invalid-arguments error before any IMAP connection
    is opened.
    """
    if not isinstance(changes, dict):
        return "update_draft requires `changes` to be an object (ADR 0031)"
    if not changes:
        return "update_draft requires at least one member in `changes` (ADR 0031)"
    unknown = sorted(set(changes) - set(CHANGE_FIELDS))
    if unknown:
        return (
            f"update_draft `changes` carries unknown member(s) {unknown}; "
            f"allowed: {sorted(CHANGE_FIELDS)} (ADR 0031)"
        )
    for field in _TEXT_CHANGE_FIELDS + _BODY_CHANGE_FIELDS:
        if field not in changes:
            continue
        value = changes[field]
        if value is None:
            continue
        if not isinstance(value, str):
            return f"update_draft `changes.{field}` must be a string or null"
    for field in _ADDRESS_CHANGE_FIELDS:
        if field not in changes:
            continue
        value = changes[field]
        if value is None:
            continue
        if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
            return f"update_draft `changes.{field}` must be an array of strings or null"
        for item in value:
            if not _is_rfc5322_address(item):
                return f"update_draft `changes.{field}` holds a malformed address"
    # ADR 0031: a header value carrying CR or LF is rejected, never
    # sanitised — silently stripping it would let a caller smuggle a
    # second header past the schema.
    for field in _TEXT_CHANGE_FIELDS + _ADDRESS_CHANGE_FIELDS:
        value = changes.get(field)
        candidates = (
            [value] if isinstance(value, str) else (value if isinstance(value, list) else [])
        )
        for candidate in candidates:
            if isinstance(candidate, str) and ("\r" in candidate or "\n" in candidate):
                return (
                    f"update_draft `changes.{field}` contains a line break; "
                    "header values must be a single line (ADR 0031)"
                )
    return None


def change_hash(changes: dict[str, Any]) -> str:
    """sha256 over the canonical JSON encoding of `changes` (ADR 0031).

    Members sorted lexicographically, no insignificant whitespace,
    UTF-8, `null` members preserved. It hashes the *request*, not the
    produced message, so it survives the regenerated `Date`,
    `Message-ID` and MIME boundaries.
    """
    import hashlib
    import json

    canonical = json.dumps(changes, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _set_body_part(message: Any, content_type: str, text: str | None) -> None:
    """Replace the first non-attachment part of `content_type`.

    ADR 0031: removing the last body part leaves an empty `text/plain`
    part rather than a message with no body, so a removal writes an
    empty payload instead of dropping the part.
    """
    from email.mime.text import MIMEText

    replacement = "" if text is None else text
    for part in message.walk():
        if part.get_content_type() != content_type:
            continue
        if (part.get_content_disposition() or "") == "attachment":
            continue
        del part["Content-Transfer-Encoding"]
        part.set_payload(replacement + "\n", charset="utf-8")
        return

    subtype = content_type.split("/", 1)[1]
    new_part = MIMEText(replacement + "\n", subtype, "utf-8")
    if message.get_content_maintype() == "multipart":
        message.attach(new_part)
        return
    # A single-part message gaining a second representation becomes a
    # multipart/alternative holding what was already there plus the new
    # part; nothing of the original body is lost.
    original = message.get_payload(decode=True) or b""
    kept = MIMEText(
        original.decode(message.get_content_charset("utf-8"), errors="replace"),
        message.get_content_subtype(),
        "utf-8",
    )
    del message["Content-Type"]
    del message["Content-Transfer-Encoding"]
    message.set_type("multipart/alternative")
    message.set_payload([kept, new_part])


def apply_draft_changes(
    rfc822: bytes, *, changes: dict[str, Any], revision: int, request_hash: str
) -> bytes:
    """Produce the bytes of the next revision of a draft (ADR 0031).

    Everything `changes` does not address is carried over verbatim:
    attachment parts, unknown headers, `From`, `References`. Four
    headers are regenerated on every revision: `X-SC-Draft-Rev`,
    `Message-ID`, `Date` and `X-SC-Draft-Change-Hash`.
    """
    import email
    import io
    from email.generator import BytesGenerator
    from email.utils import formatdate, make_msgid

    message = email.message_from_bytes(rfc822)

    for field in _TEXT_CHANGE_FIELDS + _ADDRESS_CHANGE_FIELDS:
        if field not in changes:
            continue
        header = _HEADER_FOR_CHANGE_FIELD[field]
        value = changes[field]
        del message[header]
        if field in _ADDRESS_CHANGE_FIELDS:
            addresses = value or []
            if addresses:
                message[header] = ", ".join(addresses)
            continue
        if value is not None:
            message[header] = value

    if "body_text" in changes:
        _set_body_part(message, "text/plain", changes["body_text"])
    if "body_html" in changes:
        _set_body_part(message, "text/html", changes["body_html"])

    for header, value in (
        ("Date", formatdate(localtime=True)),
        ("Message-ID", make_msgid()),
        (DRAFT_REV_HEADER, str(revision)),
        (DRAFT_CHANGE_HASH_HEADER, request_hash),
    ):
        del message[header]
        message[header] = value

    buffer = io.BytesIO()
    BytesGenerator(buffer, mangle_from_=False, maxheaderlen=0).flatten(message)
    return buffer.getvalue()


async def _sweep_draft_residue(
    account: Any,
    password: str,
    imap_folder: str,
    draft_id: str,
    *,
    live_revision: int,
) -> None:
    """Remove the lower revisions left behind by an interrupted edit.

    ADR 0030 rule 2: at most one live message per handle. A revision
    that crashed between APPEND and DELETE leaves the previous message
    in place; the resuming transaction removes it, and when no
    transaction survived to resume, the next `update_draft` does —
    which is this call, the only one on the path that holds
    `draft_edit` and may therefore delete. Reads never reach here.

    A message carrying the handle but no usable revision is left alone:
    it cannot be ordered against `live_revision`, so it is not residue
    this rule can identify.

    A failure to delete does not fail the revision. The new message is
    already in the folder and is authoritative by revision order, so the
    caller is not blocked by a mailbox that refuses the EXPUNGE
    (ADR 0031 §the saga); the leftover stays visible as residue for the
    next attempt.
    """
    criteria = f'HEADER "{DRAFT_ID_HEADER}" "{draft_id}"'
    try:
        uids = await imap_search_uids(account, password, imap_folder, criteria)
        if len(uids) < 2:
            return
        raw_headers = await imap_fetch_raw_headers_batch(account, password, imap_folder, uids)
    except Exception:
        return
    residue: list[int] = []
    for uid, raw in sorted(raw_headers.items()):
        record = _parse_draft_headers(uid, raw)
        if record.draft_id != draft_id or record.revision is None:
            continue
        if record.revision < live_revision:
            residue.append(uid)
    if not residue:
        return
    try:
        await imap_expunge_uids(account, password, imap_folder, residue)
    except Exception:
        return


class UpdateDraftResponse(TypedDict, total=False):
    decision: Literal["ALLOW", "DENY"]
    result: NotRequired[Literal["OK", "ERROR"]]
    reason: NotRequired[str]
    error: NotRequired[dict[str, str]]
    account: str
    folder: str
    draft_id: str
    revision: NotRequired[int]
    current_revision: NotRequired[int]
    missing_capability: NotRequired[str]
    mechanism: NotRequired[str]
    tx_id: NotRequired[str | None]
    idempotent_replay: NotRequired[bool]


def _escalate_ambiguity(
    context: "ServerContext",
    *,
    account_id: str,
    folder: str,
    draft_id: str,
    candidates: tuple[int, ...],
) -> str:
    """Open a transaction for an ambiguous handle and park it at once.

    ADR 0030: two messages carrying the same handle and the same
    revision cannot be ordered, and the server picks neither. That is
    an incident, not merely a caller error — a human has to look at the
    mailbox. So a transaction is written that never runs a step: it is
    escalated to `needs_operator` (ADR 0007) in the same breath, it
    names the UIDs that collided, and the caller is handed its `tx_id`
    together with `draft_ambiguous`, so the report the caller files and
    the entry the operator works on are the same case.
    """
    wal = context.saga.wal
    tx_id = wal.begin(
        caller_id=context.caller_id,
        src_account=account_id,
        src_folder=folder,
        src_uid=candidates[0] if candidates else 0,
        dst_account=account_id,
        dst_folder=folder,
    )
    uid_list = ",".join(str(uid) for uid in candidates)
    wal.mark_needs_operator(
        tx_id,
        reason=f"draft_ambiguous: draft_id={draft_id} uids={uid_list}",
    )
    return tx_id


async def handle_update_draft(
    context: "ServerContext", arguments: dict[str, Any]
) -> UpdateDraftResponse:
    """Revise one draft field-wise under an optimistic revision check."""
    account_id = str(arguments["account"])
    folder_path = str(arguments["folder"])
    draft_id = str(arguments["draft_id"])
    base_revision = int(arguments["base_revision"])
    changes = dict(arguments["changes"])

    identity: dict[str, Any] = {
        "account": account_id,
        "folder": folder_path,
        "draft_id": draft_id,
    }

    folder_decision = context.pdp.decide_folder_access(context.caller_id, account_id, folder_path)
    if not folder_decision.allowed:
        return _deny_draft_read(  # type: ignore[return-value]
            reason=folder_decision.reason,
            account=account_id,
            folder=folder_path,
            draft_id=draft_id,
        )
    assert folder_decision.folder_policy is not None
    if not folder_decision.folder_policy.draft_edit:
        response: UpdateDraftResponse = {
            "decision": "DENY",
            "reason": "capability_missing",
            "account": account_id,
            "folder": folder_path,
            "draft_id": draft_id,
            "missing_capability": "draft_edit",
        }
        return response

    account, password = await _password_for(context, account_id)
    imap_folder = await _resolve_imap_folder(context, account_id, folder_path)

    # A folder the caller is authorized for but that does not exist on
    # the server is `folder_absent`, not an empty search result.
    stats = await imap_folder_stats(account, password, imap_folder)
    if stats.kind == "absent":
        return error_envelope(  # type: ignore[return-value]
            error_type="folder_absent", reason=folder_decision.reason, extra=identity
        )

    resolution = await _resolve_draft_id(account, password, imap_folder, draft_id)
    if resolution.error_type is not None:
        extra = dict(identity)
        if resolution.error_type == "draft_ambiguous":
            extra["tx_id"] = _escalate_ambiguity(
                context,
                account_id=account_id,
                folder=imap_folder,
                draft_id=draft_id,
                candidates=resolution.candidates,
            )
        return error_envelope(  # type: ignore[return-value]
            error_type=resolution.error_type,
            reason=folder_decision.reason,
            extra=extra,
        )
    assert resolution.record is not None
    record = resolution.record
    current_revision = int(record.revision or FIRST_REVISION)
    request_hash = change_hash(changes)

    if base_revision != current_revision:
        # A retry of a call whose response was lost: the caller still
        # holds the old base and submits the identical change, and the
        # revision now in the mailbox was produced by exactly that
        # request. Nothing is written (ADR 0031).
        if base_revision == current_revision - 1 and record.change_hash == request_hash:
            return {
                "decision": "ALLOW",
                "result": "OK",
                "reason": folder_decision.reason,
                "account": account_id,
                "folder": folder_path,
                "draft_id": draft_id,
                "revision": current_revision,
                "mechanism": "draft_revision",
                "idempotent_replay": True,
            }
        conflict = error_envelope(
            error_type="draft_revision_conflict",
            reason=folder_decision.reason,
            extra={**identity, "current_revision": current_revision},
        )
        return conflict  # type: ignore[return-value]

    new_revision = current_revision + 1

    def transform(raw: bytes) -> bytes:
        return apply_draft_changes(
            raw, changes=changes, revision=new_revision, request_hash=request_hash
        )

    async def locate() -> int | None:
        return await _resolve_revision_uid(account, password, imap_folder, draft_id, new_revision)

    saga_result = await context.saga.run_message_rewrite(
        caller_id=context.caller_id,
        account=account,
        password=password,
        folder=imap_folder,
        uid=record.uid,
        transform=transform,
        mechanism="draft_revision",
        locate=locate,
    )
    if saga_result.result == "ERROR":
        error_type = saga_result.error_type or "rewrite_failed"
        if error_type not in ("uid_not_found", "rewrite_failed"):
            error_type = "rewrite_failed"
        return error_envelope(  # type: ignore[return-value]
            error_type=error_type,
            detail=saga_result.error_type or "",
            reason=folder_decision.reason,
            extra=identity,
        )
    await _sweep_draft_residue(account, password, imap_folder, draft_id, live_revision=new_revision)
    return {
        "decision": "ALLOW",
        "result": "OK",
        "reason": folder_decision.reason,
        "account": account_id,
        "folder": folder_path,
        "draft_id": draft_id,
        "revision": new_revision,
        "mechanism": saga_result.mechanism,
        "tx_id": saga_result.tx_id,
    }
