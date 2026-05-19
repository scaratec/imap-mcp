"""Minimal asyncio IMAP server with Gmail extensions.

Speaks enough of RFC 3501 + X-GM-EXT-1 to satisfy the imap-mcp server's
aioimaplib-based client. State lives in a shared GmailState instance
that the BDD harness seeds before each scenario.
"""

from __future__ import annotations

import asyncio
import email
import email.utils
import re
from typing import Any

from .state import FOLDER_TO_LABEL, GmailState, Message, decode_mutf7


class GmailIMAPHandler:
    """Handles one IMAP client connection."""

    def __init__(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter, state: GmailState
    ) -> None:
        self._r = reader
        self._w = writer
        self._state = state
        self._authed = False
        self._selected_folder: str | None = None
        state.total_connections += 1

    def _resolve_folder(self, folder: str) -> str:
        """Map a localized folder name back to the canonical name.

        When localization is active the IMAP client will use the
        localized name it received from LIST.  The mock's internal
        state uses canonical names, so we translate back here.
        """
        for canonical, (localized, _flags) in self._state._localized_folders.items():
            if folder == localized:
                return canonical
        return folder

    async def run(self) -> None:
        self._send_untagged("OK Gimap ready for requests (mock-gmail)")
        await self._w.drain()
        try:
            while True:
                line = await self._r.readline()
                if not line:
                    break
                text = line.decode("utf-8", errors="replace").rstrip("\r\n")
                if not text:
                    continue
                parts = text.split(None, 2)
                if len(parts) < 2:
                    continue
                tag = parts[0]
                cmd = parts[1].upper()
                args = parts[2] if len(parts) > 2 else ""
                await self._dispatch(tag, cmd, args)
                await self._w.drain()
        except (ConnectionResetError, BrokenPipeError, asyncio.IncompleteReadError):
            pass
        finally:
            self._w.close()

    async def _dispatch(self, tag: str, cmd: str, args: str) -> None:
        handler = {
            "CAPABILITY": self._cmd_capability,
            "LOGIN": self._cmd_login,
            "LIST": self._cmd_list,
            "SELECT": self._cmd_select,
            "EXAMINE": self._cmd_select,
            "CLOSE": self._cmd_close,
            "UID": self._cmd_uid,
            "NOOP": self._cmd_noop,
            "LOGOUT": self._cmd_logout,
            "APPEND": self._cmd_append,
            "EXPUNGE": self._cmd_expunge,
            "CREATE": self._cmd_create,
        }.get(cmd)
        if handler is None:
            self._send(tag, "BAD", f"Unknown command {cmd}")
            return
        await handler(tag, args)

    # ---------------------------------------------------------- commands

    async def _cmd_capability(self, tag: str, args: str) -> None:
        self._send_untagged(
            "CAPABILITY IMAP4rev1 UNSELECT IDLE NAMESPACE X-GM-EXT-1 SASL-IR AUTH=PLAIN UID MOVE"
        )
        self._send(tag, "OK", "CAPABILITY completed")

    async def _cmd_login(self, tag: str, args: str) -> None:
        self._authed = True
        self._send(tag, "OK", "LOGIN completed")

    async def _cmd_list(self, tag: str, args: str) -> None:
        folders = self._state.all_folders()
        localized = self._state._localized_folders
        for folder in folders:
            if folder in localized:
                loc_name, loc_flags = localized[folder]
                self._send_untagged(f'LIST ({loc_flags}) "/" "{loc_name}"')
            else:
                attrs = _folder_attrs(folder)
                self._send_untagged(f'LIST ({attrs}) "/" "{folder}"')
        self._send(tag, "OK", "LIST completed")

    async def _cmd_select(self, tag: str, args: str) -> None:
        folder = self._resolve_folder(_unquote(args))
        self._selected_folder = folder
        msgs = self._state.messages_in_folder(folder)
        uidval = self._state.uidvalidity(folder)
        self._send_untagged(f"{len(msgs)} EXISTS")
        self._send_untagged("0 RECENT")
        self._send_untagged(f"OK [UIDVALIDITY {uidval}] UIDs valid")
        next_uid = max((u for u, _ in msgs), default=0) + 1
        self._send_untagged(f"OK [UIDNEXT {next_uid}] Predicted next UID")
        self._send_untagged("FLAGS (\\Answered \\Flagged \\Deleted \\Seen \\Draft)")
        self._send(tag, "OK", "[READ-WRITE] SELECT completed")

    async def _cmd_close(self, tag: str, args: str) -> None:
        self._selected_folder = None
        self._send(tag, "OK", "CLOSE completed")

    async def _cmd_noop(self, tag: str, args: str) -> None:
        self._send(tag, "OK", "NOOP completed")

    async def _cmd_logout(self, tag: str, args: str) -> None:
        self._send_untagged("BYE LOGOUT Requested")
        self._send(tag, "OK", "LOGOUT completed")
        self._w.close()

    async def _cmd_create(self, tag: str, args: str) -> None:
        self._send(tag, "OK", "CREATE completed")

    async def _cmd_expunge(self, tag: str, args: str) -> None:
        folder = self._selected_folder or "INBOX"
        label = FOLDER_TO_LABEL.get(folder, folder)
        to_expunge = []
        for uid, msg in self._state.messages_in_folder(folder):
            if "\\Deleted" in msg.flags:
                to_expunge.append((uid, msg))
        for uid, msg in to_expunge:
            msg.flags.discard("\\Deleted")
            if label != "__ALL_MAIL__":
                self._state.remove_label(msg, label)
            self._send_untagged(f"{uid} EXPUNGE")
        self._send(tag, "OK", "EXPUNGE completed")

    async def _cmd_append(self, tag: str, args: str) -> None:
        m = re.match(r'"?([^"]+)"?\s+(?:\([^)]*\)\s+)?(?:"[^"]*"\s+)?\{(\d+)\}', args)
        if not m:
            self._send(tag, "BAD", "APPEND parse error")
            return
        folder = self._resolve_folder(m.group(1))
        size = int(m.group(2))
        self._send_untagged_raw("+ Ready for literal data\r\n")
        data = await self._r.readexactly(size)
        await self._r.readline()
        msg_obj = email.message_from_bytes(data)
        gm_msgid = self._state.new_msgid()
        label = FOLDER_TO_LABEL.get(folder, folder)
        labels = {label} if label != "__ALL_MAIL__" else {"\\Inbox"}
        message = Message(
            gm_msgid=gm_msgid,
            gm_thrid=gm_msgid,
            rfc822=data,
            labels=labels,
            message_id=msg_obj.get("Message-ID") or "",
            from_addr=msg_obj.get("From") or "",
            to_addr=msg_obj.get("To") or "",
            subject=msg_obj.get("Subject") or "",
            date=msg_obj.get("Date") or "",
        )
        self._state.add_message(message)
        uid = self._state.uid_for(folder, gm_msgid) or 0
        self._send(
            tag, "OK", f"[APPENDUID {self._state.uidvalidity(folder)} {uid}] APPEND completed"
        )

    async def _cmd_uid(self, tag: str, args: str) -> None:
        parts = args.split(None, 1)
        if not parts:
            self._send(tag, "BAD", "UID requires sub-command")
            return
        sub = parts[0].upper()
        sub_args = parts[1] if len(parts) > 1 else ""
        if sub == "SEARCH":
            await self._uid_search(tag, sub_args)
        elif sub == "FETCH":
            await self._uid_fetch(tag, sub_args)
        elif sub == "STORE":
            await self._uid_store(tag, sub_args)
        elif sub == "COPY":
            await self._uid_copy(tag, sub_args)
        elif sub == "MOVE":
            await self._uid_move(tag, sub_args)
        else:
            self._send(tag, "BAD", f"Unknown UID sub-command {sub}")

    async def _uid_search(self, tag: str, args: str) -> None:
        folder = self._selected_folder or "INBOX"
        msgs = self._state.messages_in_folder(folder)

        gm_msgid_match = re.search(r"X-GM-MSGID\s+(\d+)", args, re.IGNORECASE)
        if gm_msgid_match:
            target = int(gm_msgid_match.group(1))
            uids = [str(u) for u, m in msgs if m.gm_msgid == target]
            self._send_untagged(f"SEARCH {' '.join(uids)}" if uids else "SEARCH")
            self._send(tag, "OK", "UID SEARCH completed")
            return

        header_match = re.search(r'HEADER\s+"([^"]+)"\s+"([^"]+)"', args, re.IGNORECASE)
        if header_match:
            hdr_name = header_match.group(1)
            hdr_value = header_match.group(2)
            uids = []
            for u, m in msgs:
                if hdr_name.lower() == "message-id" and m.message_id and hdr_value in m.message_id:
                    uids.append(str(u))
            self._send_untagged(f"SEARCH {' '.join(uids)}" if uids else "SEARCH")
            self._send(tag, "OK", "UID SEARCH completed")
            return

        subject_match = re.search(r'SUBJECT\s+"([^"]+)"', args, re.IGNORECASE)
        from_match = re.search(r'FROM\s+"([^"]+)"', args, re.IGNORECASE)
        uid_match = re.search(r"^UID\s+(\d+)", args.strip())
        since_match = re.search(r"SINCE\s+(\d{1,2}-\w{3}-\d{4})", args, re.IGNORECASE)
        since_date = None
        if since_match:
            from datetime import datetime

            try:
                since_date = datetime.strptime(since_match.group(1), "%d-%b-%Y")
            except ValueError:
                pass

        uids = []
        for u, m in msgs:
            if uid_match:
                if u == int(uid_match.group(1)):
                    uids.append(str(u))
                continue
            match = True
            if since_date and m.date:
                from email.utils import parsedate_to_datetime

                try:
                    msg_date = parsedate_to_datetime(m.date).replace(tzinfo=None)
                    if msg_date < since_date:
                        match = False
                except (TypeError, ValueError):
                    pass
            if subject_match and subject_match.group(1).lower() not in m.subject.lower():
                match = False
            if from_match and from_match.group(1).lower() not in m.from_addr.lower():
                match = False
            if match:
                uids.append(str(u))

        self._send_untagged(f"SEARCH {' '.join(uids)}" if uids else "SEARCH")
        self._send(tag, "OK", "UID SEARCH completed")

    async def _uid_fetch(self, tag: str, args: str) -> None:
        folder = self._selected_folder or "INBOX"
        m = re.match(r"([\d,:]+)\s+\((.+)\)", args)
        if not m:
            self._send(tag, "BAD", "FETCH parse error")
            return
        uid_spec = m.group(1)
        items = m.group(2).upper()
        uids_to_fetch: list[int] = []
        for part in uid_spec.split(","):
            if ":" in part:
                lo, hi = part.split(":", 1)
                uids_to_fetch.extend(range(int(lo), int(hi) + 1))
            else:
                uids_to_fetch.append(int(part))
        for uid in uids_to_fetch:
            msg = self._state.message_by_uid(folder, uid)
            if msg is None:
                continue
            needs_literal = (
                "RFC822" in items
                or "BODY[]" in items
                or "BODY.PEEK[]" in items
                or "BODY.PEEK[HEADER]" in items
            )
            if needs_literal and msg.rfc822:
                parts = self._build_fetch_response(folder, uid, msg, items, skip_body=True)
                if "BODY.PEEK[HEADER]" in items:
                    hdr_end = msg.rfc822.find(b"\r\n\r\n")
                    header_bytes = msg.rfc822[: hdr_end + 2] if hdr_end >= 0 else msg.rfc822
                    body_key = "BODY[HEADER]"
                    literal_hdr = f"* {uid} FETCH ({parts} {body_key} {{{len(header_bytes)}}}\r\n"
                    self._w.write(literal_hdr.encode())
                    self._w.write(header_bytes)
                else:
                    body_key = "RFC822" if "RFC822" in items else "BODY[]"
                    literal_hdr = f"* {uid} FETCH ({parts} {body_key} {{{len(msg.rfc822)}}}\r\n"
                    self._w.write(literal_hdr.encode())
                    self._w.write(msg.rfc822)
                self._w.write(b")\r\n")
            else:
                parts = self._build_fetch_response(folder, uid, msg, items)
                self._send_untagged(f"{uid} FETCH ({parts})")
        self._send(tag, "OK", "UID FETCH completed")

    def _build_fetch_response(
        self, folder: str, uid: int, msg: Message, items: str, skip_body: bool = False
    ) -> str:
        parts: list[str] = []
        parts.append(f"UID {uid}")
        if "FLAGS" in items:
            flags = " ".join(sorted(msg.flags))
            parts.append(f"FLAGS ({flags})")
        if "X-GM-MSGID" in items:
            parts.append(f"X-GM-MSGID {msg.gm_msgid}")
        if "X-GM-THRID" in items:
            parts.append(f"X-GM-THRID {msg.gm_thrid}")
        if "X-GM-LABELS" in items:
            labels = self._state.labels_visible_from(folder, msg)
            label_str = " ".join(_quote_label(l) for l in labels)
            parts.append(f"X-GM-LABELS ({label_str})")
        if "ENVELOPE" in items:
            parts.append(f"ENVELOPE {_build_envelope(msg)}")
        if "RFC822.SIZE" in items:
            parts.append(f"RFC822.SIZE {len(msg.rfc822)}")
        if "BODYSTRUCTURE" in items:
            parts.append(
                'BODYSTRUCTURE ("TEXT" "PLAIN" ("CHARSET" "UTF-8") NIL NIL "7BIT" '
                + str(len(msg.rfc822))
                + " 1)"
            )
        if not skip_body:
            if "RFC822" in items:
                parts.append(
                    f"RFC822 {{{len(msg.rfc822)}}}\r\n{msg.rfc822.decode('utf-8', errors='replace')}"
                )
            if "BODY[]" in items or "BODY.PEEK[]" in items:
                parts.append(
                    f"BODY[] {{{len(msg.rfc822)}}}\r\n{msg.rfc822.decode('utf-8', errors='replace')}"
                )
        return " ".join(parts)

    async def _uid_store(self, tag: str, args: str) -> None:
        folder = self._selected_folder or "INBOX"
        m = re.match(
            r"([\d,:]+)\s+([+-]?)(FLAGS|X-GM-LABELS)(?:\.SILENT)?\s+\(([^)]*)\)",
            args,
            re.IGNORECASE,
        )
        if not m:
            self._send(tag, "BAD", "STORE parse error")
            return
        uid_spec = m.group(1)
        op = m.group(2)
        field = m.group(3).upper()
        values_raw = m.group(4)
        uids_to_store: list[int] = []
        for part in uid_spec.split(","):
            if ":" in part:
                lo, hi = part.split(":", 1)
                uids_to_store.extend(range(int(lo), int(hi) + 1))
            else:
                uids_to_store.append(int(part))
        # Check injected rejection BEFORE mutating state, so the test
        # observes "STORE never ran" rather than "half-applied".
        if field == "X-GM-LABELS" and self._state.next_store_rejection is not None:
            status, text = self._state.next_store_rejection
            self._state.next_store_rejection = None
            self._send(tag, status, text)
            return
        any_resolved = False
        for uid in uids_to_store:
            msg = self._state.message_by_uid(folder, uid)
            if msg is None:
                continue
            any_resolved = True
            if field == "X-GM-LABELS":
                labels = _parse_label_list(values_raw)
                for label in labels:
                    wire = label.replace("\\\\", "\\")
                    if self._state.record_store_operations and op in ("+", "-"):
                        self._state.store_operations.append((uid, op, wire))
                    # Real Gmail: user labels arrive Modified-UTF-7
                    # encoded; system labels (\Inbox etc.) are flags
                    # and travel literally. Decoding here lets the
                    # message end up under its UTF-8 label name so
                    # later SEARCH/SELECT on the UTF-8 form works.
                    applied = wire if wire.startswith("\\") else decode_mutf7(wire)
                    if op == "+":
                        self._state.add_label(msg, applied)
                    elif op == "-":
                        self._state.remove_label(msg, applied)
                visible = self._state.labels_visible_from(folder, msg)
                label_str = " ".join(_quote_label(l) for l in visible)
                self._send_untagged(f"{uid} FETCH (X-GM-LABELS ({label_str}) UID {uid})")
            elif field == "FLAGS":
                flags = set(values_raw.split())
                if op == "+":
                    msg.flags.update(flags)
                elif op == "-":
                    msg.flags -= flags
                else:
                    msg.flags = flags
                flags_str = " ".join(sorted(msg.flags))
                self._send_untagged(f"{uid} FETCH (FLAGS ({flags_str}) UID {uid})")
        if not any_resolved and uids_to_store:
            self._send(tag, "NO", "STORE failed: no matching UIDs")
            return
        self._send(tag, "OK", "STORE completed")

    async def _uid_copy(self, tag: str, args: str) -> None:
        folder = self._selected_folder or "INBOX"
        m = re.match(r'(\d+)\s+"?([^"]+)"?', args)
        if not m:
            self._send(tag, "BAD", "COPY parse error")
            return
        uid = int(m.group(1))
        target = self._resolve_folder(m.group(2))
        msg = self._state.message_by_uid(folder, uid)
        if msg is None:
            self._send(tag, "NO", "Message not found")
            return
        target_label = FOLDER_TO_LABEL.get(target, target)
        self._state.add_label(msg, target_label if target_label != "__ALL_MAIL__" else target)
        self._send(tag, "OK", "COPY completed")

    async def _uid_move(self, tag: str, args: str) -> None:
        folder = self._selected_folder or "INBOX"
        m = re.match(r'(\d+)\s+"?([^"]+)"?', args)
        if not m:
            self._send(tag, "BAD", "MOVE parse error")
            return
        uid = int(m.group(1))
        target = self._resolve_folder(m.group(2))
        msg = self._state.message_by_uid(folder, uid)
        if msg is None:
            self._send(tag, "NO", "Message not found")
            return
        src_label = FOLDER_TO_LABEL.get(folder, folder)
        target_label = FOLDER_TO_LABEL.get(target, target)
        if src_label != "__ALL_MAIL__":
            self._state.remove_label(msg, src_label)
        if target_label != "__ALL_MAIL__":
            self._state.add_label(msg, target_label)
        self._send_untagged(f"{uid} EXPUNGE")
        self._send(tag, "OK", "MOVE completed")

    # ---------------------------------------------------------- helpers

    def _send(self, tag: str, status: str, text: str) -> None:
        self._w.write(f"{tag} {status} {text}\r\n".encode())

    def _send_untagged(self, text: str) -> None:
        self._w.write(f"* {text}\r\n".encode())

    def _send_untagged_raw(self, text: str) -> None:
        self._w.write(text.encode())


def _folder_attrs(folder: str) -> str:
    attrs_map: dict[str, str] = {
        "INBOX": "\\HasNoChildren",
        "[Gmail]": "\\HasChildren \\Noselect",
        "[Gmail]/All Mail": "\\All \\HasNoChildren",
        "[Gmail]/Drafts": "\\Drafts \\HasNoChildren",
        "[Gmail]/Important": "\\HasNoChildren \\Important",
        "[Gmail]/Sent Mail": "\\HasNoChildren \\Sent",
        "[Gmail]/Spam": "\\HasNoChildren \\Junk",
        "[Gmail]/Starred": "\\Flagged \\HasNoChildren",
        "[Gmail]/Trash": "\\HasNoChildren \\Trash",
    }
    return attrs_map.get(folder, "\\HasNoChildren")


def _unquote(s: str) -> str:
    s = s.strip()
    if s.startswith('"') and s.endswith('"'):
        return s[1:-1]
    return s


def _quote_label(label: str) -> str:
    if label.startswith("\\"):
        return f'"{label}"'
    if " " in label or '"' in label:
        return f'"{label}"'
    return label


def _parse_label_list(raw: str) -> list[str]:
    labels = []
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


def _build_envelope(msg: Message) -> str:
    from_parts = _addr_parts(msg.from_addr)
    to_parts = _addr_parts(msg.to_addr)
    subj = msg.subject.replace('"', '\\"')
    date = msg.date.replace('"', '\\"')
    mid = (msg.message_id or "").replace('"', '\\"')
    return (
        f'("{date}" "{subj}" '
        f"(({from_parts})) (({from_parts})) (({from_parts})) "
        f"(({to_parts})) NIL NIL NIL "
        f'"{mid}")'
    )


def _addr_parts(addr: str) -> str:
    if "<" in addr:
        name_part = addr.split("<")[0].strip().strip('"')
        email_part = addr.split("<")[1].rstrip(">").strip()
    else:
        name_part = ""
        email_part = addr.strip()
    if "@" in email_part:
        local, domain = email_part.rsplit("@", 1)
    else:
        local, domain = email_part, ""
    name_part = name_part.replace('"', '\\"') if name_part else "NIL"
    if name_part != "NIL":
        name_part = f'"{name_part}"'
    return f'{name_part} NIL "{local}" "{domain}"'


async def start_gmail_mock(
    state: GmailState, host: str = "127.0.0.1", port: int = 0
) -> tuple[asyncio.Server, int]:
    async def _handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        handler = GmailIMAPHandler(reader, writer, state)
        await handler.run()

    server = await asyncio.start_server(_handle, host, port)
    actual_port = server.sockets[0].getsockname()[1]
    return server, actual_port
