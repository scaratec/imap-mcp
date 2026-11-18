"""Draft handlers: create_draft, create_reply_draft, get_draft,
list_drafts, update_draft, discard_draft.

Adheres to:
- ADR 0042: Cryptographic draft provenance via HMAC-SHA256 and wal.db
- ADR 0044: Normalized response envelope and write-only own-draft exception
- ADR 0045: Capability checks and outbound draft_recipients guardrail
- ADR 0048: Pre-effect intent and post-effect outcome audit records
- ADR 0057: Immutable received mail and structured draft composition
- ADR 0063: Structured own-draft composition, attachments, quote isolation, and revisions
"""

from __future__ import annotations

import base64
import email
import email.encoders
import hashlib
import json
import uuid
from email.message import Message
from email.mime.base import MIMEBase
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formatdate, make_msgid
from typing import TYPE_CHECKING, Any, Literal, NamedTuple

from .. import reply as reply_builder
from ..imap_core import (
    append_message as imap_append_message,
)
from ..imap_core import (
    expunge_uids as imap_expunge_uids,
)
from ..imap_core import (
    fetch_body as imap_fetch_body,
)
from ..imap_core import (
    fetch_envelope as imap_fetch_envelope,
)
from ..imap_core import (
    fetch_envelopes_batch as imap_fetch_envelopes_batch,
)
from ..imap_core import (
    fetch_full_message as imap_fetch_full_message,
)
from ..imap_core import (
    fetch_message_for_reply as imap_fetch_message_for_reply,
)
from ..imap_core import (
    fetch_raw_headers_batch as imap_fetch_raw_headers_batch,
)
from ..imap_core import (
    find_trash_folder,
)
from ..imap_core import (
    folder_stats as imap_folder_stats,
)
from ..imap_core import (
    gmail_label_swap as imap_gmail_label_swap,
)
from ..imap_core import (
    list_mailboxes as imap_list_mailboxes,
)
from ..imap_core import (
    move_uids as imap_move_uids,
)
from ..imap_core import (
    search_uids as imap_search_uids,
)
from ..policy import (
    level_rank,
    normalize_address,
    normalize_domain,
)
from ..provenance import (
    compute_canonical_digest,
    compute_provenance_mac,
    verify_provenance_mac,
)
from ._common import (
    _ensure_folder_aliases,
    _facts_from_envelope,
    _is_google_provider,
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
DRAFT_PROVENANCE_HEADER = "X-SC-Provenance"
DRAFT_CHANGE_HASH_HEADER = "X-SC-Draft-Change-Hash"
_X_SC_PREFIX = b"x-sc-"
FIRST_REVISION = 1
DRAFT_APPEND_FLAGS = ("\\Draft", "\\Seen")

DraftFormat = Literal["text/plain", "text/html", "multipart/alternative", "multipart/mixed"]


def _strip_x_sc_headers(header_block: bytes) -> bytes:
    """Strip all X-SC-* headers case-insensitively before appending (ADR 0042 §1)."""
    kept: list[bytes] = []
    dropping = False
    for line in header_block.splitlines(keepends=True):
        if line[:1] in (b" ", b"\t"):
            if not dropping:
                kept.append(line)
            continue
        dropping = line.lower().startswith(_X_SC_PREFIX)
        if not dropping:
            kept.append(line)
    return b"".join(kept)


def _split_header_block(rfc822: bytes) -> tuple[bytes, bytes]:
    for sep in (b"\r\n\r\n", b"\n\n"):
        idx = rfc822.find(sep)
        if idx != -1:
            split_len = len(sep) // 2
            return rfc822[: idx + split_len], rfc822[idx + split_len :]
    return rfc822, b""


def _inject_provenance_headers(
    rfc822: bytes,
    *,
    draft_id: str,
    revision: int,
    provenance_mac: str,
    change_hash: str | None = None,
) -> bytes:
    header_block, remainder = _split_header_block(rfc822)
    clean_headers = _strip_x_sc_headers(header_block)
    nl = b"\r\n" if b"\r\n" in rfc822 else b"\n"

    injected = (
        f"{DRAFT_ID_HEADER}: {draft_id}".encode("ascii")
        + nl
        + f"{DRAFT_REV_HEADER}: {revision}".encode("ascii")
        + nl
        + f"{DRAFT_PROVENANCE_HEADER}: {provenance_mac}".encode("ascii")
        + nl
    )
    if change_hash:
        injected += f"{DRAFT_CHANGE_HASH_HEADER}: {change_hash}".encode("ascii") + nl

    return injected + clean_headers + remainder


def _write_audit_intent(context: ServerContext, tool: str, arguments: dict[str, Any]) -> str:
    req_id = getattr(context, "request_id", None) or str(uuid.uuid4())
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


def check_draft_recipients(folder_policy: Any, recipients: list[str]) -> bool:
    """Verify draft_recipients allowlist (ADR 0045 §2)."""
    if not folder_policy or not folder_policy.draft_recipients:
        return True
    cfg = folder_policy.draft_recipients
    allowed_domains = {normalize_domain(d) for d in cfg.allowed_domains}
    allowed_addrs = {a.strip().lower() for a in cfg.allowed_addresses}

    if not allowed_domains and not allowed_addrs:
        return True

    for r in recipients:
        addr, dom = normalize_address(r)
        if addr in allowed_addrs:
            continue
        if dom and dom in allowed_domains:
            continue
        return False
    return True


class _DraftHeaders(NamedTuple):
    uid: int
    draft_id: str | None
    revision: int | None
    provenance_mac: str | None
    change_hash: str | None = None


def _parse_draft_headers(uid: int, raw_header: bytes) -> _DraftHeaders:
    msg = email.message_from_bytes(raw_header)
    raw_id = msg.get(DRAFT_ID_HEADER)
    draft_id = raw_id.strip() if raw_id else None
    if not draft_id:
        return _DraftHeaders(uid=uid, draft_id=None, revision=None, provenance_mac=None)

    raw_rev = msg.get(DRAFT_REV_HEADER)
    revision = None
    if raw_rev:
        try:
            val = int(raw_rev.strip())
            if val >= 1:
                revision = val
        except ValueError:
            pass

    raw_prov = msg.get(DRAFT_PROVENANCE_HEADER)
    provenance_mac = raw_prov.strip() if raw_prov else None

    raw_hash = msg.get(DRAFT_CHANGE_HASH_HEADER)
    change_hash = raw_hash.strip() if raw_hash else None

    return _DraftHeaders(
        uid=uid,
        draft_id=draft_id,
        revision=revision,
        provenance_mac=provenance_mac,
        change_hash=change_hash,
    )


class _Resolution(NamedTuple):
    record: _DraftHeaders | None
    error_type: str | None
    candidates: tuple[int, ...] = ()
    raw_msg: bytes | None = None
    all_uids: tuple[int, ...] = ()


async def _resolve_draft_id(
    account: Any, password: str, imap_folder: str, draft_id: str
) -> _Resolution:
    criteria = f'HEADER "{DRAFT_ID_HEADER}" "{draft_id}"'
    uids = await imap_search_uids(account, password, imap_folder, criteria)
    if not uids:
        return _Resolution(record=None, error_type="not_found")
    raw_headers = await imap_fetch_raw_headers_batch(account, password, imap_folder, uids)
    matches: list[_DraftHeaders] = []
    for uid, raw in sorted(raw_headers.items()):
        rec = _parse_draft_headers(uid, raw)
        if rec.draft_id == draft_id:
            matches.append(rec)
    if not matches:
        return _Resolution(record=None, error_type="not_found")

    ordered = [r for r in matches if r.revision is not None]
    if not ordered:
        return _Resolution(record=None, error_type="draft_not_editable")

    top_rev = max(r.revision or 0 for r in ordered)
    live = [r for r in ordered if r.revision == top_rev]
    if len(live) > 1:
        return _Resolution(
            record=None,
            error_type="draft_ambiguous",
            candidates=tuple(r.uid for r in live),
            all_uids=tuple(r.uid for r in matches),
        )
    return _Resolution(
        record=live[0],
        error_type=None,
        all_uids=tuple(r.uid for r in matches),
    )


def _format_address_list(addresses: list[str]) -> str:
    from email.utils import formataddr, getaddresses

    rendered = [formataddr(pair, charset="utf-8") for pair in getaddresses(addresses)]
    return ", ".join(rendered)


def compose_draft_message(arguments: dict[str, Any], account: Any) -> tuple[bytes, DraftFormat]:
    """Compose structured draft into RFC 822 bytes (ADR 0063)."""
    to_list = arguments.get("to") or []
    cc_list = arguments.get("cc") or []
    bcc_list = arguments.get("bcc") or []
    subject = arguments.get("subject") or ""
    body_text = arguments.get("body_text")
    body_html = arguments.get("body_html")
    in_reply_to = arguments.get("in_reply_to")
    attachments = arguments.get("attachments") or []

    # From address derivation (ADR 0050, ADR 0063)
    from_addr = ""
    if account.user and "@" in account.user:
        from_addr = account.user
    elif account.id and "@" in account.id:
        from_addr = account.id
    elif account.identity:
        from_addr = account.identity
    else:
        from_addr = account.id

    if body_html is not None and body_text is None:
        body_text = reply_builder.strip_html_preserving_paragraphs(body_html)

    # Determine body structure
    if body_text is not None and body_html is not None:
        body_part: MIMEBase = MIMEMultipart("alternative")
        p_text = MIMEText(body_text + "\n", "plain", "utf-8")
        p_html = MIMEText(body_html + "\n", "html", "utf-8")
        body_part.attach(p_text)
        body_part.attach(p_html)
        top_format: DraftFormat = "multipart/alternative"
    elif body_html is not None:
        p_text = MIMEText((body_text or "") + "\n", "plain", "utf-8")
        p_html = MIMEText(body_html + "\n", "html", "utf-8")
        body_part = MIMEMultipart("alternative")
        body_part.attach(p_text)
        body_part.attach(p_html)
        top_format = "multipart/alternative"
    else:
        body_part = MIMEText((body_text or "") + "\n", "plain", "utf-8")
        top_format = "text/plain"

    if attachments:
        top_msg = MIMEMultipart("mixed")
        top_msg.attach(body_part)
        for att in attachments:
            fname = att["filename"]
            mtype = att["mime_type"]
            c_b64 = att["content_base64"]
            maintype, _, subtype = mtype.partition("/")
            part = MIMEBase(maintype, subtype or "octet-stream")
            part.set_payload(base64.b64decode(c_b64))
            email.encoders.encode_base64(part)
            part.add_header("Content-Disposition", "attachment", filename=fname)
            top_msg.attach(part)
        stored_fmt: DraftFormat = "multipart/mixed"
        msg: MIMEBase = top_msg
    else:
        msg = body_part
        stored_fmt = top_format

    msg["From"] = from_addr
    if to_list:
        msg["To"] = _format_address_list(to_list)
    if cc_list:
        msg["Cc"] = _format_address_list(cc_list)
    if bcc_list:
        msg["Bcc"] = _format_address_list(bcc_list)
    if subject:
        msg["Subject"] = subject
    if in_reply_to:
        msg["In-Reply-To"] = in_reply_to
    msg["Date"] = formatdate(localtime=True)
    msg["Message-ID"] = make_msgid()

    # Flatten
    import io
    from email.generator import BytesGenerator

    buf = io.BytesIO()
    BytesGenerator(buf, mangle_from_=False, maxheaderlen=78).flatten(msg)
    return buf.getvalue(), stored_fmt


def apply_draft_changes(
    rfc822: bytes, *, changes: dict[str, Any], revision: int, request_hash: str
) -> bytes:
    return apply_draft_changes_v3(
        rfc822,
        changes=changes,
        revision=revision,
        request_hash=request_hash,
        provenance_mac="v1:test",
        draft_id="d-test",
    )


# Closed change fields (ADR 0031, ADR 0063)
CHANGE_FIELDS = (
    "subject",
    "in_reply_to",
    "to",
    "cc",
    "bcc",
    "body_text",
    "body_html",
    "attachments",
)


def _reply_bodies(arguments: dict[str, Any]) -> tuple[str, str | None]:
    raw_text = arguments.get("reply_text")
    raw_html = arguments.get("reply_html")
    reply_html = str(raw_html) if raw_html is not None and str(raw_html).strip() else None
    reply_text = str(raw_text) if raw_text is not None else ""
    if not reply_text.strip() and reply_html is not None:
        reply_text = reply_builder.strip_html_preserving_paragraphs(reply_html)
    return reply_text, reply_html


def validate_changes(changes: Any) -> str | None:
    if not isinstance(changes, dict):
        return "update_draft `changes` must be an object (ADR 0031)"
    if not changes:
        return "update_draft requires at least one member in `changes` (ADR 0031)"
    for k in changes:
        if k not in CHANGE_FIELDS:
            return f"update_draft `changes` carries unknown member {k!r} (ADR 0031)"
    for f in ("subject", "in_reply_to", "body_text", "body_html"):
        if f in changes and changes[f] is not None and not isinstance(changes[f], str):
            return f"update_draft `changes.{f}` must be a string or null"
    for f in ("to", "cc", "bcc"):
        if (
            f in changes
            and changes[f] is not None
            and (
                not isinstance(changes[f], list) or not all(isinstance(x, str) for x in changes[f])
            )
        ):
            return f"update_draft `changes.{f}` must be an array of strings or null"
    return None


def _inject_draft_headers(rfc822: bytes, *, draft_id: str, revision: int) -> bytes:
    header_block, remainder = _split_header_block(rfc822)
    clean_headers = _strip_x_sc_headers(header_block)
    nl = b"\r\n" if b"\r\n" in rfc822 else b"\n"
    prefix = (
        f"{DRAFT_ID_HEADER}: {draft_id}".encode("ascii")
        + nl
        + f"{DRAFT_REV_HEADER}: {revision}".encode("ascii")
        + nl
    )
    return prefix + clean_headers + remainder


def compose_draft(fields: dict[str, Any]) -> bytes:
    dummy_account = type(
        "Dummy", (), {"user": "a@example.org", "id": "a@example.org", "identity": None}
    )()
    raw, _ = compose_draft_message(fields, dummy_account)
    return raw


def stored_format(rfc822: bytes) -> DraftFormat:
    msg = email.message_from_bytes(rfc822)
    part = msg
    while part.is_multipart():
        if part.get_content_type() == "multipart/alternative":
            return "multipart/alternative"
        children = part.get_payload()
        if not isinstance(children, list) or not children:
            return "text/plain"
        first = children[0]
        if not isinstance(first, Message):
            return "text/plain"
        part = first
    return "text/html" if part.get_content_type() == "text/html" else "text/plain"


def change_hash(changes: dict[str, Any]) -> str:
    """Canonical JSON encoding sha256 (ADR 0057, ADR 0063)."""
    canonical = json.dumps(changes, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


# ------------------------------------------------------------- create_draft


async def handle_create_draft(context: ServerContext, arguments: dict[str, Any]) -> dict[str, Any]:
    account_id = str(arguments["account"])
    folder_path = str(arguments["folder"])

    folder_decision = context.pdp.decide_folder_access(context.caller_id, account_id, folder_path)
    if not folder_decision.allowed:
        return {
            "decision": "DENY",
            "reason": folder_decision.reason,
            "account": account_id,
            "folder": folder_path,
        }
    assert folder_decision.folder_policy is not None
    fp = folder_decision.folder_policy
    if not fp.draft_append:
        return {
            "decision": "DENY",
            "reason": "capability_missing",
            "account": account_id,
            "folder": folder_path,
        }

    account, password = await _password_for(context, account_id)
    imap_folder = await _resolve_imap_folder(context, account_id, folder_path)

    # Recipient validation
    all_recips: list[str] = []
    if "rfc822" in arguments:
        message_bytes = str(arguments["rfc822"]).encode("utf-8")
        parsed_msg = email.message_from_bytes(message_bytes)
        from email.utils import getaddresses

        for h in ("To", "Cc", "Bcc"):
            all_recips.extend(a for _, a in getaddresses(parsed_msg.get_all(h, [])) if a)
        fmt = stored_format(message_bytes)
    else:
        all_recips.extend(arguments.get("to") or [])
        all_recips.extend(arguments.get("cc") or [])
        all_recips.extend(arguments.get("bcc") or [])
        message_bytes, fmt = compose_draft_message(arguments, account)

    if not check_draft_recipients(fp, all_recips):
        return {
            "decision": "DENY",
            "reason": "recipient_not_allowed",
            "account": account_id,
            "folder": folder_path,
        }

    draft_id = str(uuid.uuid4())
    canonical_digest = compute_canonical_digest(message_bytes)
    provenance_mac = compute_provenance_mac(
        context.provenance_hmac_key,
        context.caller_id,
        account_id,
        draft_id,
        FIRST_REVISION,
        canonical_digest,
    )

    final_rfc822 = _inject_provenance_headers(
        message_bytes,
        draft_id=draft_id,
        revision=FIRST_REVISION,
        provenance_mac=provenance_mac,
    )

    # Pre-effect intent audit
    _write_audit_intent(context, "create_draft", arguments)

    try:
        append_res = await imap_append_message(
            account, password, imap_folder, final_rfc822, flags=DRAFT_APPEND_FLAGS
        )
    except TimeoutError:
        return error_envelope(
            error_type="append_timeout",
            detail="APPEND draft timed out",
            extra={"account": account_id, "folder": folder_path},
        )
    except Exception as exc:  # noqa: BLE001
        return error_envelope(
            error_type="append_failed",
            detail=str(exc),
            extra={"account": account_id, "folder": folder_path},
        )

    if append_res.outcome != "ok":
        return error_envelope(
            error_type="append_rejected",
            detail=append_res.imap_response or "APPEND draft rejected",
            extra={"account": account_id, "folder": folder_path},
        )

    # Record in wal provenance
    if context.saga is not None:
        parsed_final = email.message_from_bytes(final_rfc822)
        mid = parsed_final.get("Message-ID")
        context.saga.wal.record_provenance(
            draft_id, FIRST_REVISION, context.caller_id, account_id, mid, canonical_digest
        )

    return {
        "decision": "ALLOW",
        "result": "OK",
        "reason": folder_decision.reason,
        "account": account_id,
        "folder": folder_path,
        "draft_id": draft_id,
        "revision": FIRST_REVISION,
        "format": fmt,
    }


# ------------------------------------------------------------- create_reply_draft


async def handle_create_reply_draft(
    context: ServerContext, arguments: dict[str, Any]
) -> dict[str, Any]:
    account_id = str(arguments["account"])
    source_folder = str(arguments["source_folder"])
    uid = int(arguments["uid"])
    drafts_folder = str(arguments["drafts_folder"])
    raw_text = arguments.get("reply_text")
    raw_html = arguments.get("reply_html")

    reply_text = str(raw_text) if raw_text is not None else ""
    reply_html = str(raw_html) if raw_html is not None and str(raw_html).strip() else None
    if not reply_text.strip() and reply_html is not None:
        reply_text = reply_builder.strip_html_preserving_paragraphs(reply_html)

    if not reply_text.strip():
        return error_envelope(
            error_type="empty_reply_text",
            detail="Reply text or html must be non-empty",
            extra={
                "account": account_id,
                "source_folder": source_folder,
                "drafts_folder": drafts_folder,
            },
        )

    src_dec = context.pdp.decide_folder_access(context.caller_id, account_id, source_folder)
    if not src_dec.allowed:
        return {
            "decision": "DENY",
            "reason": src_dec.reason,
            "account": account_id,
            "folder": source_folder,
        }

    drafts_dec = context.pdp.decide_folder_access(context.caller_id, account_id, drafts_folder)
    if not drafts_dec.allowed:
        return {
            "decision": "DENY",
            "reason": drafts_dec.reason,
            "account": account_id,
            "folder": drafts_folder,
        }
    assert drafts_dec.folder_policy is not None
    if not drafts_dec.folder_policy.draft_append:
        return {
            "decision": "DENY",
            "reason": "capability_missing",
            "account": account_id,
            "folder": drafts_folder,
        }

    account, password = await _password_for(context, account_id)
    imap_src = await _resolve_imap_folder(context, account_id, source_folder)
    imap_dst = await _resolve_imap_folder(context, account_id, drafts_folder)

    # Fetch source message
    envelope = await imap_fetch_envelope(account, password, imap_src, uid)
    if envelope is None:
        return error_envelope(
            error_type="not_found",
            detail="Source message not found",
            extra={"account": account_id, "folder": source_folder, "uid": uid},
        )

    facts = _facts_from_envelope(envelope)
    eff_vis, eff_reason = context.pdp.evaluate_message_effective(
        context.caller_id, account_id, source_folder, facts
    )

    if level_rank(eff_vis) < level_rank("METADATA"):
        return error_envelope(
            error_type="not_found",
            detail="Source message not found",
            extra={"account": account_id, "folder": source_folder, "uid": uid},
        )

    if level_rank(eff_vis) < level_rank("BODY"):
        return {
            "decision": "DENY",
            "reason": "visibility_below_BODY",
            "account": account_id,
            "folder": source_folder,
            "uid": uid,
        }

    if not envelope.message_id:
        return error_envelope(
            error_type="missing_message_id",
            detail="Source message lacks Message-ID",
            extra={"account": account_id, "folder": source_folder, "uid": uid},
        )

    fetch_res = await imap_fetch_message_for_reply(account, password, imap_src, uid)
    if fetch_res is None:
        return error_envelope(
            error_type="not_found",
            detail="Source message not found",
            extra={"account": account_id, "folder": source_folder, "uid": uid},
        )
    source_msg, source_body = fetch_res

    self_identity = account.wire_user()
    if account.identity:
        self_identity = account.identity

    from email.header import decode_header as _dh
    from email.header import make_header as _mh

    src_from = str(_mh(_dh(source_msg.get("From") or "")))
    src_reply_to = source_msg.get("Reply-To")
    src_to = source_msg.get("To")
    src_cc = source_msg.get("Cc")
    src_subject = str(_mh(_dh(source_msg.get("Subject") or "")))
    src_date = source_msg.get("Date")
    src_references = source_msg.get("References")
    src_mid = envelope.message_id

    subject = reply_builder.build_reply_subject(src_subject)
    to = reply_builder.derive_reply_to(src_reply_to, src_from)
    cc = reply_builder.derive_reply_cc(src_to, src_cc, self_identity)
    attribution = reply_builder.build_attribution(src_date, src_from)
    quoted = reply_builder.quote_body(source_body)
    body = reply_builder.build_reply_body(reply_text, attribution, quoted)
    html_body_val = (
        reply_builder.build_reply_html(reply_html, attribution, source_body)
        if reply_html is not None
        else None
    )
    in_reply_to, references = reply_builder.build_threading_headers(src_mid, src_references)

    # Check recipient allowlist on drafts_folder
    from email.utils import getaddresses

    rep_addrs = [a for _, a in getaddresses([to, cc]) if a]
    if not check_draft_recipients(drafts_dec.folder_policy, rep_addrs):
        return {
            "decision": "DENY",
            "reason": "recipient_not_allowed",
            "account": account_id,
            "folder": drafts_folder,
        }

    reply_bytes = reply_builder.build_reply_message(
        self_identity=self_identity,
        reply_to=to,
        cc=cc,
        subject=subject,
        in_reply_to=in_reply_to,
        references=references,
        body=body,
        html_body=html_body_val,
    )

    draft_id = str(uuid.uuid4())
    canonical_digest = compute_canonical_digest(reply_bytes)
    provenance_mac = compute_provenance_mac(
        context.provenance_hmac_key,
        context.caller_id,
        account_id,
        draft_id,
        FIRST_REVISION,
        canonical_digest,
    )

    final_rfc822 = _inject_provenance_headers(
        reply_bytes,
        draft_id=draft_id,
        revision=FIRST_REVISION,
        provenance_mac=provenance_mac,
    )

    _write_audit_intent(context, "create_reply_draft", arguments)

    append_result = await imap_append_message(
        account, password, imap_dst, final_rfc822, flags=DRAFT_APPEND_FLAGS
    )
    if append_result.outcome != "ok":
        return error_envelope(
            error_type="append_failed",
            detail=append_result.imap_response or "APPEND reply draft failed",
            extra={
                "account": account_id,
                "source_folder": source_folder,
                "drafts_folder": drafts_folder,
                "uid": uid,
            },
        )

    if context.saga is not None:
        p_final = email.message_from_bytes(final_rfc822)
        mid = p_final.get("Message-ID")
        context.saga.wal.record_provenance(
            draft_id, FIRST_REVISION, context.caller_id, account_id, mid, canonical_digest
        )

    return {
        "decision": "ALLOW",
        "result": "OK",
        "reason": eff_reason,
        "account": account_id,
        "source_folder": source_folder,
        "drafts_folder": drafts_folder,
        "uid": uid,
        "draft_id": draft_id,
        "revision": FIRST_REVISION,
    }


# ------------------------------------------------------------- get_draft & list_drafts


async def handle_get_draft(context: ServerContext, arguments: dict[str, Any]) -> dict[str, Any]:
    account_id = str(arguments["account"])
    folder_path = str(arguments["folder"])
    draft_id = str(arguments["draft_id"])

    folder_decision = context.pdp.decide_folder_access(context.caller_id, account_id, folder_path)
    if not folder_decision.allowed:
        return {
            "decision": "DENY",
            "reason": folder_decision.reason,
            "account": account_id,
            "folder": folder_path,
        }

    assert folder_decision.folder_policy is not None
    max_vis = context.pdp.folder_max_visibility(folder_decision.folder_policy)
    if level_rank(max_vis) < level_rank("BODY"):
        return {
            "decision": "DENY",
            "reason": "visibility_below_BODY",
            "account": account_id,
            "folder": folder_path,
        }

    account, password = await _password_for(context, account_id)
    imap_folder = await _resolve_imap_folder(context, account_id, folder_path)
    resolution = await _resolve_draft_id(account, password, imap_folder, draft_id)
    if resolution.error_type is not None:
        return error_envelope(
            error_type=resolution.error_type,
            detail="Draft not found",
            extra={"account": account_id, "folder": folder_path, "draft_id": draft_id},
        )

    assert resolution.record is not None
    record = resolution.record

    result = await imap_fetch_body(account, password, imap_folder, record.uid)
    if result is None:
        return error_envelope(
            error_type="not_found",
            detail="Draft message body not found",
            extra={"account": account_id, "folder": folder_path, "draft_id": draft_id},
        )
    envelope, body_text, msg_raw = result
    msg: Any = msg_raw

    facts = _facts_from_envelope(envelope)
    eff_vis, eff_reason = context.pdp.evaluate_message_effective(
        context.caller_id, account_id, folder_path, facts
    )

    if level_rank(eff_vis) < level_rank("METADATA"):
        return error_envelope(
            error_type="not_found",
            detail="Draft not found",
            extra={"account": account_id, "folder": folder_path, "draft_id": draft_id},
        )

    if level_rank(eff_vis) < level_rank("BODY"):
        return {
            "decision": "DENY",
            "reason": "visibility_below_BODY",
            "account": account_id,
            "folder": folder_path,
        }

    from email.utils import getaddresses

    to_addrs = [a for _, a in getaddresses(msg.get_all("To", [])) if a]
    cc_addrs = [a for _, a in getaddresses(msg.get_all("Cc", [])) if a]
    bcc_addrs = [a for _, a in getaddresses(msg.get_all("Bcc", [])) if a]

    html_body = None
    for part in msg.walk():
        if part.get_content_type() == "text/html":
            p = part.get_payload(decode=True)
            if isinstance(p, bytes):
                html_body = p.decode(part.get_content_charset("utf-8"), errors="replace").rstrip(
                    "\r\n"
                )
                break

    all_parts = _walk_attachment_parts(msg)
    att_meta = []
    for idx, p in enumerate(all_parts):
        payload_bytes = p.get_payload(decode=True) or b""
        att_meta.append(
            {
                "index": idx,
                "filename": decode_mime_words(p.get_filename() or "") or None,
                "mime_type": p.get_content_type(),
                "size_bytes": len(payload_bytes),
            }
        )

    return {
        "decision": "ALLOW",
        "result": "OK",
        "reason": eff_reason,
        "account": account_id,
        "folder": folder_path,
        "draft_id": draft_id,
        "revision": record.revision,
        "subject": envelope.subject,
        "to": to_addrs,
        "cc": cc_addrs,
        "bcc": bcc_addrs,
        "body_text": body_text,
        "body_html": html_body,
        "attachments": att_meta,
    }


async def handle_list_drafts(context: ServerContext, arguments: dict[str, Any]) -> dict[str, Any]:
    account_id = str(arguments["account"])
    folder_path = str(arguments["folder"])

    folder_decision = context.pdp.decide_folder_access(context.caller_id, account_id, folder_path)
    if not folder_decision.allowed:
        return {
            "decision": "DENY",
            "reason": folder_decision.reason,
            "account": account_id,
            "folder": folder_path,
        }
    assert folder_decision.folder_policy is not None
    fp = folder_decision.folder_policy

    max_vis = context.pdp.folder_max_visibility(fp)
    if level_rank(max_vis) < level_rank("METADATA"):
        return {
            "decision": "DENY",
            "reason": "visibility_below_METADATA",
            "account": account_id,
            "folder": folder_path,
        }

    account, password = await _password_for(context, account_id)
    imap_folder = await _resolve_imap_folder(context, account_id, folder_path)
    stats = await imap_folder_stats(account, password, imap_folder)
    if stats.kind == "absent":
        return error_envelope(
            error_type="folder_absent",
            detail="Folder does not exist on IMAP server",
            reason=folder_decision.reason,
            extra={"account": account_id, "folder": folder_path},
        )
    if stats.kind == "select_failed":
        return error_envelope(
            error_type="select_failed",
            detail=stats.imap_response or "SELECT mailbox failed",
            reason=folder_decision.reason,
            extra={"account": account_id, "folder": folder_path},
        )
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
    env_by_uid = {e.uid: e for e in envelopes}
    raw_headers = await imap_fetch_raw_headers_batch(account, password, imap_folder, uids)

    entries: list[dict[str, Any]] = []
    live_by_handle: dict[str, int] = {}

    for uid in sorted(uids):
        env = env_by_uid.get(uid)
        if env is None:
            continue
        facts = _facts_from_envelope(env)
        eff_vis, _eff_reason = context.pdp.evaluate_message_effective(
            context.caller_id, account_id, folder_path, facts
        )
        if level_rank(eff_vis) < level_rank("METADATA"):
            continue

        raw = raw_headers.get(uid)
        rec = _parse_draft_headers(uid, raw) if raw else None

        # Verify provenance ownership & editability
        is_editable = False
        if (
            rec
            and rec.draft_id
            and rec.revision
            and context.saga is not None
            and context.saga.wal.is_caller_draft_owner(rec.draft_id, context.caller_id, account_id)
        ):
            # Verify provenance header MAC
            try:
                full_raw = await imap_fetch_full_message(account, password, imap_folder, uid)
                if full_raw:
                    digest = compute_canonical_digest(full_raw)
                    is_editable = verify_provenance_mac(
                        context.provenance_hmac_key,
                        rec.provenance_mac,
                        context.caller_id,
                        account_id,
                        rec.draft_id,
                        rec.revision,
                        digest,
                    )
            except Exception:  # noqa: BLE001
                is_editable = False

        subject_val = None if level_rank(eff_vis) == level_rank("METADATA") else env.subject
        entry: dict[str, Any] = {
            "subject": subject_val,
            "editable": is_editable,
        }
        if level_rank(eff_vis) == level_rank("METADATA"):
            entry["redacted_fields"] = ["envelope", "headers", "body", "attachments"]

        if rec and rec.draft_id:
            entry["draft_id"] = rec.draft_id
            if rec.revision is not None:
                entry["revision"] = rec.revision
                pos = live_by_handle.get(rec.draft_id)
                if pos is None:
                    live_by_handle[rec.draft_id] = len(entries)
                else:
                    prev_rev = entries[pos].get("revision") or 0
                    if rec.revision > prev_rev:
                        entries[pos] = entry
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


# ------------------------------------------------------------- update_draft


def apply_draft_changes_v3(
    rfc822: bytes,
    *,
    changes: dict[str, Any],
    revision: int,
    request_hash: str,
    provenance_mac: str,
    draft_id: str,
) -> bytes:
    msg: Any = email.message_from_bytes(rfc822)

    for field in ("subject", "in_reply_to"):
        if field in changes:
            h = "Subject" if field == "subject" else "In-Reply-To"
            del msg[h]
            if changes[field] is not None:
                msg[h] = str(changes[field])

    for field in ("to", "cc", "bcc"):
        if field in changes:
            h = field.capitalize()
            del msg[h]
            val = changes[field]
            if val:
                msg[h] = _format_address_list(val)

    if "body_text" in changes or "body_html" in changes:
        new_text = changes.get("body_text")
        new_html = changes.get("body_html")
        if new_html is not None and new_text is None:
            new_text = reply_builder.strip_html_preserving_paragraphs(new_html)

        # Replace or update body parts
        if new_html is not None and new_text is not None:
            alt_part = MIMEMultipart("alternative")
            alt_part.attach(MIMEText(new_text + "\n", "plain", "utf-8"))
            alt_part.attach(MIMEText(new_html + "\n", "html", "utf-8"))
            replacement: MIMEBase = alt_part
        elif new_html is not None:
            alt_part = MIMEMultipart("alternative")
            alt_part.attach(MIMEText((new_text or "") + "\n", "plain", "utf-8"))
            alt_part.attach(MIMEText(new_html + "\n", "html", "utf-8"))
            replacement = alt_part
        else:
            replacement = MIMEText((new_text or "") + "\n", "plain", "utf-8")

        old_headers = list(msg.items())
        # If original was multipart/mixed with attachments, preserve attachments
        if msg.is_multipart() and msg.get_content_type() == "multipart/mixed":
            new_mixed = MIMEMultipart("mixed")
            for k, v in old_headers:
                if k.lower() not in ("content-type", "mime-version"):
                    new_mixed[k] = v
            new_mixed.attach(replacement)
            # Reattach existing attachments
            for part in msg.get_payload():
                if (part.get("Content-Disposition") or "").lower().startswith("attachment"):
                    new_mixed.attach(part)
            msg = new_mixed
        else:
            msg = replacement
            for k, v in old_headers:
                if (
                    k.lower() not in ("content-type", "mime-version", "content-transfer-encoding")
                    and k not in msg
                ):
                    msg[k] = v

    # Handle changes.attachments if present (ADR 0063)
    if "attachments" in changes:
        atts = changes["attachments"] or []
        old_headers = list(msg.items())
        # Rebuild as multipart/mixed
        mixed = MIMEMultipart("mixed")
        for k, v in old_headers:
            if k.lower() not in ("content-type", "mime-version"):
                mixed[k] = v
        # Extract existing body
        if msg.is_multipart() and msg.get_content_type() == "multipart/mixed":
            body_child = msg.get_payload()[0]
            mixed.attach(body_child)
        else:
            mixed.attach(msg)
        for att in atts:
            fname = att["filename"]
            mtype = att["mime_type"]
            c_b64 = att["content_base64"]
            maintype, _, subtype = mtype.partition("/")
            p = MIMEBase(maintype, subtype or "octet-stream")
            p.set_payload(base64.b64decode(c_b64))
            email.encoders.encode_base64(p)
            p.add_header("Content-Disposition", "attachment", filename=fname)
            mixed.attach(p)
        msg = mixed

    del msg["Date"]
    msg["Date"] = formatdate(localtime=True)
    del msg["Message-ID"]
    msg["Message-ID"] = make_msgid()

    import io
    from email.generator import BytesGenerator

    buf = io.BytesIO()
    BytesGenerator(buf, mangle_from_=False, maxheaderlen=0).flatten(msg)
    flattened = buf.getvalue()

    return _inject_provenance_headers(
        flattened,
        draft_id=draft_id,
        revision=revision,
        provenance_mac=provenance_mac,
        change_hash=request_hash,
    )


async def handle_update_draft(context: ServerContext, arguments: dict[str, Any]) -> dict[str, Any]:
    account_id = str(arguments["account"])
    folder_path = str(arguments["folder"])
    draft_id = str(arguments["draft_id"])
    base_revision = int(arguments["base_revision"])
    changes = dict(arguments["changes"])

    folder_decision = context.pdp.decide_folder_access(context.caller_id, account_id, folder_path)
    if not folder_decision.allowed:
        return {
            "decision": "DENY",
            "reason": folder_decision.reason,
            "account": account_id,
            "folder": folder_path,
        }

    assert folder_decision.folder_policy is not None
    fp = folder_decision.folder_policy
    if not fp.draft_edit:
        return {
            "decision": "DENY",
            "reason": "capability_missing",
            "account": account_id,
            "folder": folder_path,
        }

    account, password = await _password_for(context, account_id)
    imap_folder = await _resolve_imap_folder(context, account_id, folder_path)

    stats = await imap_folder_stats(account, password, imap_folder)
    if stats.kind == "absent":
        return error_envelope(
            error_type="folder_absent",
            detail="Folder does not exist on IMAP server",
            reason=folder_decision.reason,
            extra={"account": account_id, "folder": folder_path, "draft_id": draft_id},
        )
    if stats.kind == "select_failed":
        return error_envelope(
            error_type="select_failed",
            detail=stats.imap_response or "SELECT mailbox failed",
            reason=folder_decision.reason,
            extra={"account": account_id, "folder": folder_path, "draft_id": draft_id},
        )

    instance_id = str(uuid.uuid4())
    leased = False
    if context.saga is not None:
        leased = context.saga.wal.acquire_draft_lease(account_id, draft_id, instance_id)

    try:
        return await _do_update_draft(
            context,
            arguments,
            account_id,
            folder_path,
            draft_id,
            base_revision,
            changes,
            folder_decision,
            fp,
            account,
            password,
            imap_folder,
        )
    finally:
        if leased and context.saga is not None:
            context.saga.wal.release_draft_lease(account_id, draft_id, instance_id)


async def _do_update_draft(
    context: ServerContext,
    arguments: dict[str, Any],
    account_id: str,
    folder_path: str,
    draft_id: str,
    base_revision: int,
    changes: dict[str, Any],
    folder_decision: Any,
    fp: Any,
    account: Any,
    password: str,
    imap_folder: str,
) -> dict[str, Any]:
    # ADR 0042 §2: Ownership verification via WAL
    if context.saga is not None and not context.saga.wal.is_caller_draft_owner(
        draft_id, context.caller_id, account_id
    ):
        return error_envelope(
            error_type="not_found",
            detail="Draft not found",
            reason=folder_decision.reason,
            extra={"account": account_id, "folder": folder_path, "draft_id": draft_id},
        )

    resolution = await _resolve_draft_id(account, password, imap_folder, draft_id)
    if resolution.error_type is not None:
        extra = {"account": account_id, "folder": folder_path, "draft_id": draft_id}
        if resolution.error_type == "draft_ambiguous":
            ambig_tx_id = ""
            if context.saga is not None:
                src_uid = resolution.candidates[0] if resolution.candidates else 0
                ambig_tx_id = context.saga.wal.begin(
                    context.caller_id,
                    account_id,
                    folder_path,
                    src_uid,
                    account_id,
                    folder_path,
                )
                context.saga._audit_step(
                    ambig_tx_id, "escalated", outcome="ERROR", reason="draft_ambiguous"
                )
                context.saga.wal.mark_needs_operator(ambig_tx_id, reason="draft_ambiguous")
            extra["tx_id"] = ambig_tx_id
            return error_envelope(
                error_type="draft_ambiguous",
                detail="Draft ambiguous: multiple revisions share draft_id and revision",
                reason=folder_decision.reason,
                extra=extra,
            )
        return error_envelope(
            error_type=resolution.error_type,
            detail="Draft not found",
            reason=folder_decision.reason,
            extra=extra,
        )

    assert resolution.record is not None
    rec = resolution.record
    current_revision = int(rec.revision or FIRST_REVISION)
    req_hash = change_hash(changes)

    # Idempotent replay check (ADR 0063 §3)
    if base_revision != current_revision:
        if base_revision == current_revision - 1 and rec.change_hash == req_hash:
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
        return error_envelope(
            error_type="draft_revision_conflict",
            detail="Draft revision conflict",
            reason=folder_decision.reason,
            extra={
                "account": account_id,
                "folder": folder_path,
                "draft_id": draft_id,
                "current_revision": current_revision,
            },
        )

    # Fetch full raw message to verify existing provenance MAC
    raw_existing = await imap_fetch_full_message(account, password, imap_folder, rec.uid)
    if raw_existing is None:
        return error_envelope(
            error_type="not_found",
            detail="Draft message not found",
            reason=folder_decision.reason,
            extra={"account": account_id, "folder": folder_path, "draft_id": draft_id},
        )

    digest_existing = compute_canonical_digest(raw_existing)
    is_valid_prov = verify_provenance_mac(
        context.provenance_hmac_key,
        rec.provenance_mac,
        context.caller_id,
        account_id,
        draft_id,
        current_revision,
        digest_existing,
    )
    if not is_valid_prov:
        return error_envelope(
            error_type="draft_not_editable",
            detail="Draft provenance broken or tampered",
            reason=folder_decision.reason,
            extra={"account": account_id, "folder": folder_path, "draft_id": draft_id},
        )

    # Check recipient allowlist on updated recipients
    updated_recips: list[str] = []
    if "to" in changes or "cc" in changes or "bcc" in changes:
        existing_msg: Any = email.message_from_bytes(raw_existing)
        from email.utils import getaddresses

        for h in ("to", "cc", "bcc"):
            if h in changes:
                updated_recips.extend(changes[h] or [])
            else:
                updated_recips.extend(
                    a for _, a in getaddresses(existing_msg.get_all(h.capitalize(), [])) if a
                )
        if not check_draft_recipients(fp, updated_recips):
            return {
                "decision": "DENY",
                "reason": "recipient_not_allowed",
                "account": account_id,
                "folder": folder_path,
            }

    new_revision = current_revision + 1

    # Pre-calculate new provenance MAC
    dummy_mac = compute_provenance_mac(
        context.provenance_hmac_key,
        context.caller_id,
        account_id,
        draft_id,
        new_revision,
        "dummy",
    )
    temp_bytes = apply_draft_changes_v3(
        raw_existing,
        changes=changes,
        revision=new_revision,
        request_hash=req_hash,
        provenance_mac=dummy_mac,
        draft_id=draft_id,
    )
    new_digest = compute_canonical_digest(temp_bytes)
    final_mac = compute_provenance_mac(
        context.provenance_hmac_key,
        context.caller_id,
        account_id,
        draft_id,
        new_revision,
        new_digest,
    )
    final_bytes = apply_draft_changes_v3(
        raw_existing,
        changes=changes,
        revision=new_revision,
        request_hash=req_hash,
        provenance_mac=final_mac,
        draft_id=draft_id,
    )

    from ..saga import _maybe_crash

    tx_id: str | None = None
    if context.saga is not None:
        tx_id = context.saga.wal.begin(
            caller_id=context.caller_id,
            src_account=account_id,
            src_folder=folder_path,
            src_uid=rec.uid,
            dst_account=account_id,
            dst_folder=folder_path,
        )
        context.saga.wal.record_fetch(
            tx_id=tx_id,
            message_id=draft_id,
            content_hash=req_hash,
            raw_sha256=digest_existing,
            canonical_sha256=new_digest,
            size_bytes=len(final_bytes),
            staged_rfc822=final_bytes,
        )
        context.saga._audit_step(tx_id, "begin")
        context.saga._audit_step(tx_id, "fetched")

    _write_audit_intent(context, "update_draft", arguments)

    # Append new revision
    try:
        append_res = await imap_append_message(
            account, password, imap_folder, final_bytes, flags=DRAFT_APPEND_FLAGS
        )
    except Exception as exc:  # noqa: BLE001
        if tx_id and context.saga is not None:
            context.saga.wal.bump_retry(tx_id, f"append_failed: {exc}")
        return error_envelope(
            error_type="append_failed",
            detail=str(exc),
            reason=folder_decision.reason,
            extra={"account": account_id, "folder": folder_path, "draft_id": draft_id},
        )

    if append_res.outcome != "ok":
        if tx_id and context.saga is not None:
            context.saga.wal.bump_retry(tx_id, f"append_rejected: {append_res.imap_response}")
        return error_envelope(
            error_type="append_rejected",
            detail=append_res.imap_response or "APPEND revision failed",
            reason=folder_decision.reason,
            extra={"account": account_id, "folder": folder_path, "draft_id": draft_id},
        )

    if tx_id and context.saga is not None:
        target_uid = getattr(append_res, "append_uid", None)
        context.saga.wal.mark_staged(tx_id, target_uid)
        context.saga._audit_step(tx_id, "staged")

    _maybe_crash("post_append")

    # Delete previous revision(s)
    uids_to_expunge = list(resolution.all_uids) if resolution.all_uids else [rec.uid]
    expunge_err: Exception | None = None
    try:
        await imap_expunge_uids(account, password, imap_folder, uids_to_expunge)
    except Exception as exc:  # noqa: BLE001
        expunge_err = exc

    if expunge_err is not None:
        if tx_id and context.saga is not None:
            count = context.saga.wal.bump_retry(tx_id, f"delete_failed: {expunge_err}")
            if count >= context.saga.retry_limit:
                context.saga._audit_step(tx_id, "escalated", outcome="ERROR")
                context.saga.wal.mark_needs_operator(tx_id)
        res_err: dict[str, Any] = {
            "decision": "ALLOW",
            "result": "ERROR",
            "error": {
                "type": "transient_imap_failure",
                "message": str(expunge_err),
            },
            "reason": folder_decision.reason,
            "account": account_id,
            "folder": folder_path,
            "draft_id": draft_id,
            "revision": new_revision,
            "mechanism": "draft_revision",
        }
        if tx_id:
            res_err["tx_id"] = tx_id
        return res_err

    _maybe_crash("post_delete")

    if tx_id and context.saga is not None:
        context.saga.wal.mark_deleted(tx_id)
        context.saga._audit_step(tx_id, "commit")
        context.saga.wal.commit(tx_id)

        p_final = email.message_from_bytes(final_bytes)
        mid = p_final.get("Message-ID")
        context.saga.wal.record_provenance(
            draft_id, new_revision, context.caller_id, account_id, mid, new_digest
        )

    res_ok: dict[str, Any] = {
        "decision": "ALLOW",
        "result": "OK",
        "reason": folder_decision.reason,
        "account": account_id,
        "folder": folder_path,
        "draft_id": draft_id,
        "revision": new_revision,
        "mechanism": "draft_revision",
    }
    if tx_id:
        res_ok["tx_id"] = tx_id
    return res_ok


# ------------------------------------------------------------- discard_draft


async def _uids_carrying_handle(
    account: Any, password: str, imap_folder: str, draft_id: str
) -> list[int]:
    criteria = f'HEADER "{DRAFT_ID_HEADER}" "{draft_id}"'
    uids = await imap_search_uids(account, password, imap_folder, criteria)
    if not uids:
        return []
    raw_headers = await imap_fetch_raw_headers_batch(account, password, imap_folder, uids)
    return [
        uid
        for uid, raw in sorted(raw_headers.items())
        if _parse_draft_headers(uid, raw).draft_id == draft_id
    ]


async def handle_discard_draft(context: ServerContext, arguments: dict[str, Any]) -> dict[str, Any]:
    account_id = str(arguments["account"])
    folder_path = str(arguments["folder"])
    draft_id = str(arguments["draft_id"])

    # 1. Source \Drafts move_out
    src_dec = context.pdp.decide_folder_access(context.caller_id, account_id, folder_path)
    if not src_dec.allowed:
        return {
            "decision": "DENY",
            "reason": src_dec.reason,
            "account": account_id,
            "folder": folder_path,
        }
    assert src_dec.folder_policy is not None
    if not src_dec.folder_policy.move_out:
        return {
            "decision": "DENY",
            "reason": "capability_missing",
            "account": account_id,
            "folder": folder_path,
        }

    # 2. Account \Trash existence
    account, password = await _password_for(context, account_id)
    mailboxes = await imap_list_mailboxes(account, password)
    imap_src = await _resolve_imap_folder(context, account_id, folder_path)

    # Check source \Drafts role (ADR 0063 §3)
    is_drafts = False
    for fi in mailboxes:
        if fi.path == imap_src and any(token.lower() == "\\drafts" for token in fi.flags.split()):
            is_drafts = True
            break
    if not is_drafts and folder_path.lower() in ("drafts", "[gmail]/drafts"):
        is_drafts = True
    if not is_drafts:
        return {
            "decision": "DENY",
            "reason": "capability_missing",
            "account": account_id,
            "folder": folder_path,
        }

    trash_wire = find_trash_folder(mailboxes)
    if trash_wire is None:
        return error_envelope(
            error_type="trash_unavailable",
            detail="no folder carries \\Trash special-use flag",
            reason=src_dec.reason,
            extra={"account": account_id, "folder": folder_path, "draft_id": draft_id},
        )
    await _ensure_folder_aliases(context, account_id)
    trash_folder = context.pdp.canonical_folder(account_id, trash_wire)

    # 3. \Trash accept_incoming
    trash_dec = context.pdp.decide_folder_access(context.caller_id, account_id, trash_folder)
    if not trash_dec.allowed:
        return {
            "decision": "DENY",
            "reason": trash_dec.reason,
            "account": account_id,
            "folder": folder_path,
        }
    assert trash_dec.folder_policy is not None
    if not trash_dec.folder_policy.accept_incoming:
        return {
            "decision": "DENY",
            "reason": "capability_missing",
            "account": account_id,
            "folder": folder_path,
        }

    imap_src = await _resolve_imap_folder(context, account_id, folder_path)
    listed = {fi.path for fi in mailboxes}
    if imap_src not in listed and imap_src.upper() != "INBOX":
        return error_envelope(
            error_type="folder_absent",
            detail="Folder does not exist on IMAP server",
            reason=src_dec.reason,
            extra={"account": account_id, "folder": folder_path, "draft_id": draft_id},
        )

    # 4. Caller provenance verification
    if context.saga is not None and not context.saga.wal.is_caller_draft_owner(
        draft_id, context.caller_id, account_id
    ):
        return error_envelope(
            error_type="not_found",
            detail="Draft not found",
            reason=src_dec.reason,
            extra={"account": account_id, "folder": folder_path, "draft_id": draft_id},
        )

    uids = await _uids_carrying_handle(account, password, imap_src, draft_id)
    if not uids:
        return error_envelope(
            error_type="not_found",
            detail="Draft not found",
            reason=src_dec.reason,
            extra={"account": account_id, "folder": folder_path, "draft_id": draft_id},
        )

    _write_audit_intent(context, "discard_draft", arguments)

    # Move to trash
    account_model = context.account_by_id(account_id)
    if account_model is not None and _is_google_provider(account_model):
        from ..imap_core import _LABEL_TO_FOLDER

        f_to_l = {v: k for k, v in _LABEL_TO_FOLDER.items()}
        src_label = f_to_l.get(folder_path, folder_path)
        dst_label = f_to_l.get(trash_folder, trash_folder)
        for u in uids:
            await imap_gmail_label_swap(account, password, imap_src, u, src_label, dst_label)
        mechanism = "gmail_label_swap"
    else:
        mechanism = await imap_move_uids(account, password, imap_src, uids, trash_wire)

    if context.saga is not None:
        context.saga.wal.discard_provenance(draft_id, account_id)

    return {
        "decision": "ALLOW",
        "result": "OK",
        "reason": src_dec.reason,
        "account": account_id,
        "folder": folder_path,
        "draft_id": draft_id,
        "discarded_count": len(uids),
        "moved_to": trash_folder,
        "mechanism": mechanism,
    }
