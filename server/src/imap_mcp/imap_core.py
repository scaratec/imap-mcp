"""Minimal IMAP client — Walking-Skeleton slice (ADR 0012, 0013).

Built on `aioimaplib`. Opens a single async connection per call for
now; the hybrid connection pool from ADR 0013 will wrap these calls
when the scenarios that exercise pool semantics activate.

Only the operations the current tool set needs are exposed:
- `list_folders(account)` returns the full folder path list as the
  IMAP server reports it, unfiltered by policy.
The PDP (in `policy.py`) is responsible for reducing that list.

XOAUTH2 and Gmail extensions are deferred; only plain-auth password
logins are supported at this stage.
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Literal

from aioimaplib import IMAP4, IMAP4_SSL

from .config import Account

# --------------------------------------------------------------- mUTF-7
# RFC 3501 §5.1.3 — Modified UTF-7 codec for IMAP mailbox names.
# The MCP surface uses UTF-8 throughout; encoding/decoding happens
# only at the IMAP wire boundary inside this module.


def decode_mutf7(wire: str) -> str:
    """Decode an IMAP Modified UTF-7 mailbox name to UTF-8.

    Printable ASCII passes through unchanged.  ``&`` starts a
    base64-encoded UTF-16BE run terminated by ``-``.  The literal
    ampersand is encoded as ``&-``.

    Returns the input unchanged on malformed sequences so that callers
    always get a usable folder path (degraded, not crashed).
    """
    import base64

    out: list[str] = []
    i = 0
    n = len(wire)
    while i < n:
        if wire[i] != "&":
            out.append(wire[i])
            i += 1
            continue
        end = wire.find("-", i + 1)
        if end < 0:
            return wire
        if end == i + 1:
            out.append("&")
            i = end + 1
            continue
        b64 = wire[i + 1 : end].replace(",", "/")
        pad = (4 - len(b64) % 4) % 4
        try:
            raw = base64.b64decode(b64 + "=" * pad)
            out.append(raw.decode("utf-16-be"))
        except Exception:
            return wire
        i = end + 1
    return "".join(out)


def encode_mutf7(utf8: str) -> str:
    """Encode a UTF-8 mailbox name to IMAP Modified UTF-7.

    Printable ASCII (0x20–0x7E) passes through.  Everything else is
    grouped into runs and encoded as base64'd UTF-16BE between ``&``
    and ``-``.  A literal ``&`` becomes ``&-``.
    """
    import base64

    out: list[str] = []
    buf: list[str] = []

    def flush() -> None:
        if not buf:
            return
        raw = "".join(buf).encode("utf-16-be")
        b64 = base64.b64encode(raw).decode("ascii").rstrip("=").replace("/", ",")
        out.append("&" + b64 + "-")
        buf.clear()

    for ch in utf8:
        cp = ord(ch)
        if 0x20 <= cp <= 0x7E:
            flush()
            if ch == "&":
                out.append("&-")
            else:
                out.append(ch)
        else:
            buf.append(ch)
    flush()
    return "".join(out)


@dataclass(frozen=True)
class Envelope:
    """Subset of message metadata needed for policy + ENVELOPE-level tools."""

    uid: int
    from_address: str
    to_addresses: list[str]
    subject: str
    message_id: str | None
    date: str | None
    size_bytes: int = 0
    has_attachment: bool = False
    flags: tuple[str, ...] = ()


# RFC 3501 LIST response format:  * LIST (flags) "sep" "name"
# aioimaplib returns the untagged responses as bytes strings with the
# `* LIST ` prefix already stripped: `(flags) "sep" "name"`.
_LIST_LINE = re.compile(rb'\s*\((?P<flags>[^)]*)\)\s+"(?P<sep>[^"]*)"\s+(?P<name>.+?)\s*$')


def _append_timeout() -> int:
    """Timeout (seconds) for IMAP4 connections that perform APPEND.

    The BDD harness can override via ``TestHooks.append_timeout_override``
    (env: ``IMAP_MCP_APPEND_TIMEOUT``) when injecting an APPEND delay
    so the server returns a timeout instead of blocking forever.
    """
    from .test_hooks import get_global_hooks

    override = get_global_hooks().append_timeout_override
    return override if override is not None else 60


async def _open_imap(account: Account, *, timeout: int = 10) -> IMAP4:
    from .tracing import tracer

    with tracer.start_as_current_span(
        "imap.connect",
        attributes={"imap.host": account.host, "imap.port": account.port},
    ):
        if account.port == 993:
            imap = IMAP4_SSL(host=account.host, port=account.port, timeout=timeout)
        else:
            try:
                _r, _w = await asyncio.wait_for(
                    asyncio.open_connection(account.host, account.port),
                    timeout=timeout,
                )
                _w.close()
                try:
                    await _w.wait_closed()
                except Exception:
                    pass
            except ConnectionRefusedError:
                raise
            except OSError as exc:
                raise ConnectionRefusedError(*exc.args) from exc
            imap = IMAP4(host=account.host, port=account.port, timeout=timeout)
        await imap.wait_hello_from_server()
        return imap


async def _authenticate_imap(imap: IMAP4, account: Account, password: str) -> None:
    from .tracing import tracer

    auth_type = account.auth.type if account.auth else "password"
    with tracer.start_as_current_span(
        "imap.authenticate", attributes={"imap.auth_type": auth_type}
    ):
        user = _imap_user_for(account)
        if account.auth and account.auth.type == "xoauth2":
            try:
                await imap.xoauth2(user, password)
            except Exception as e:
                raise RuntimeError(f"IMAP AUTHENTICATE failed: {e}")
        else:
            await imap.login(user, password)


@dataclass(frozen=True)
class FolderInfo:
    """Folder path with message count from IMAP STATUS."""

    path: str
    message_count: int
    flags: str = ""


async def list_folders(account: Account, password: str) -> list[FolderInfo]:
    """Connect, authenticate, LIST + STATUS per folder, logout."""
    imap = await _open_imap(account)
    await _authenticate_imap(imap, account, password)

    try:
        status, response = await imap.list('""', "*")
        if status != "OK":
            raise RuntimeError(f"IMAP LIST failed: {status} {response!r}")
        entries: list[tuple[str, str]] = []
        for raw in response:
            if isinstance(raw, bytes) is False:
                continue
            line = raw.strip()
            if not line or line.endswith(b"LIST completed") or line == b"Done":
                continue
            match = _LIST_LINE.search(line)
            if not match:
                continue
            flags_raw = match.group("flags").decode("utf-8", errors="replace")
            if "\\Noselect" in flags_raw:
                continue
            name_raw = match.group("name").strip()
            if name_raw.startswith(b'"') and name_raw.endswith(b'"'):
                name_raw = name_raw[1:-1]
            entries.append((decode_mutf7(name_raw.decode("utf-8")), flags_raw))

        result: list[FolderInfo] = []
        for folder_path, flags in entries:
            count = await _folder_message_count(imap, folder_path)
            result.append(FolderInfo(path=folder_path, message_count=count, flags=flags))
        return result
    finally:
        await imap.logout()


_SPECIAL_USE_TO_CANONICAL: dict[str, str] = {
    "\\Drafts": "[Gmail]/Drafts",
    "\\Sent": "[Gmail]/Sent Mail",
    "\\Trash": "[Gmail]/Trash",
    "\\Junk": "[Gmail]/Spam",
    "\\Flagged": "[Gmail]/Starred",
    "\\Important": "[Gmail]/Important",
    "\\All": "[Gmail]/All Mail",
}


def build_folder_alias_map(folders: list[FolderInfo]) -> dict[str, str]:
    """Build a mapping from canonical policy paths to actual IMAP paths.

    Scans the RFC 6154 special-use flags from the IMAP LIST response.
    When a folder carries a special-use flag but its path differs from
    the canonical English name, a mapping entry is created so that
    policy paths can be resolved to the actual IMAP path.

    Returns: {canonical_path: actual_imap_path}
    """
    alias_map: dict[str, str] = {}
    for fi in folders:
        for token in fi.flags.split():
            canonical = _SPECIAL_USE_TO_CANONICAL.get(token)
            if canonical is not None and fi.path != canonical:
                alias_map[canonical] = fi.path
    return alias_map


_STATUS_MESSAGES = re.compile(rb"MESSAGES\s+(\d+)")


async def _folder_message_count(imap: IMAP4, folder: str) -> int:
    """Issue IMAP STATUS for a folder and return its MESSAGES count."""
    folder = encode_mutf7(folder)
    quoted = f'"{folder}"'
    status, response = await imap.status(quoted, "(MESSAGES)")
    if status != "OK":
        return 0
    for part in response:
        if isinstance(part, bytes):
            m = _STATUS_MESSAGES.search(part)
            if m:
                return int(m.group(1))
    return 0


def _imap_user_for(account: Account) -> str:
    """Derive the IMAP username from an Account.

    If the account has an explicit ``user`` field, use it directly.
    Otherwise fall back to the walking-skeleton convention: the id
    is ``<imap-user>-<tenant>`` and the user is the segment before
    the first dash, or the whole id if no dash is present.
    """
    if account.user is not None:
        return account.user
    return account.id.split("-", 1)[0]


async def fetch_envelope(account: Account, password: str, folder: str, uid: int) -> Envelope | None:
    """Fetch ENVELOPE fields of a single UID. Returns None if absent."""
    import email
    from email.utils import getaddresses, parsedate_to_datetime

    folder = encode_mutf7(folder)
    imap = await _open_imap(account)
    await _authenticate_imap(imap, account, password)
    try:
        status, _ = await imap.select(folder)
        if status != "OK":
            return None
        status, response = await imap.uid(
            "fetch", str(uid), "(FLAGS BODY.PEEK[HEADER] RFC822.SIZE BODYSTRUCTURE)"
        )
        if status != "OK":
            return None
        raw_header = _extract_literal(response)
        if raw_header is None:
            return None
        size_bytes, has_attachment = _extract_meta(response)
        flags = _extract_flags(response)
        message = email.message_from_bytes(raw_header)
        from_addrs = getaddresses(message.get_all("From", []))
        to_addrs = getaddresses(message.get_all("To", []) + message.get_all("Cc", []))
        date_iso: str | None = None
        date_header = message.get("Date")
        if date_header:
            try:
                parsed = parsedate_to_datetime(date_header)
                if parsed.tzinfo is None:
                    parsed = parsed.replace(tzinfo=timezone.utc)
                date_iso = parsed.isoformat().replace("+00:00", "Z")
            except (TypeError, ValueError):
                date_iso = None
        return Envelope(
            uid=uid,
            from_address=from_addrs[0][1] if from_addrs else "",
            to_addresses=[addr for _, addr in to_addrs if addr],
            subject=_decode_header(message.get("Subject", "") or ""),
            message_id=message.get("Message-ID"),
            date=date_iso,
            size_bytes=size_bytes,
            has_attachment=has_attachment,
            flags=flags,
        )
    finally:
        await imap.logout()


def _decode_header(raw: str) -> str:
    """RFC 2047-decode a header value and unfold any header continuations."""
    from email.header import decode_header, make_header

    try:
        decoded = str(make_header(decode_header(raw)))
    except Exception:
        decoded = raw
    return re.sub(r"\r?\n\s+", " ", decoded)


async def fetch_envelopes_batch(
    account: Account, password: str, folder: str, uids: list[int]
) -> list[Envelope]:
    """Fetch ENVELOPE fields for multiple UIDs in a single IMAP session."""
    import email
    from email.utils import getaddresses, parsedate_to_datetime

    folder = encode_mutf7(folder)
    if not uids:
        return []
    imap = await _open_imap(account)
    await _authenticate_imap(imap, account, password)
    results: list[Envelope] = []
    try:
        status, _ = await imap.select(folder)
        if status != "OK":
            return []
        uid_set = ",".join(str(u) for u in uids)
        status, response = await imap.uid(
            "fetch", uid_set, "(FLAGS BODY.PEEK[HEADER] RFC822.SIZE BODYSTRUCTURE)"
        )
        if status != "OK":
            return []
        i = 0
        while i < len(response):
            raw_header = _extract_literal_at(response, i)
            if raw_header is None:
                i += 1
                continue
            uid_val = _extract_uid(response, i)
            size_bytes, has_attachment = _extract_meta_at(response, i)
            flags = _extract_flags_at(response, i)
            message = email.message_from_bytes(raw_header)
            from_addrs = getaddresses(message.get_all("From", []))
            to_addrs = getaddresses(message.get_all("To", []) + message.get_all("Cc", []))
            date_iso: str | None = None
            date_header = message.get("Date")
            if date_header:
                try:
                    parsed = parsedate_to_datetime(date_header)
                    if parsed.tzinfo is None:
                        parsed = parsed.replace(tzinfo=timezone.utc)
                    date_iso = parsed.isoformat().replace("+00:00", "Z")
                except (TypeError, ValueError):
                    pass
            results.append(
                Envelope(
                    uid=uid_val or 0,
                    from_address=from_addrs[0][1] if from_addrs else "",
                    to_addresses=[addr for _, addr in to_addrs if addr],
                    subject=_decode_header(message.get("Subject", "") or ""),
                    message_id=message.get("Message-ID"),
                    date=date_iso,
                    size_bytes=size_bytes,
                    has_attachment=has_attachment,
                    flags=flags,
                )
            )
            i += 2
    finally:
        await imap.logout()
    return results


def _extract_uid(response: list[bytes | bytearray], start: int) -> int | None:
    """Extract UID from a FETCH response frame."""
    import re

    for j in range(start, min(start + 2, len(response))):
        part = response[j]
        if isinstance(part, (bytes, bytearray)):
            m = re.search(rb"UID\s+(\d+)", part)
            if m:
                return int(m.group(1))
    return None


def _extract_literal_at(response: list[bytes | bytearray], start: int) -> bytes | None:
    """Pick the header literal starting at position `start`."""
    for j in range(start, min(start + 2, len(response))):
        part = response[j]
        if isinstance(part, (bytes, bytearray)) and (
            b"From:" in part or b"Date:" in part or b"Subject:" in part
        ):
            return bytes(part)
    return None


def _extract_meta_at(response: list[bytes | bytearray], start: int) -> tuple[int, bool]:
    """Pull RFC822.SIZE and attachment hint from a frame at `start`."""
    import re

    size_bytes = 0
    has_attachment = False
    for j in range(start, min(start + 3, len(response))):
        part = response[j]
        if not isinstance(part, (bytes, bytearray)):
            continue
        m = re.search(rb"RFC822\.SIZE\s+(\d+)", part)
        if m:
            size_bytes = int(m.group(1))
        if re.search(rb'(?i)"(?:attachment|inline)"', part):
            has_attachment = True
    return size_bytes, has_attachment


def _extract_meta(response: list[bytes | bytearray]) -> tuple[int, bool]:
    """Pull RFC822.SIZE and a has-attachment hint out of a FETCH frame.

    aioimaplib returns the non-literal portion of the FETCH response
    as a bytes frame that carries the key=value pairs. We parse it
    loosely with regex because building a full IMAP response parser
    here would be out of scope.
    """
    import re

    size_bytes = 0
    has_attachment = False
    for item in response:
        if not isinstance(item, (bytes, bytearray)):
            continue
        text = bytes(item)
        size_match = re.search(rb"RFC822\.SIZE\s+(\d+)", text)
        if size_match:
            size_bytes = int(size_match.group(1))
        lower = text.lower()
        if b"multipart/mixed" in lower or b'"attachment"' in lower or b'"inline"' in lower:
            has_attachment = True
    return size_bytes, has_attachment


_FLAGS_RE = re.compile(rb"FLAGS\s+\(([^)]*)\)")


def _extract_flags(response: list[bytes | bytearray]) -> tuple[str, ...]:
    """Pull FLAGS from a FETCH response frame."""
    for item in response:
        if not isinstance(item, (bytes, bytearray)):
            continue
        m = _FLAGS_RE.search(bytes(item))
        if m:
            raw = m.group(1).decode("utf-8", errors="replace").strip()
            if not raw:
                return ()
            return tuple(raw.split())
    return ()


def _extract_flags_at(response: list[bytes | bytearray], start: int) -> tuple[str, ...]:
    """Pull FLAGS from a FETCH response frame at position `start`."""
    for j in range(start, min(start + 3, len(response))):
        part = response[j]
        if not isinstance(part, (bytes, bytearray)):
            continue
        m = _FLAGS_RE.search(bytes(part))
        if m:
            raw = m.group(1).decode("utf-8", errors="replace").strip()
            if not raw:
                return ()
            return tuple(raw.split())
    return ()


async def fetch_full_message(
    account: Account, password: str, folder: str, uid: int
) -> "bytes | None":
    """Return the raw RFC822 bytes for a UID."""
    folder = encode_mutf7(folder)
    imap = await _open_imap(account)
    await _authenticate_imap(imap, account, password)
    try:
        status, _ = await imap.select(folder)
        if status != "OK":
            return None
        status, response = await imap.uid("fetch", str(uid), "(RFC822)")
        if status != "OK":
            return None
        return _extract_literal(response)
    finally:
        await imap.logout()


def mime_add_attachment(rfc822: bytes, filename: str, mime_type: str, content: bytes) -> bytes:
    import email
    from email.message import EmailMessage

    import email.policy as _ep

    msg = email.message_from_bytes(rfc822, policy=_ep.default)
    maintype, _, subtype = mime_type.partition("/")
    if not msg.is_multipart():
        original_body = msg.get_body(preferencelist=("plain", "html"))
        wrapper = EmailMessage()
        for key, value in msg.items():
            if key.lower() not in ("content-type", "content-transfer-encoding", "mime-version"):
                wrapper[key] = value
        wrapper["MIME-Version"] = "1.0"
        if original_body is not None:
            ct = original_body.get_content_type()
            body_maintype, _, body_subtype = ct.partition("/")
            body_bytes = original_body.get_content()
            if isinstance(body_bytes, str):
                wrapper.set_content(body_bytes, subtype=body_subtype)
            else:
                wrapper.set_content(body_bytes, maintype=body_maintype, subtype=body_subtype)
        msg = wrapper
    msg.add_attachment(content, maintype=maintype, subtype=subtype, filename=filename)
    return msg.as_bytes()


def mime_replace_attachment(
    rfc822: bytes,
    filename: str,
    new_content: bytes,
    new_mime_type: str | None = None,
    new_filename: str | None = None,
) -> bytes:
    import email

    import email.policy as _ep

    msg = email.message_from_bytes(rfc822, policy=_ep.default)
    if not msg.is_multipart():
        raise ValueError(f"Message is not multipart; cannot replace attachment {filename!r}")
    found = False
    parts = list(msg.iter_parts())
    for i, part in enumerate(parts):
        part_fn = part.get_filename()
        if part_fn == filename:
            target_mime = new_mime_type or part.get_content_type()
            maintype, _, subtype = target_mime.partition("/")
            target_fn = new_filename or filename
            part.set_content(
                new_content,
                maintype=maintype,
                subtype=subtype,
                filename=target_fn,
                disposition="attachment",
            )
            found = True
            break
    if not found:
        raise ValueError(f"Attachment {filename!r} not found in message")
    return msg.as_bytes()


def mime_delete_attachment(rfc822: bytes, filename: str) -> bytes:
    import email
    from email.message import EmailMessage

    import email.policy as _ep

    msg = email.message_from_bytes(rfc822, policy=_ep.default)
    if not msg.is_multipart():
        raise ValueError(f"Message is not multipart; cannot delete attachment {filename!r}")
    found = False
    keep_parts = []
    for part in msg.iter_parts():
        part_fn = part.get_filename()
        if part_fn == filename and not found:
            found = True
            continue
        keep_parts.append(part)
    if not found:
        raise ValueError(f"Attachment {filename!r} not found in message")
    new_msg = EmailMessage()
    for key, value in msg.items():
        if key.lower() not in ("content-type", "content-transfer-encoding", "mime-version"):
            new_msg[key] = value
    new_msg["MIME-Version"] = "1.0"
    if len(keep_parts) == 1 and keep_parts[0].get_content_disposition() != "attachment":
        body_part = keep_parts[0]
        ct = body_part.get_content_type()
        maintype, _, subtype = ct.partition("/")
        body_content = body_part.get_content()
        if isinstance(body_content, str):
            new_msg.set_content(body_content, subtype=subtype)
        else:
            new_msg.set_content(body_content, maintype=maintype, subtype=subtype)
    else:
        new_msg.set_content("", subtype="plain")
        for part in keep_parts:
            if part.get_content_disposition() in ("attachment", "inline"):
                ct = part.get_content_type()
                maintype, _, subtype = ct.partition("/")
                payload = part.get_payload(decode=True) or b""
                fn = part.get_filename() or "attachment"
                new_msg.add_attachment(
                    payload,
                    maintype=maintype,
                    subtype=subtype,
                    filename=fn,
                )
            elif part == keep_parts[0]:
                ct = part.get_content_type()
                _, _, subtype = ct.partition("/")
                body_content = part.get_content()
                if isinstance(body_content, str):
                    new_msg.set_content(body_content, subtype=subtype)
    return new_msg.as_bytes()


def _strip_html(html: str) -> str:
    from html import unescape
    from html.parser import HTMLParser

    class _Stripper(HTMLParser):
        def __init__(self) -> None:
            super().__init__()
            self._parts: list[str] = []

        def handle_data(self, data: str) -> None:
            self._parts.append(data)

    stripper = _Stripper()
    stripper.feed(unescape(html))
    text = "".join(stripper._parts)
    lines = [line.strip() for line in text.splitlines()]
    return "\n".join(line for line in lines if line)


async def fetch_body(
    account: Account, password: str, folder: str, uid: int
) -> "tuple[Envelope, str, object] | None":
    """Fetch headers + plain-text body for one UID. Returns None if absent.

    The third element is the parsed ``email.message.Message`` so that
    callers can walk the MIME tree for attachment metadata without a
    second IMAP round-trip.
    """
    import email

    folder = encode_mutf7(folder)
    imap = await _open_imap(account)
    await _authenticate_imap(imap, account, password)
    try:
        status, _ = await imap.select(folder)
        if status != "OK":
            return None
        status, response = await imap.uid("fetch", str(uid), "(FLAGS RFC822)")
        if status != "OK":
            return None
        raw = _extract_literal(response)
        if raw is None:
            return None
        flags = _extract_flags(response)
        message = email.message_from_bytes(raw)
        from email.utils import getaddresses

        from_addrs = getaddresses(message.get_all("From", []))
        to_addrs = getaddresses(message.get_all("To", []) + message.get_all("Cc", []))
        body_text = ""
        if message.is_multipart():
            for part in message.walk():
                if part.get_content_type() == "text/plain":
                    body_text = part.get_payload(decode=True).decode(
                        part.get_content_charset("utf-8"), errors="replace"
                    )
                    break
            if not body_text:
                for part in message.walk():
                    if part.get_content_type() == "text/html":
                        raw_html = part.get_payload(decode=True).decode(
                            part.get_content_charset("utf-8"), errors="replace"
                        )
                        body_text = _strip_html(raw_html)
                        break
        else:
            payload = message.get_payload(decode=True)
            if isinstance(payload, bytes):
                raw = payload.decode(message.get_content_charset("utf-8"), errors="replace")
                if message.get_content_type() == "text/html":
                    body_text = _strip_html(raw)
                else:
                    body_text = raw
        body_text = body_text.rstrip("\r\n")
        envelope = Envelope(
            uid=uid,
            from_address=from_addrs[0][1] if from_addrs else "",
            to_addresses=[addr for _, addr in to_addrs if addr],
            subject=_decode_header(message.get("Subject", "") or ""),
            message_id=message.get("Message-ID"),
            date=message.get("Date"),
            flags=flags,
        )
        return envelope, body_text, message
    finally:
        await imap.logout()


async def fetch_message_for_reply(account: Account, password: str, folder: str, uid: int):
    """Fetch the parsed email.Message and the extracted plain-text body.

    Same I/O path as `fetch_body` but returns the full Message object so
    callers can read raw headers (Reply-To, Cc separately, References,
    From with display-name, original Date string) that the slim
    `Envelope` does not expose. Used by `create_reply_draft`.

    Returns ``None`` when the UID is absent.
    """
    import email
    from email.message import Message

    folder = encode_mutf7(folder)
    imap = await _open_imap(account)
    await _authenticate_imap(imap, account, password)
    try:
        status, _ = await imap.select(folder)
        if status != "OK":
            return None
        status, response = await imap.uid("fetch", str(uid), "(FLAGS RFC822)")
        if status != "OK":
            return None
        raw = _extract_literal(response)
        if raw is None:
            return None
        message: Message = email.message_from_bytes(raw)
        body_text = ""
        if message.is_multipart():
            for part in message.walk():
                if part.get_content_type() == "text/plain":
                    body_text = part.get_payload(decode=True).decode(
                        part.get_content_charset("utf-8"), errors="replace"
                    )
                    break
            if not body_text:
                for part in message.walk():
                    if part.get_content_type() == "text/html":
                        raw_html = part.get_payload(decode=True).decode(
                            part.get_content_charset("utf-8"), errors="replace"
                        )
                        body_text = _strip_html(raw_html)
                        break
        else:
            payload = message.get_payload(decode=True)
            if isinstance(payload, bytes):
                decoded = payload.decode(message.get_content_charset("utf-8"), errors="replace")
                if message.get_content_type() == "text/html":
                    body_text = _strip_html(decoded)
                else:
                    body_text = decoded
        body_text = body_text.replace("\r\n", "\n").rstrip("\n")
        return message, body_text
    finally:
        await imap.logout()


async def folder_stats(
    account: Account, password: str, folder: str
) -> tuple[int, list[int]] | None:
    """Return (exists, uid_list) for a folder."""
    folder = encode_mutf7(folder)
    imap = await _open_imap(account)
    await _authenticate_imap(imap, account, password)
    try:
        status, data = await imap.select(folder)
        if status != "OK":
            return None
        status, response = await imap.uid_search("ALL")
        if status != "OK":
            return None
        if not response or not response[0]:
            return 0, []
        raw = response[0] if isinstance(response[0], (bytes, bytearray)) else b""
        uids = [int(tok) for tok in bytes(raw).split()] if raw else []
        return len(uids), uids
    finally:
        await imap.logout()


async def store_flag(
    account: Account,
    password: str,
    folder: str,
    uid: int,
    flag: str,
    *,
    add: bool,
) -> bool:
    folder = encode_mutf7(folder)
    imap = await _open_imap(account)
    await _authenticate_imap(imap, account, password)
    try:
        status, _ = await imap.select(folder)
        if status != "OK":
            return False
        op = "+FLAGS" if add else "-FLAGS"
        status, _ = await imap.uid("store", str(uid), op, f"({flag})")
        return status == "OK"
    finally:
        await imap.logout()


async def store_flags_batch(
    account: Account,
    password: str,
    folder: str,
    uids: list[int],
    flag: str,
    *,
    add: bool,
) -> int:
    """STORE flag on multiple UIDs in one IMAP session."""
    folder = encode_mutf7(folder)
    if not uids:
        return 0
    imap = await _open_imap(account)
    await _authenticate_imap(imap, account, password)
    try:
        status, _ = await imap.select(folder)
        if status != "OK":
            return 0
        op = "+FLAGS" if add else "-FLAGS"
        uid_str = ",".join(str(u) for u in uids)
        status, _ = await imap.uid("store", uid_str, op, f"({flag})")
        return len(uids) if status == "OK" else 0
    finally:
        await imap.logout()


async def store_keywords(
    account: Account,
    password: str,
    folder: str,
    uid: int,
    keywords: list[str],
    *,
    mode: str,
) -> bool:
    folder = encode_mutf7(folder)
    imap = await _open_imap(account)
    await _authenticate_imap(imap, account, password)
    try:
        status, _ = await imap.select(folder)
        if status != "OK":
            return False
        op_map = {"add": "+FLAGS", "remove": "-FLAGS", "replace": "FLAGS"}
        op = op_map[mode]
        joined = " ".join(keywords)
        status, _ = await imap.uid("store", str(uid), op, f"({joined})")
        return status == "OK"
    finally:
        await imap.logout()


class TargetFolderMissing(RuntimeError):
    """The target folder does not exist on the account."""


class UidNotFound(RuntimeError):
    """The source UID is not present in the selected folder."""


class UidStale(RuntimeError):
    """UIDVALIDITY changed between SELECT and the next mutating
    command. A new MOVE/COPY against the original UID would target a
    different message; the saga must re-resolve before retrying."""


class LabelMutationFailed(RuntimeError):
    """A Gmail X-GM-LABELS STORE was rejected by the server (NO/BAD).

    Carries the stage ("add" or "remove"), the label that was being
    mutated, the IMAP status, and the raw response text so the handler
    can surface a precise ``provider_rejected`` error instead of
    masking it as ``uid_not_found``.
    """

    def __init__(self, stage: str, label: str, status: str, response_text: str) -> None:
        super().__init__(f"X-GM-LABELS {stage} {label!r} failed: {status} {response_text}")
        self.stage = stage
        self.label = label
        self.status = status
        self.response_text = response_text


def _extract_uidvalidity(lines: "list[bytes | bytearray] | None") -> int | None:
    """Pull `UIDVALIDITY <n>` from the untagged response lines of a
    SELECT or NOOP. Returns None if absent."""
    if not lines:
        return None
    pattern = re.compile(rb"\[UIDVALIDITY\s+(\d+)\]")
    for raw in lines:
        if not isinstance(raw, (bytes, bytearray)):
            continue
        m = pattern.search(bytes(raw))
        if m:
            try:
                return int(m.group(1))
            except ValueError:
                continue
    return None


async def move_message(
    account: Account,
    password: str,
    folder: str,
    uid: int,
    target_folder: str,
) -> str:
    """Intra-account move via RFC 6851 MOVE. Returns 'native_move' or 'copy_store_expunge'."""
    folder = encode_mutf7(folder)
    target_folder = encode_mutf7(target_folder)
    imap = await _open_imap(account)
    await _authenticate_imap(imap, account, password)
    try:
        # Confirm target exists before attempting the move; otherwise
        # distinguishing uid_not_found from target_folder_missing after
        # the fact would require parsing IMAP NO-response text.
        status, _ = await imap.select(target_folder)
        if status != "OK":
            raise TargetFolderMissing(target_folder)
        status, lines = await imap.select(folder)
        if status != "OK":
            raise RuntimeError(f"cannot SELECT source {folder!r}")
        uidvalidity_before = _extract_uidvalidity(lines)
        # Confirm the UID actually exists before MOVE — again for
        # error-type attribution.
        status, response = await imap.uid_search(f"UID {uid}")
        if status != "OK":
            raise RuntimeError(f"SEARCH failed: {status}")
        raw = response[0] if response and isinstance(response[0], (bytes, bytearray)) else b""
        hits = bytes(raw).split() if raw else []
        if not hits:
            raise UidNotFound(uid)
        # NOOP between SEARCH and MOVE so the server has a chance to
        # surface a UIDVALIDITY change as an untagged response. ADR
        # 0006 §uidvalidity_consistency.
        try:
            _, noop_lines = await imap.noop()
        except Exception:
            noop_lines = None
        uidvalidity_after = _extract_uidvalidity(noop_lines)
        if (
            uidvalidity_before is not None
            and uidvalidity_after is not None
            and uidvalidity_before != uidvalidity_after
        ):
            raise UidStale(uid)
        # Honour the IMAP CAPABILITY advertisement: only try native
        # MOVE when the server announced it. Otherwise go straight to
        # COPY + STORE + EXPUNGE so the wire-level command sequence
        # matches the server's stated abilities (RFC 6851 §3).
        has_move = bool(imap.has_capability("MOVE"))
        if has_move:
            try:
                status, _ = await imap.uid("move", str(uid), target_folder)
                if status == "OK":
                    return "native_move"
            except Exception:
                pass
        status, _ = await imap.uid("copy", str(uid), target_folder)
        if status != "OK":
            raise RuntimeError(f"COPY failed: {status}")
        await imap.uid("store", str(uid), "+FLAGS", r"(\Deleted)")
        await imap.expunge()
        return "copy_store_expunge"
    finally:
        await imap.logout()


async def copy_message(
    account: Account,
    password: str,
    folder: str,
    uid: int,
    target_folder: str,
) -> bool:
    folder = encode_mutf7(folder)
    target_folder = encode_mutf7(target_folder)
    imap = await _open_imap(account)
    await _authenticate_imap(imap, account, password)
    try:
        status, _ = await imap.select(folder)
        if status != "OK":
            return False
        status, _ = await imap.uid("copy", str(uid), target_folder)
        return status == "OK"
    finally:
        await imap.logout()


@dataclass(frozen=True)
class AppendResult:
    """Outcome of an APPEND that completed with a tagged server response.

    `outcome` is "ok" when the server accepted the APPEND, "rejected"
    when the server returned a tagged NO or BAD. `imap_response` carries
    the verbatim reason text the server sent after the NO/BAD token, or
    None for "ok". Failures that yield no tagged response (timeout,
    connection lost, library errors) propagate as exceptions and are
    NOT represented here — the caller decides how to classify them."""

    outcome: Literal["ok", "rejected"]
    imap_response: str | None


def _connection_lost(imap: IMAP4) -> bool:
    """True when the IMAP connection's transport has been closed.

    aioimaplib's `connection_lost` callback does not fail pending
    futures, so a server-side connection drop while we wait for a
    tagged response surfaces to us as an `asyncio.TimeoutError`
    rather than a connection error. The transport's `is_closing()`
    flag is the only signal we have to tell the two apart; this
    helper hides the two-level attribute walk into aioimaplib's
    internals so callers do not reach across object boundaries.
    """
    transport = getattr(imap.protocol, "transport", None)
    return transport is None or transport.is_closing()


async def append_message(
    account: Account,
    password: str,
    folder: str,
    rfc822: bytes,
    flags: tuple[str, ...] = (),
) -> AppendResult:
    folder = encode_mutf7(folder)
    timeout = _append_timeout()
    imap = await _open_imap(account, timeout=timeout)
    await _authenticate_imap(imap, account, password)
    try:
        flags_str = " ".join(flags) if flags else None
        try:
            response = await asyncio.wait_for(
                imap.append(
                    rfc822,
                    mailbox=folder,
                    flags=flags_str,
                    date=datetime.now(tz=timezone.utc),
                ),
                timeout=timeout,
            )
        except asyncio.TimeoutError:
            if _connection_lost(imap):
                raise ConnectionResetError("IMAP connection lost while waiting for APPEND response")
            raise
        if response.result == "OK":
            return AppendResult(outcome="ok", imap_response=None)
        reason: str | None = None
        if response.lines:
            last = response.lines[-1]
            if isinstance(last, (bytes, bytearray)):
                reason = bytes(last).decode("utf-8", errors="replace")
            else:
                reason = str(last)
            reason = reason.rstrip("\r\n")
        return AppendResult(outcome="rejected", imap_response=reason)
    finally:
        try:
            await imap.logout()
        except Exception:
            pass


async def fetch_raw_with_flags(
    account: Account, password: str, folder: str, uid: int
) -> tuple[bytes, tuple[str, ...]] | None:
    """Fetch RFC822 bytes and FLAGS for a UID. Returns None if absent."""
    folder = encode_mutf7(folder)
    imap = await _open_imap(account)
    await _authenticate_imap(imap, account, password)
    try:
        status, _ = await imap.select(folder)
        if status != "OK":
            return None
        status, response = await imap.uid("fetch", str(uid), "(FLAGS RFC822)")
        if status != "OK":
            return None
        raw = _extract_literal(response)
        if raw is None:
            return None
        flags = _extract_flags(response)
        return raw, flags
    finally:
        await imap.logout()


async def search_uids(
    account: Account, password: str, folder: str, criteria: str = "ALL"
) -> list[int]:
    """Execute a SEARCH in `folder` and return the matching UIDs."""
    folder = encode_mutf7(folder)
    imap = await _open_imap(account)
    await _authenticate_imap(imap, account, password)
    try:
        status, _ = await imap.select(folder)
        if status != "OK":
            return []
        status, response = await imap.uid_search(criteria)
        if status != "OK":
            return []
        if not response:
            return []
        raw = response[0] if isinstance(response[0], bytes) else b""
        if not raw:
            return []
        return [int(token) for token in raw.split()]
    finally:
        await imap.logout()


def _extract_literal(response: list[bytes | bytearray]) -> bytes | None:
    """Pick the header/body literal out of an aioimaplib FETCH response.

    aioimaplib returns protocol framing as `bytes` and literal content
    (headers, body bytes) as `bytearray`. Pick the bytearray directly —
    it is unambiguous, regardless of how long BODYSTRUCTURE or other
    metadata make the surrounding bytes frames.
    """
    for item in response:
        if isinstance(item, bytearray):
            data = bytes(item)
            if data.strip():
                return data
    return None


def _first_header_index(data: bytes) -> int | None:
    import re

    match = re.search(rb"^[A-Za-z][A-Za-z0-9-]*:", data, flags=re.MULTILINE)
    return match.start() if match else 0


# ---------------------------------------------------------- Gmail extensions


async def gmail_search_by_msgid(
    account: Account, password: str, folder: str, gm_msgid: int
) -> list[int]:
    """SEARCH X-GM-MSGID <id> in the given folder."""
    folder = encode_mutf7(folder)
    imap = await _open_imap(account)
    await _authenticate_imap(imap, account, password)
    try:
        status, _ = await imap.select(folder)
        if status != "OK":
            return []
        status, response = await imap.uid_search(f"X-GM-MSGID {gm_msgid}")
        if status != "OK":
            return []
        if not response:
            return []
        raw = response[0] if isinstance(response[0], bytes) else b""
        if not raw:
            return []
        return [int(token) for token in raw.split()]
    finally:
        await imap.logout()


async def gmail_fetch_labels(account: Account, password: str, folder: str, uid: int) -> list[str]:
    """FETCH (X-GM-LABELS) for a single UID."""
    folder = encode_mutf7(folder)
    imap = await _open_imap(account)
    await _authenticate_imap(imap, account, password)
    try:
        status, _ = await imap.select(folder)
        if status != "OK":
            return []
        status, response = await imap.uid("fetch", str(uid), "(X-GM-LABELS)")
        if status != "OK":
            return []
        # Parse X-GM-LABELS (label1 label2 "label with spaces") from response
        for item in response:
            if not isinstance(item, (bytes, bytearray)):
                continue
            text = bytes(item).decode("utf-8", errors="replace")
            m = re.search(r"X-GM-LABELS\s+\(([^)]*)\)", text)
            if m:
                return _parse_gmail_label_list(m.group(1))
        return []
    finally:
        await imap.logout()


async def gmail_fetch_msgid(account: Account, password: str, folder: str, uid: int) -> int | None:
    """FETCH (X-GM-MSGID) for a single UID."""
    folder = encode_mutf7(folder)
    imap = await _open_imap(account)
    await _authenticate_imap(imap, account, password)
    try:
        status, _ = await imap.select(folder)
        if status != "OK":
            return None
        status, response = await imap.uid("fetch", str(uid), "(X-GM-MSGID)")
        if status != "OK":
            return None
        for item in response:
            if not isinstance(item, (bytes, bytearray)):
                continue
            text = bytes(item).decode("utf-8", errors="replace")
            m = re.search(r"X-GM-MSGID\s+(\d+)", text)
            if m:
                return int(m.group(1))
        return None
    finally:
        await imap.logout()


def _response_text(lines: "list[bytes | bytearray] | None") -> str:
    if not lines:
        return ""
    parts: list[str] = []
    for raw in lines:
        if isinstance(raw, (bytes, bytearray)):
            parts.append(bytes(raw).decode("utf-8", errors="replace"))
        else:
            parts.append(str(raw))
    return " ".join(parts).strip()


async def gmail_label_swap(
    account: Account,
    password: str,
    uid: int,
    remove_label: str,
    add_label: str,
) -> None:
    """ADD the target label, then REMOVE the source label.

    Gmail folders are label projections: removing the source label
    first detaches the UID from the selected mailbox, so the next
    STORE on the same UID is rejected as "no matching UIDs". ADD must
    therefore run before REMOVE while the source label is still
    keeping the UID visible in the selected folder.
    """
    folder = encode_mutf7(_label_to_folder(remove_label))
    wire_add = _encode_gmail_label(add_label)
    wire_remove = _encode_gmail_label(remove_label)
    imap = await _open_imap(account)
    await _authenticate_imap(imap, account, password)
    try:
        status, _ = await imap.select(folder)
        if status != "OK":
            raise RuntimeError(f"cannot SELECT {folder!r}")
        status, lines = await imap.uid(
            "store", str(uid), "+X-GM-LABELS", f"({_quote_gmail_label(wire_add)})"
        )
        if status != "OK":
            raise LabelMutationFailed("add", add_label, status, _response_text(lines))
        status, lines = await imap.uid(
            "store", str(uid), "-X-GM-LABELS", f"({_quote_gmail_label(wire_remove)})"
        )
        if status != "OK":
            raise LabelMutationFailed("remove", remove_label, status, _response_text(lines))
    finally:
        await imap.logout()


async def gmail_list_labels(account: Account, password: str) -> list[dict]:
    """LIST all folders and return label info dicts.

    Each dict contains: ``name`` (folder path), ``flags`` (raw IMAP
    flags string), ``separator`` (hierarchy delimiter).
    """
    imap = await _open_imap(account)
    await _authenticate_imap(imap, account, password)
    try:
        status, response = await imap.list('""', "*")
        if status != "OK":
            raise RuntimeError(f"IMAP LIST failed: {status} {response!r}")
        labels: list[dict] = []
        for raw in response:
            if isinstance(raw, bytes) is False:
                continue
            line = raw.strip()
            if not line or line.endswith(b"LIST completed") or line == b"Done":
                continue
            match = _LIST_LINE.search(line)
            if not match:
                continue
            flags_raw = match.group("flags").decode("utf-8", errors="replace")
            sep_raw = match.group("sep").decode("utf-8", errors="replace")
            name_raw = match.group("name").strip()
            if name_raw.startswith(b'"') and name_raw.endswith(b'"'):
                name_raw = name_raw[1:-1]
            name = name_raw.decode("utf-8")
            labels.append(
                {
                    "name": name,
                    "flags": flags_raw,
                    "separator": sep_raw,
                }
            )
        return labels
    finally:
        await imap.logout()


def _parse_gmail_label_list(raw: str) -> list[str]:
    """Parse a parenthesised X-GM-LABELS value into a Python list."""
    labels: list[str] = []
    i = 0
    while i < len(raw):
        if raw[i] == '"':
            j = raw.index('"', i + 1)
            labels.append(raw[i + 1 : j])
            i = j + 1
        elif raw[i] == " ":
            i += 1
        else:
            j = raw.find(" ", i)
            if j == -1:
                j = len(raw)
            labels.append(raw[i:j])
            i = j
    return labels


def _quote_gmail_label(label: str) -> str:
    """Quote a label for use in an IMAP STORE X-GM-LABELS command."""
    if label.startswith("\\"):
        return f'"{label}"'
    if " " in label or '"' in label:
        return f'"{label}"'
    return label


def _encode_gmail_label(label: str) -> str:
    """Modified-UTF-7 encode a Gmail user label for the wire.

    Gmail's X-GM-EXT-1 places user labels on the IMAP wire in the
    same encoding as mailbox names (RFC 3501 §5.1.3). System labels
    starting with backslash are IMAP flags, not mailbox names, and
    travel literally — encoding them would corrupt the flag syntax.
    """
    if label.startswith("\\"):
        return label
    return encode_mutf7(label)


# Maps Gmail system labels back to IMAP folder paths.
_LABEL_TO_FOLDER: dict[str, str] = {
    "\\Inbox": "INBOX",
    "\\Sent": "[Gmail]/Sent Mail",
    "\\Drafts": "[Gmail]/Drafts",
    "\\Trash": "[Gmail]/Trash",
    "\\Spam": "[Gmail]/Spam",
    "\\Starred": "[Gmail]/Starred",
    "\\Important": "[Gmail]/Important",
}


def _label_to_folder(label: str) -> str:
    """Map a Gmail label to the corresponding IMAP folder name."""
    return _LABEL_TO_FOLDER.get(label, label)
