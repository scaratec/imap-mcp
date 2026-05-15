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

import re

from dataclasses import dataclass

from aioimaplib import IMAP4, IMAP4_SSL

from .config import Account


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

    Override via `IMAP_MCP_APPEND_TIMEOUT` for scenarios that inject an
    APPEND delay and then assert the server exits the call with a
    timeout rather than blocking forever.
    """
    import os

    raw = os.environ.get("IMAP_MCP_APPEND_TIMEOUT")
    if raw:
        try:
            return int(raw)
        except ValueError:
            pass
    return 60


async def _open_imap(account: Account, *, timeout: int = 10) -> IMAP4:
    from .tracing import tracer

    with tracer.start_as_current_span(
        "imap.connect",
        attributes={"imap.host": account.host, "imap.port": account.port},
    ):
        import asyncio as _asyncio

        if account.port == 993:
            imap = IMAP4_SSL(host=account.host, port=account.port, timeout=timeout)
        else:
            try:
                _r, _w = await _asyncio.wait_for(
                    _asyncio.open_connection(account.host, account.port),
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
            entries.append((name_raw.decode("utf-8"), flags_raw))

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
    from datetime import timezone
    from email.utils import getaddresses, parsedate_to_datetime

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
            subject=re.sub(r"\r?\n\s+", " ", message.get("Subject", "") or ""),
            message_id=message.get("Message-ID"),
            date=date_iso,
            size_bytes=size_bytes,
            has_attachment=has_attachment,
            flags=flags,
        )
    finally:
        await imap.logout()


async def fetch_envelopes_batch(
    account: Account, password: str, folder: str, uids: list[int]
) -> list[Envelope]:
    """Fetch ENVELOPE fields for multiple UIDs in a single IMAP session."""
    import email
    from datetime import timezone
    from email.utils import getaddresses, parsedate_to_datetime

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
                    subject=re.sub(r"\r?\n\s+", " ", message.get("Subject", "") or ""),
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


async def fetch_body(
    account: Account, password: str, folder: str, uid: int
) -> tuple[Envelope, str] | None:
    """Fetch headers + plain-text body for one UID. Returns None if absent."""
    import email

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
        else:
            payload = message.get_payload(decode=True)
            if isinstance(payload, bytes):
                body_text = payload.decode(message.get_content_charset("utf-8"), errors="replace")
        # Strip trailing CRLF / whitespace that IMAP servers add during
        # APPEND; the caller expects the body as authored.
        body_text = body_text.rstrip("\r\n")
        envelope = Envelope(
            uid=uid,
            from_address=from_addrs[0][1] if from_addrs else "",
            to_addresses=[addr for _, addr in to_addrs if addr],
            subject=re.sub(r"\r?\n\s+", " ", message.get("Subject", "") or ""),
            message_id=message.get("Message-ID"),
            date=message.get("Date"),
            flags=flags,
        )
        return envelope, body_text
    finally:
        await imap.logout()


async def folder_stats(
    account: Account, password: str, folder: str
) -> tuple[int, list[int]] | None:
    """Return (exists, uid_list) for a folder."""
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


async def append_message(
    account: Account,
    password: str,
    folder: str,
    rfc822: bytes,
) -> bool:
    import asyncio
    from datetime import datetime, timezone

    timeout = _append_timeout()
    imap = await _open_imap(account, timeout=timeout)
    await _authenticate_imap(imap, account, password)
    try:
        status, _ = await asyncio.wait_for(
            imap.append(
                rfc822,
                mailbox=folder,
                date=datetime.now(tz=timezone.utc),
            ),
            timeout=timeout,
        )
        return status == "OK"
    finally:
        try:
            await imap.logout()
        except Exception:
            pass


async def search_uids(
    account: Account, password: str, folder: str, criteria: str = "ALL"
) -> list[int]:
    """Execute a SEARCH in `folder` and return the matching UIDs."""
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


async def gmail_label_swap(
    account: Account,
    password: str,
    uid: int,
    remove_label: str,
    add_label: str,
) -> None:
    """STORE -X-GM-LABELS (remove) then +X-GM-LABELS (add).

    Both operations are performed on the same connection with the
    source folder selected. The message stays in [Gmail]/All Mail;
    only its label set changes — which is the Gmail-native way to
    "move" between virtual folders.
    """
    # Select the folder that corresponds to the label being removed so
    # that the UID is valid in the selected mailbox.
    folder = _label_to_folder(remove_label)
    imap = await _open_imap(account)
    await _authenticate_imap(imap, account, password)
    try:
        status, _ = await imap.select(folder)
        if status != "OK":
            raise RuntimeError(f"cannot SELECT {folder!r}")
        # Remove the source label
        status, _ = await imap.uid(
            "store", str(uid), "-X-GM-LABELS", f"({_quote_gmail_label(remove_label)})"
        )
        if status != "OK":
            raise RuntimeError(f"STORE -X-GM-LABELS failed: {status}")
        # Add the target label
        status, _ = await imap.uid(
            "store", str(uid), "+X-GM-LABELS", f"({_quote_gmail_label(add_label)})"
        )
        if status != "OK":
            raise RuntimeError(f"STORE +X-GM-LABELS failed: {status}")
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
