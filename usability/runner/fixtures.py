"""Synthetic mailbox fixtures generated from the usability feature files.

The Background tables are the whole mailbox: folders, localized names,
messages, attachments and owner drafts. This module turns a
`ScenarioSpec` into RFC 5322 messages with fixed boundaries, Message-IDs
and dates, seeds a fresh mock-gmail `GmailState` with them, and takes
snapshots of the mailbox for grading. Same spec in, same bytes out.
"""

from __future__ import annotations

import base64
import email
import email.policy
import hashlib
import sys
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from email.message import EmailMessage
from email.utils import format_datetime
from typing import Any

from . import REPO_ROOT
from .features import ScenarioSpec
from .pdf import invoice_lines, render_pdf

_MOCK_SRC = REPO_ROOT / "bdd" / "mock-gmail" / "src"
if str(_MOCK_SRC) not in sys.path:
    sys.path.insert(0, str(_MOCK_SRC))

from mock_gmail.state import GmailState, Message, decode_mutf7

OWNER_ADDRESS = "inhaber@example.org"
OWNER_NAME = "Kim Brandt"
MESSAGE_ID_DOMAIN = "fixtures.usability.example.org"

# Canonical Gmail system folder → (label inside mock-gmail, RFC 6154 flags).
SYSTEM_FOLDERS: dict[str, tuple[str | None, str]] = {
    "INBOX": ("\\Inbox", ""),
    "[Gmail]/Drafts": ("\\Drafts", "\\Drafts \\HasNoChildren"),
    "[Gmail]/Sent Mail": ("\\Sent", "\\Sent \\HasNoChildren"),
    "[Gmail]/Trash": ("\\Trash", "\\Trash \\HasNoChildren"),
    "[Gmail]/Spam": ("\\Spam", "\\Junk \\HasNoChildren"),
    "[Gmail]/Starred": ("\\Starred", "\\Flagged \\HasNoChildren"),
    "[Gmail]/Important": ("\\Important", "\\Important \\HasNoChildren"),
    "[Gmail]/All Mail": (None, "\\All \\HasNoChildren"),
}

_MONTHS_DE = (
    "Januar", "Februar", "März", "April", "Mai", "Juni",
    "Juli", "August", "September", "Oktober", "November", "Dezember",
)


def encode_mutf7(text: str) -> str:
    """UTF-8 → RFC 3501 §5.1.3 modified UTF-7 (the wire form of mailbox names)."""
    out: list[str] = []
    buffer: list[str] = []

    def flush() -> None:
        if buffer:
            raw = "".join(buffer).encode("utf-16-be")
            out.append("&" + base64.b64encode(raw).decode("ascii").rstrip("=").replace("/", ",") + "-")
            buffer.clear()

    for ch in text:
        if 0x20 <= ord(ch) <= 0x7E:
            flush()
            out.append("&-" if ch == "&" else ch)
        else:
            buffer.append(ch)
    flush()
    return "".join(out)


@dataclass
class SeedMessage:
    ref: int | None
    folder: str
    labels: set[str]
    flags: set[str]
    message_id: str
    from_addr: str
    to_addr: str
    subject: str
    date_header: str
    rfc822: bytes
    attachment_name: str | None = None
    attachment_bytes: bytes | None = None
    owner_draft: bool = False


@dataclass
class MailboxFixture:
    account: str
    localized: list[tuple[str, str, str]]  # (canonical, wire name, flags)
    custom_folders: list[str]  # UTF-8 display names
    messages: list[SeedMessage] = field(default_factory=list)

    def attachments(self) -> dict[str, bytes]:
        return {
            m.attachment_name: m.attachment_bytes
            for m in self.messages
            if m.attachment_name and m.attachment_bytes is not None
        }

    def by_ref(self) -> dict[int, SeedMessage]:
        return {m.ref: m for m in self.messages if m.ref is not None}


def _parse_day(value: str) -> datetime:
    if "T" in value:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)
    d = date.fromisoformat(value)
    return datetime(d.year, d.month, d.day, 8, 0, 0, tzinfo=UTC)


def _label_for(folder: str) -> str | None:
    if folder in SYSTEM_FOLDERS:
        return SYSTEM_FOLDERS[folder][0]
    return encode_mutf7(folder)


def _display_name(address: str) -> str:
    local, _, domain = address.partition("@")
    if address == OWNER_ADDRESS:
        return OWNER_NAME
    parts = [p for p in local.replace("_", ".").split(".") if p]
    if len(parts) >= 2:
        return " ".join(p.capitalize() for p in parts)
    return domain.split(".")[0].replace("-", " ").title()


def _body(subject: str, sender: str, attachment: str | None) -> str:
    lines = ["Guten Tag,", "", f"{subject}."]
    if attachment:
        lines += ["", f"Die Unterlagen finden Sie im Anhang ({attachment})."]
    lines += ["", "Viele Grüße", _display_name(sender), ""]
    return "\n".join(lines)


def _build_rfc822(
    *,
    message_id: str,
    from_addr: str,
    to_addr: str,
    subject: str,
    when: datetime,
    body: str,
    attachment: tuple[str, bytes] | None,
    boundary: str,
) -> bytes:
    msg = EmailMessage(policy=email.policy.SMTP)
    msg["From"] = f"{_display_name(from_addr)} <{from_addr}>"
    msg["To"] = f"{_display_name(to_addr)} <{to_addr}>"
    msg["Subject"] = subject
    msg["Date"] = format_datetime(when)
    msg["Message-ID"] = message_id
    msg["MIME-Version"] = "1.0"
    msg.set_content(body, charset="utf-8", cte="quoted-printable")
    if attachment is not None:
        name, data = attachment
        msg.add_attachment(data, maintype="application", subtype="pdf", filename=name)
        msg.set_boundary(boundary)
    return msg.as_bytes()


def build_mailbox(spec: ScenarioSpec) -> MailboxFixture:
    localized = []
    for loc in spec.localized:
        if loc.canonical not in SYSTEM_FOLDERS:
            raise ValueError(f"{spec.id}: {loc.canonical!r} is not a Gmail system folder")
        localized.append((loc.canonical, encode_mutf7(loc.localized), SYSTEM_FOLDERS[loc.canonical][1]))

    custom: list[str] = []
    fixture = MailboxFixture(account=spec.account, localized=localized, custom_folders=custom)
    for row in spec.messages:
        if row.folder not in SYSTEM_FOLDERS and row.folder not in custom:
            custom.append(row.folder)
        sender = row.sender or OWNER_ADDRESS
        recipient = row.recipient or OWNER_ADDRESS
        when = _parse_day(row.date)
        message_id = f"<u{row.ref}.{spec.account}@{MESSAGE_ID_DOMAIN}>"
        attachment: tuple[str, bytes] | None = None
        if row.attachment:
            number = row.attachment.rsplit(".", 1)[0]
            pdf = render_pdf(
                invoice_lines(
                    number=number,
                    issuer=sender.partition("@")[2],
                    issued=when.date(),
                    amount_eur=row.amount_eur,
                    recipient=f"{OWNER_NAME}, {OWNER_ADDRESS}",
                ),
                title=f"Rechnung {number}",
                creation=when.date(),
            )
            attachment = (row.attachment, pdf)
        label = _label_for(row.folder)
        flags = {"\\Seen"} if row.folder == "[Gmail]/Sent Mail" else set()
        fixture.messages.append(
            SeedMessage(
                ref=row.ref,
                folder=row.folder,
                labels={label} if label else set(),
                flags=flags,
                message_id=message_id,
                from_addr=sender,
                to_addr=recipient,
                subject=row.subject,
                date_header=format_datetime(when),
                rfc822=_build_rfc822(
                    message_id=message_id,
                    from_addr=sender,
                    to_addr=recipient,
                    subject=row.subject,
                    when=when,
                    body=_body(row.subject, sender, row.attachment),
                    attachment=attachment,
                    boundary=f"=_usability_{row.ref}",
                ),
                attachment_name=row.attachment,
                attachment_bytes=attachment[1] if attachment else None,
            )
        )
    for index, draft in enumerate(spec.owner_drafts, start=1):
        when = _parse_day(draft.date)
        message_id = f"<owner-draft-{index}.{spec.account}@{MESSAGE_ID_DOMAIN}>"
        body = f"Hallo {_display_name(draft.to).split()[0]},\n\nkurze Info zu: {draft.subject}.\n\nViele Grüße\n{OWNER_NAME}\n"
        label = _label_for(draft.folder)
        fixture.messages.append(
            SeedMessage(
                ref=None,
                folder=draft.folder,
                labels={label} if label else set(),
                flags={"\\Draft", "\\Seen"},
                message_id=message_id,
                from_addr=OWNER_ADDRESS,
                to_addr=draft.to,
                subject=draft.subject,
                date_header=format_datetime(when),
                rfc822=_build_rfc822(
                    message_id=message_id,
                    from_addr=OWNER_ADDRESS,
                    to_addr=draft.to,
                    subject=draft.subject,
                    when=when,
                    body=body,
                    attachment=None,
                    boundary="",
                ),
                owner_draft=True,
            )
        )
    return fixture


def _internal_folder(folder: str) -> str:
    """Folder name the mock state uses for UID bookkeeping."""
    if folder in SYSTEM_FOLDERS:
        return folder
    return encode_mutf7(folder)


def seed_state(state: GmailState, fixture: MailboxFixture) -> None:
    """Seed a fresh mock-gmail state. UIDs follow the table's `uid` column."""
    state.reset()
    state.set_localized_folders(fixture.localized)
    for folder in fixture.custom_folders:
        state.create_folder(encode_mutf7(folder))
    for index, seed in enumerate(fixture.messages, start=1):
        if seed.ref is not None:
            internal = _internal_folder(seed.folder)
            counters = state._uid_counters
            if counters.get(internal, 0) < seed.ref:
                counters[internal] = seed.ref - 1
        msgid_number = 1_900_000_000_000_000_000 + index
        state.add_message(
            Message(
                gm_msgid=msgid_number,
                gm_thrid=msgid_number,
                rfc822=seed.rfc822,
                labels=set(seed.labels),
                flags=set(seed.flags),
                message_id=seed.message_id,
                from_addr=seed.from_addr,
                to_addr=seed.to_addr,
                subject=seed.subject,
                date=seed.date_header,
            )
        )


# ------------------------------------------------------------- snapshots


def mime_structure(part: email.message.Message) -> str:
    if part.is_multipart():
        payload = part.get_payload()
        assert isinstance(payload, list)
        children = ",".join(mime_structure(p) for p in payload if isinstance(p, email.message.Message))
        return f"{part.get_content_type()}[{children}]"
    return part.get_content_type()


def _decoded_text(msg: email.message.Message, subtype: str) -> str | None:
    for part in msg.walk():
        if part.is_multipart():
            continue
        if part.get_content_type() == f"text/{subtype}" and not part.get_filename():
            payload = part.get_payload(decode=True)
            if isinstance(payload, bytes):
                charset = part.get_content_charset() or "utf-8"
                return payload.decode(charset, errors="replace")
    return None


def snapshot(state: GmailState) -> list[dict[str, Any]]:
    """Every message with the folders it is visible in, MIME shape and bodies."""
    visible: dict[int, list[str]] = {}
    for folder in state.all_folders():
        for _uid, msg in state.messages_in_folder(folder):
            visible.setdefault(msg.gm_msgid, []).append(decode_mutf7(folder))
    out: list[dict[str, Any]] = []
    for order, msg in enumerate(state.messages):
        parsed = email.message_from_bytes(msg.rfc822, policy=email.policy.default)
        attachments = []
        for part in parsed.walk():
            filename = part.get_filename()
            if filename:
                payload = part.get_payload(decode=True)
                data = payload if isinstance(payload, bytes) else b""
                attachments.append(
                    {"filename": filename, "sha256": hashlib.sha256(data).hexdigest(), "size": len(data)}
                )
        uids = {}
        for folder in visible.get(msg.gm_msgid, []):
            uid = state.uid_for(encode_mutf7(folder) if folder not in SYSTEM_FOLDERS else folder, msg.gm_msgid)
            if uid is not None:
                uids[folder] = uid
        out.append(
            {
                "order": order,
                "gm_msgid": msg.gm_msgid,
                "message_id": (msg.message_id or "").strip(),
                "folders": sorted(visible.get(msg.gm_msgid, [])),
                "uids": uids,
                "labels": sorted(decode_mutf7(label) for label in msg.labels),
                "flags": sorted(msg.flags),
                "from": str(parsed.get("From", "")),
                "to": str(parsed.get("To", "")),
                "subject": str(parsed.get("Subject", "")),
                "date": str(parsed.get("Date", "")),
                "mime": mime_structure(parsed),
                "has_html": _decoded_text(parsed, "html") is not None,
                "text_plain": _decoded_text(parsed, "plain"),
                "text_html": _decoded_text(parsed, "html"),
                "attachments": attachments,
                "sha256": hashlib.sha256(msg.rfc822).hexdigest(),
                "is_draft": "\\Drafts" in msg.labels or "\\Draft" in msg.flags,
            }
        )
    return out


def month_name_de(month: int) -> str:
    return _MONTHS_DE[month - 1]
