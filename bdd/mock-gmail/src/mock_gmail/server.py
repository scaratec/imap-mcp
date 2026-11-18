"""Minimal asyncio IMAP server with Gmail extensions.

Speaks enough of RFC 3501 + X-GM-EXT-1 to satisfy the imap-mcp server's
aioimaplib-based client. State lives in a shared GmailState instance
that the BDD harness seeds before each scenario.
"""

from __future__ import annotations

import asyncio
import binascii
import email
import email.utils
import re
from datetime import UTC, datetime

from .mime import bodystructure
from .search import SearchSyntaxError, search
from .state import (
    FOLDER_TO_LABEL,
    HIDDEN_LABELS,
    CommandRecord,
    GmailState,
    LabelStoreRecord,
    Message,
    decode_mutf7,
)

# Commands whose first argument is a mailbox name (RFC 3501 §6.3).
_MAILBOX_COMMANDS = frozenset({
    "SELECT", "EXAMINE", "STATUS", "APPEND", "CREATE", "DELETE",
    "RENAME", "SUBSCRIBE", "UNSUBSCRIBE",
})

# CAPABILITY before and after authentication, as Gmail answers them
# (trimmed to what the mock implements).
_CAPS_NOT_AUTHENTICATED = "IMAP4rev1 AUTH=XOAUTH2"
_CAPS_AUTHENTICATED = "IMAP4rev1 AUTH=XOAUTH2 MOVE UIDPLUS X-GM-EXT-1"

# RFC 3501 §9 atom-specials, minus resp-specials (`]` is allowed in an
# astring) and minus `\` (handled separately for flags).
_ATOM_SPECIALS = frozenset('(){ %*"')


def _parse_uid_set(spec: str, max_val: int = 1_000_000_000) -> list[int]:
    """Parse IMAP sequence set such as '1,2' or '1:3' into a list of ints."""
    uids: set[int] = set()
    for part in spec.split(","):
        if ":" in part:
            lo_s, hi_s = part.split(":", 1)
            lo = max_val if lo_s == "*" else int(lo_s)
            hi = max_val if hi_s == "*" else int(hi_s)
            for x in range(min(lo, hi), max(lo, hi) + 1):
                uids.add(x)
        else:
            uids.add(max_val if part == "*" else int(part))
    return sorted(uids)


class GmailIMAPHandler:
    """Handles one IMAP client connection."""

    def __init__(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter, state: GmailState
    ) -> None:
        self._r = reader
        self._w = writer
        self._state = state
        self._authed = False
        self._read_only = False
        self._user: str | None = None
        self._selected_folder: str | None = None
        self.epoch = state.epoch
        state.connection_opened(self)

    def close(self) -> None:
        """Drop the connection from the server side."""
        self._w.close()

    def _resolve_folder(self, folder: str) -> str | None:
        """Map a wire mailbox name to the mock's internal folder name.

        When localization is active the IMAP client must use the
        localized name it received from LIST; the mock's internal
        state uses canonical names, so we translate back here. The
        canonical name of a localized folder resolves to None: real
        Gmail does not know that mailbox (ADR 0033).
        """
        return self._state.mailbox_for_wire_name(folder)

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
            self._state.connection_closed(self)
            self._w.close()

    async def _dispatch(self, tag: str, cmd: str, args: str) -> None:
        self._record(cmd, args)
        if not self._authed and cmd not in ("CAPABILITY", "NOOP", "LOGOUT", "LOGIN", "AUTHENTICATE"):
            self._send(tag, "BAD", "[CLIENTBUG] Command requires authenticated state")
            return

        if (
            cmd in ("SEARCH", "FETCH", "STORE", "COPY", "MOVE", "EXPUNGE", "CLOSE")
            or cmd == "UID"
        ) and self._selected_folder is None:
            self._send(tag, "BAD", "[CLIENTBUG] Command requires selected state")
            return

        if self._read_only and cmd in ("STORE", "EXPUNGE"):
            self._send(tag, "NO", "[READ-ONLY] Mailbox is read-only")
            return

        handler = {
            "CAPABILITY": self._cmd_capability,
            "LOGIN": self._cmd_login,
            "AUTHENTICATE": self._cmd_authenticate,
            "LIST": self._cmd_list,
            "SELECT": self._cmd_select,
            "EXAMINE": self._cmd_examine,
            "CLOSE": self._cmd_close,
            "UID": self._cmd_uid,
            "NOOP": self._cmd_noop,
            "LOGOUT": self._cmd_logout,
            "APPEND": self._cmd_append,
            "EXPUNGE": self._cmd_expunge,
            "CREATE": self._cmd_create,
            "STATUS": self._cmd_status,
        }.get(cmd)
        if handler is None:
            self._send(tag, "BAD", f"Unknown command {cmd}")
            return
        await handler(tag, args)

    # ---------------------------------------------------------- commands

    async def _cmd_capability(self, tag: str, args: str) -> None:
        # Real Gmail advertises MOVE and UIDPLUS only after
        # authentication; the greeting-time list lacks them.
        caps = _CAPS_AUTHENTICATED if self._authed else _CAPS_NOT_AUTHENTICATED
        self._send_untagged(f"CAPABILITY {caps}")
        self._send(tag, "OK", "CAPABILITY completed")

    async def _cmd_login(self, tag: str, args: str) -> None:
        parts = args.split(None, 1)
        if len(parts) < 2:
            self._send(tag, "BAD", "LOGIN requires username and password")
            return
        user = _unquote(parts[0])
        password = _unquote(parts[1])
        if password != self._state.password:
            self._send(tag, "NO", "[AUTHENTICATIONFAILED] Invalid credentials")
            return
        self._authed = True
        self._user = user
        self._send(tag, "OK", "LOGIN completed")

    async def _cmd_authenticate(self, tag: str, args: str) -> None:
        import base64

        parts = args.split(None, 1)
        mech = parts[0].upper() if parts else ""
        if mech != "XOAUTH2":
            self._send(tag, "NO", f"[AUTHENTICATIONFAILED] Unsupported mechanism {mech}")
            return

        if len(parts) > 1:
            blob_b64 = parts[1]
        else:
            self._send_untagged_raw("+ \r\n")
            await self._w.drain()
            line = await self._r.readline()
            blob_b64 = line.decode("utf-8", errors="replace").rstrip("\r\n")

        try:
            raw = base64.b64decode(blob_b64)
        except (ValueError, binascii.Error):
            self._send(tag, "BAD", "Invalid base64 encoding")
            return

        user = None
        token = None
        for chunk in raw.split(b"\x01"):
            if chunk.startswith(b"user="):
                user = chunk[5:].decode("utf-8", errors="replace")
            elif chunk.startswith(b"auth=Bearer "):
                token = chunk[12:].decode("utf-8", errors="replace")

        if not user or not token:
            self._send(tag, "NO", "[AUTHENTICATIONFAILED] Invalid credentials")
            return

        # Check reactive revocation test hook
        if self._state._revoke_current_token_once:
            self._state._revoke_current_token_once = False
            self._state.revoked_tokens.add(token)
            self._send(tag, "NO", "[AUTHENTICATIONFAILED] Invalid credentials")
            return

        if token in self._state.revoked_tokens:
            self._send(tag, "NO", "[AUTHENTICATIONFAILED] Invalid credentials")
            return

        if self._state.token_validator is not None:
            try:
                if not self._state.token_validator(token):
                    self._send(tag, "NO", "[AUTHENTICATIONFAILED] Invalid credentials")
                    return
            except (ValueError, TypeError, KeyError, AttributeError):
                self._send(tag, "NO", "[AUTHENTICATIONFAILED] Invalid credentials")
                return

        self._authed = True
        self._user = user
        self._send(tag, "OK", "Success")

    async def _cmd_list(self, tag: str, args: str) -> None:
        parts = args.split(None, 1)
        ref = _unquote(parts[0]) if parts else ""
        pat = _unquote(parts[1]) if len(parts) > 1 else "*"
        if ref == "" and pat == "":
            self._send_untagged('LIST (\\Noselect) "/" ""')
            self._send(tag, "OK", "LIST completed")
            return

        def _matches(name: str) -> bool:
            if pat in ("*", "%"):
                return True
            regex_str = "^" + re.escape(pat).replace(r"\*", ".*").replace(r"\%", "[^/]*") + "$"
            return bool(re.match(regex_str, name))

        folders = self._state.all_folders()
        localized = self._state._localized_folders
        for folder in folders:
            if folder in localized:
                loc_name, loc_flags = localized[folder]
                if _matches(loc_name) or _matches(folder):
                    self._send_untagged(f'LIST ({loc_flags}) "/" "{loc_name}"')
            else:
                if _matches(folder):
                    attrs = _folder_attrs(folder)
                    self._send_untagged(f'LIST ({attrs}) "/" "{folder}"')
        self._send(tag, "OK", "LIST completed")

    async def _cmd_examine(self, tag: str, args: str) -> None:
        await self._select_internal(tag, args, read_only=True)

    async def _cmd_select(self, tag: str, args: str) -> None:
        await self._select_internal(tag, args, read_only=False)

    async def _select_internal(self, tag: str, args: str, read_only: bool) -> None:
        wire = _unquote(args)
        folder = self._resolve_folder(wire)
        if folder is None or not self._state.folder_exists(folder):
            # A failed SELECT leaves the connection unselected (RFC 3501
            # §6.3.1); the text is what Gmail answers.
            self._selected_folder = None
            self._read_only = False
            self._send(
                tag,
                "NO",
                f"[NONEXISTENT] Unknown Mailbox: {wire} (now in authenticated state) (Failure)",
            )
            return
        self._selected_folder = folder
        self._read_only = read_only
        msgs = self._state.messages_in_folder(folder)
        uidval = self._state.uidvalidity(folder)
        self._send_untagged(f"{len(msgs)} EXISTS")
        self._send_untagged("0 RECENT")
        self._send_untagged(f"OK [UIDVALIDITY {uidval}] UIDs valid")
        next_uid = self._state.uidnext(folder)
        self._send_untagged(f"OK [UIDNEXT {next_uid}] Predicted next UID")
        self._send_untagged("FLAGS (\\Answered \\Flagged \\Deleted \\Seen \\Draft)")
        mode_str = "[READ-ONLY]" if read_only else "[READ-WRITE]"
        cmd_name = "EXAMINE" if read_only else "SELECT"
        self._send(tag, "OK", f"{mode_str} {cmd_name} completed")

    async def _cmd_status(self, tag: str, args: str) -> None:
        """RFC 3501 §6.3.10 STATUS, counted like SELECT counts EXISTS."""
        m = re.match(r'\s*("(?:[^"\\]|\\.)*"|\S+)\s+\(([^)]*)\)\s*$', args)
        if not m:
            self._send(tag, "BAD", "Could not parse command")
            return
        wire = _first_astring(m.group(1)) or ""
        folder = self._resolve_folder(wire)
        if folder is None or not self._state.folder_exists(folder):
            self._send(tag, "NO", f"[NONEXISTENT] Unknown Mailbox: {wire} (Failure)")
            return
        msgs = self._state.messages_in_folder(folder)
        values = {
            "MESSAGES": len(msgs),
            "RECENT": 0,
            "UIDNEXT": self._state.uidnext(folder),
            "UIDVALIDITY": self._state.uidvalidity(folder),
            "UNSEEN": sum(1 for _, msg in msgs if "\\Seen" not in msg.flags),
        }
        items = m.group(2).upper().split()
        unknown = [item for item in items if item not in values]
        if unknown or not items:
            self._send(tag, "BAD", "Could not parse command")
            return
        listed = " ".join(f"{item} {values[item]}" for item in items)
        quoted = wire.replace("\\", "\\\\").replace('"', '\\"')
        self._send_untagged(f'STATUS "{quoted}" ({listed})')
        self._send(tag, "OK", "Success")

    async def _cmd_close(self, tag: str, args: str) -> None:
        if self._selected_folder is not None:
            folder = self._selected_folder
            msgs = list(self._state.messages_in_folder(folder))
            for uid, msg in msgs:
                if "\\Deleted" in msg.flags:
                    msg.flags.discard("\\Deleted")
                    self._state.remove_message_from_folder(folder, msg)
            self._selected_folder = None
        self._send(tag, "OK", "CLOSE completed")

    async def _cmd_noop(self, tag: str, args: str) -> None:
        self._send(tag, "OK", "NOOP completed")

    async def _cmd_logout(self, tag: str, args: str) -> None:
        self._send_untagged("BYE LOGOUT Requested")
        self._send(tag, "OK", "LOGOUT completed")
        self._w.close()

    async def _cmd_create(self, tag: str, args: str) -> None:
        wire = _unquote(args)
        folder = self._resolve_folder(wire) or decode_mutf7(wire)
        if self._state.folder_exists(folder):
            self._send(tag, "NO", "[ALREADYEXISTS] Mailbox already exists")
            return
        self._state.create_folder(folder)
        self._send(tag, "OK", "CREATE completed")

    async def _cmd_expunge(self, tag: str, args: str) -> None:
        if self._read_only:
            self._send(tag, "NO", "[READ-ONLY] Mailbox is read-only")
            return
        if self._selected_folder is None:
            self._send(tag, "BAD", "[CLIENTBUG] Command requires selected state")
            return
        folder = self._selected_folder
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
        m = re.match(r'"?([^"]+)"?\s+(?:\(([^)]*)\)\s+)?(?:"([^"]*)"\s+)?\{(\d+)\}', args)
        if not m:
            self._send(tag, "BAD", "APPEND parse error")
            return
        folder = self._resolve_folder(m.group(1))
        if folder is None or not self._state.folder_exists(folder):
            self._send(tag, "NO", "[TRYCREATE] Mailbox does not exist")
            return
        internal_date = datetime.now(UTC)
        if m.group(3) is not None:
            try:
                internal_date = datetime.strptime(m.group(3).strip(), "%d-%b-%Y %H:%M:%S %z")
            except ValueError:
                self._send(tag, "BAD", "Could not parse command")
                return
        size = int(m.group(4))
        self._send_untagged_raw("+ Ready for literal data\r\n")
        data = await self._r.readexactly(size)
        await self._r.readline()
        msg_obj = email.message_from_bytes(data)
        gm_msgid = self._state.new_msgid()
        label = FOLDER_TO_LABEL.get(folder, folder)
        labels = {label} if label != "__ALL_MAIL__" else set()
        message = Message(
            gm_msgid=gm_msgid,
            gm_thrid=gm_msgid,
            rfc822=data,
            labels=labels,
            flags=set((m.group(2) or "").split()),
            message_id=msg_obj.get("Message-ID") or "",
            from_addr=msg_obj.get("From") or "",
            to_addr=msg_obj.get("To") or "",
            subject=msg_obj.get("Subject") or "",
            date=msg_obj.get("Date") or "",
            internal_date=internal_date,
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
        elif sub == "EXPUNGE":
            await self._uid_expunge(tag, sub_args)
        else:
            self._send(tag, "BAD", f"Unknown UID sub-command {sub}")

    async def _uid_search(self, tag: str, args: str) -> None:
        """RFC 3501 §6.4.4 UID SEARCH; a program that does not parse is
        answered BAD, as Gmail does."""
        if self._selected_folder is None:
            self._send(tag, "BAD", "[CLIENTBUG] Command requires selected state")
            return
        folder = self._selected_folder
        try:
            uids = search(args, self._state.messages_in_folder(folder))
        except SearchSyntaxError:
            self._send(tag, "BAD", "Could not parse command")
            return
        self._send_untagged("SEARCH " + " ".join(str(u) for u in uids) if uids else "SEARCH")
        self._send(tag, "OK", "SEARCH completed (Success)")

    async def _uid_fetch(self, tag: str, args: str) -> None:
        if self._selected_folder is None:
            self._send(tag, "BAD", "[CLIENTBUG] Command requires selected state")
            return
        folder = self._selected_folder

        if self._state._fetch_delay_ms > 0:
            delay = self._state._fetch_delay_ms / 1000.0
            if self._state._fetch_delay_once:
                self._state._fetch_delay_ms = 0
                self._state._fetch_delay_once = False
            await asyncio.sleep(delay)

        m = re.match(r"([\d,:]+)\s+\((.+)\)", args)
        if not m:
            self._send(tag, "BAD", "FETCH parse error")
            return
        uid_spec = m.group(1)
        items_raw = m.group(2).strip()
        items = items_raw.upper()

        if not _validate_fetch_items(items):
            self._send(tag, "BAD", "Could not parse command")
            return

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

            # Non-PEEK full/body read sets \Seen in READ-WRITE mode
            is_non_peek = (
                re.search(r"\bRFC822(?!\.)\b", items) is not None
                or "BODY[]" in items
                or "BODY[HEADER]" in items
            )
            if is_non_peek and not self._read_only:
                msg.flags.add("\\Seen")

            literal_spec = _extract_literal_spec(items, msg)
            if literal_spec is not None and msg.rfc822:
                body_key, payload = literal_spec
                parts = self._build_fetch_response(folder, uid, msg, items, skip_body=True)
                prefix = f"* {uid} FETCH ({parts} " if parts else f"* {uid} FETCH ("
                literal_hdr = f"{prefix}{body_key} {{{len(payload)}}}\r\n"
                self._w.write(literal_hdr.encode())
                self._w.write(payload)
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
        if re.search(r"\bFLAGS\b", items):
            flags = " ".join(sorted(msg.flags))
            parts.append(f"FLAGS ({flags})")
        if re.search(r"\bX-GM-MSGID\b", items):
            parts.append(f"X-GM-MSGID {msg.gm_msgid}")
        if re.search(r"\bX-GM-THRID\b", items):
            parts.append(f"X-GM-THRID {msg.gm_thrid}")
        if re.search(r"\bX-GM-LABELS\b", items):
            labels = self._state.labels_visible_from(folder, msg)
            label_str = " ".join(_quote_label(l) for l in labels)
            parts.append(f"X-GM-LABELS ({label_str})")
        if re.search(r"\bENVELOPE\b", items):
            parts.append(f"ENVELOPE {_build_envelope(msg)}")
        if re.search(r"\bRFC822\.SIZE\b", items):
            parts.append(f"RFC822.SIZE {len(msg.rfc822)}")
        if re.search(r"\bINTERNALDATE\b", items) and msg.internal_date is not None:
            parts.append(f'INTERNALDATE "{msg.internal_date.strftime("%d-%b-%Y %H:%M:%S +0000")}"')
        if re.search(r"\bBODYSTRUCTURE\b", items):
            parts.append(f"BODYSTRUCTURE {bodystructure(msg.rfc822)}")
        if not skip_body:
            if re.search(r"\bRFC822(?!\.)\b", items):
                parts.append(
                    f"RFC822 {{{len(msg.rfc822)}}}\r\n{msg.rfc822.decode('utf-8', errors='replace')}"
                )
            if "BODY[]" in items or "BODY.PEEK[]" in items:
                parts.append(
                    f"BODY[] {{{len(msg.rfc822)}}}\r\n{msg.rfc822.decode('utf-8', errors='replace')}"
                )
        return " ".join(parts)

    async def _uid_store(self, tag: str, args: str) -> None:
        if self._read_only:
            self._send(tag, "NO", "[READ-ONLY] Mailbox is read-only")
            return
        if self._selected_folder is None:
            self._send(tag, "BAD", "[CLIENTBUG] Command requires selected state")
            return
        folder = self._selected_folder
        m = re.match(
            r"([\d,:]+)\s+([+-]?)(FLAGS|X-GM-LABELS)(\.SILENT)?\s+(.*)$",
            args,
            re.IGNORECASE,
        )
        if not m:
            self._send(tag, "BAD", "Could not parse command")
            return
        uid_spec = m.group(1)
        op = m.group(2)
        field = m.group(3).upper()
        silent = m.group(4) is not None
        values_raw = m.group(5).strip()
        uids_to_store: list[int] = []
        for part in uid_spec.split(","):
            if ":" in part:
                lo, hi = part.split(":", 1)
                uids_to_store.extend(range(int(lo), int(hi) + 1))
            else:
                uids_to_store.append(int(part))
        labels: list[str] = []
        if field == "X-GM-LABELS":
            parsed = _parse_label_list(values_raw)
            if parsed is None:
                # Real Gmail: a malformed list, e.g. a quoted string
                # holding a bare backslash ("\Trash"), is a syntax error.
                self._log_label_store(op, values_raw, None, "BAD")
                self._send(tag, "BAD", "Could not parse command")
                return
            labels = parsed
        # Check injected rejection BEFORE mutating state, so the test
        # observes "STORE never ran" rather than "half-applied".
        if field == "X-GM-LABELS" and self._state.next_store_rejection is not None:
            status, text = self._state.next_store_rejection
            self._state.next_store_rejection = None
            self._log_label_store(op, values_raw, tuple(labels), status)
            self._send(tag, status, text)
            return
        any_resolved = False
        for uid in uids_to_store:
            msg = self._state.message_by_uid(folder, uid)
            if msg is None:
                continue
            any_resolved = True
            if field == "X-GM-LABELS":
                uids_before = [u for u, _ in self._state.messages_in_folder(folder)]
                for wire in labels:
                    if self._state.record_store_operations and op in ("+", "-"):
                        self._state.store_operations.append((uid, op, wire))
                    # Drafts is not an X-GM-LABELS label on Gmail: STORE
                    # of it is accepted and changes nothing.
                    if wire in HIDDEN_LABELS:
                        continue
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
                if self._state.message_by_uid(folder, uid) is None:
                    # The label change took the message out of the
                    # selected mailbox (e.g. +\Trash): Gmail expunges it.
                    self._send_untagged(f"{uids_before.index(uid) + 1} EXPUNGE")
                elif not silent:
                    visible = self._state.labels_visible_from(folder, msg)
                    label_str = " ".join(_quote_label(l) for l in visible)
                    self._send_untagged(f"{uid} FETCH (X-GM-LABELS ({label_str}) UID {uid})")
            elif field == "FLAGS":
                flags = set(values_raw.strip("()").split())
                if op == "+":
                    msg.flags.update(flags)
                elif op == "-":
                    msg.flags -= flags
                else:
                    msg.flags = flags
                if not silent:
                    flags_str = " ".join(sorted(msg.flags))
                    self._send_untagged(f"{uid} FETCH (FLAGS ({flags_str}) UID {uid})")
                if "\\Deleted" in msg.flags:
                    self._auto_expunge(folder, uid, msg)
        if (
            not any_resolved
            and uids_to_store
            and not any(self._state.uid_was_assigned(folder, uid) for uid in uids_to_store)
        ):
            if field == "X-GM-LABELS":
                self._log_label_store(op, values_raw, tuple(labels), "NO")
            self._send(tag, "NO", "STORE failed: no matching UIDs")
            return
        if field == "X-GM-LABELS":
            self._log_label_store(op, values_raw, tuple(labels), "OK")
        self._send(tag, "OK", "STORE completed")

    def _auto_expunge(self, folder: str, uid: int, msg: Message) -> None:
        """Gmail's default auto-expunge: a message marked `\\Deleted`
        leaves the selected label mailbox at once, without EXPUNGE.

        In Trash or Spam that deletes it for good; in any other label
        mailbox (drafts included) only that mailbox loses it. All Mail
        is not a label mailbox and keeps the flag until EXPUNGE.
        """
        label = FOLDER_TO_LABEL.get(folder, folder)
        if label == "__ALL_MAIL__":
            return
        uids = [u for u, _ in self._state.messages_in_folder(folder)]
        seq = uids.index(uid) + 1 if uid in uids else None
        msg.flags.discard("\\Deleted")
        if label in ("\\Trash", "\\Spam"):
            self._state.delete_message(msg)
        else:
            self._state.leave_mailbox(msg, folder)
        if seq is not None:
            self._send_untagged(f"{seq} EXPUNGE")

    def _log_label_store(
        self, op: str, raw: str, labels: tuple[str, ...] | None, status: str
    ) -> None:
        self._state.label_store_log.append(
            LabelStoreRecord(user=self._user, op=op, raw=raw, labels=labels, status=status)
        )

    async def _uid_expunge(self, tag: str, args: str) -> None:
        """RFC 4315 UID EXPUNGE: expunge only the named `\\Deleted` UIDs."""
        if self._read_only:
            self._send(tag, "NO", "[READ-ONLY] Mailbox is read-only")
            return
        if self._selected_folder is None:
            self._send(tag, "BAD", "[CLIENTBUG] Command requires selected state")
            return
        folder = self._selected_folder
        label = FOLDER_TO_LABEL.get(folder, folder)
        wanted: set[int] = set()
        for part in args.strip().split(","):
            if not part:
                continue
            if ":" in part:
                lo, hi = part.split(":", 1)
                wanted.update(range(int(lo), int(hi) + 1))
            else:
                wanted.add(int(part))
        for uid, msg in self._state.messages_in_folder(folder):
            if uid not in wanted or "\\Deleted" not in msg.flags:
                continue
            msg.flags.discard("\\Deleted")
            if label != "__ALL_MAIL__":
                self._state.remove_label(msg, label)
            self._send_untagged(f"{uid} EXPUNGE")
        self._send(tag, "OK", "UID EXPUNGE completed")

    async def _uid_copy(self, tag: str, args: str) -> None:
        if self._selected_folder is None:
            self._send(tag, "BAD", "[CLIENTBUG] Command requires selected state")
            return
        folder = self._selected_folder
        m = re.match(r'([0-9,:*]+)\s+"?([^"]+)"?', args)
        if not m:
            self._send(tag, "BAD", "COPY parse error")
            return
        uids = _parse_uid_set(m.group(1))
        target = self._resolve_folder(m.group(2))
        if target is None or not self._state.folder_exists(target):
            self._send(tag, "NO", "[TRYCREATE] Mailbox does not exist")
            return
        target_label = FOLDER_TO_LABEL.get(target, target)
        found_any = False
        for uid in uids:
            msg = self._state.message_by_uid(folder, uid)
            if msg is not None:
                found_any = True
                if target_label != "__ALL_MAIL__":
                    self._state.add_label(msg, target_label)
        if not found_any:
            self._send(tag, "NO", "Message not found")
            return
        self._send(tag, "OK", "COPY completed")

    async def _uid_move(self, tag: str, args: str) -> None:
        if self._read_only:
            self._send(tag, "NO", "[READ-ONLY] Mailbox is read-only")
            return
        if self._selected_folder is None:
            self._send(tag, "BAD", "[CLIENTBUG] Command requires selected state")
            return
        folder = self._selected_folder
        m = re.match(r'([0-9,:*]+)\s+"?([^"]+)"?', args)
        if not m:
            self._send(tag, "BAD", "MOVE parse error")
            return
        uids = _parse_uid_set(m.group(1))
        target = self._resolve_folder(m.group(2))
        if target is None or not self._state.folder_exists(target):
            self._send(tag, "NO", "[TRYCREATE] Mailbox does not exist")
            return
        src_label = FOLDER_TO_LABEL.get(folder, folder)
        target_label = FOLDER_TO_LABEL.get(target, target)
        moved_uids: list[int] = []
        for uid in uids:
            msg = self._state.message_by_uid(folder, uid)
            if msg is not None:
                moved_uids.append(uid)
                if src_label != "__ALL_MAIL__":
                    self._state.remove_label(msg, src_label)
                if target_label != "__ALL_MAIL__":
                    self._state.add_label(msg, target_label)
        if not moved_uids:
            self._send(tag, "NO", "Message not found")
            return
        for uid in moved_uids:
            self._send_untagged(f"{uid} EXPUNGE")
        self._send(tag, "OK", "MOVE completed")

    # ---------------------------------------------------------- helpers

    def _record(self, cmd: str, args: str) -> None:
        """Append the command to the shared command log."""
        if self.epoch != self._state.epoch:
            return
        command = cmd
        mailbox: str | None = None
        if cmd in _MAILBOX_COMMANDS:
            mailbox = _first_astring(args)
        elif cmd == "UID":
            parts = args.split(None, 2)
            if parts:
                command = f"UID {parts[0].upper()}"
            if command in ("UID COPY", "UID MOVE") and len(parts) > 2:
                mailbox = _first_astring(parts[2])
        if cmd == "LOGIN":
            recorded_args = ""
        elif cmd == "AUTHENTICATE":
            recorded_args = args.split(None, 1)[0] if args else ""
        else:
            recorded_args = args
        user = self._user
        if cmd == "LOGIN" and user is None:
            # LOGIN names the user it is about to authenticate.
            first = args.split(None, 1)
            user = _unquote(first[0]) if first else None
        self._state.command_log.append(
            CommandRecord(user=user, command=command, mailbox=mailbox, args=recorded_args)
        )

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


def _first_astring(args: str) -> str | None:
    """Return the first quoted string or atom of an argument list, unquoted."""
    s = args.lstrip()
    if not s:
        return None
    if s.startswith('"'):
        out: list[str] = []
        i = 1
        while i < len(s):
            ch = s[i]
            if ch == "\\" and i + 1 < len(s):
                out.append(s[i + 1])
                i += 2
                continue
            if ch == '"':
                break
            out.append(ch)
            i += 1
        return "".join(out)
    return s.split(None, 1)[0]


def _quote_label(label: str) -> str:
    """Render a label in an X-GM-LABELS response the way Gmail does:
    system labels as flag atoms, other labels as an atom when they are
    one, otherwise as an RFC 3501 quoted string."""
    if _is_flag_atom(label) or _is_atom(label):
        return label
    escaped = label.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _is_atom(token: str) -> bool:
    return bool(token) and all(
        0x20 < ord(ch) < 0x7F and ch not in _ATOM_SPECIALS and ch != "\\" for ch in token
    )


def _is_flag_atom(token: str) -> bool:
    return token.startswith("\\") and _is_atom(token[1:])


def _parse_label_list(raw: str) -> list[str] | None:
    """Parse the label list of an X-GM-LABELS STORE per RFC 3501.

    Accepts a parenthesised list or a single label. Each label is an
    atom, a flag (`\\` atom) or a quoted string in which `\\` may only
    escape `\\` or `"` (RFC 3501 §4.3, §9 `quoted`). Anything else,
    such as `"\\Trash"` with a single backslash, returns None: Gmail
    answers it with `BAD Could not parse command`.
    """
    s = raw.strip()
    if s.startswith("("):
        if not s.endswith(")"):
            return None
        s = s[1:-1]
        if not s:
            return []
    elif not s:
        return None
    labels: list[str] = []
    i = 0
    n = len(s)
    while i < n:
        if s[i] == '"':
            out: list[str] = []
            i += 1
            while True:
                if i >= n:
                    return None
                ch = s[i]
                if ch == '"':
                    i += 1
                    break
                if ch == "\\":
                    if i + 1 >= n or s[i + 1] not in ('"', "\\"):
                        return None
                    out.append(s[i + 1])
                    i += 2
                    continue
                if ch in "\r\n":
                    return None
                out.append(ch)
                i += 1
            labels.append("".join(out))
        else:
            j = s.find(" ", i)
            if j == -1:
                j = n
            token = s[i:j]
            if not (_is_atom(token) or _is_flag_atom(token)):
                return None
            labels.append(token)
            i = j
        if i < n:
            if s[i] != " " or i + 1 >= n:
                return None
            i += 1
    return labels


def _esc_str(s: str) -> str:
    return s.replace("\\", "\\\\").replace('"', '\\"')


def _validate_fetch_items(items: str) -> bool:
    cleaned = re.sub(
        r"BODY(?:\.PEEK)?\[HEADER\.FIELDS\s*\([^)]*\)\]", "", items, flags=re.IGNORECASE
    )
    cleaned = re.sub(r"BODY(?:\.PEEK)?\[(?:HEADER)?\]", "", cleaned, flags=re.IGNORECASE)
    tokens = [t.strip() for t in cleaned.split() if t.strip()]
    valid_tokens = {
        "FLAGS",
        "UID",
        "RFC822.SIZE",
        "RFC822.HEADER",
        "RFC822",
        "INTERNALDATE",
        "ENVELOPE",
        "BODYSTRUCTURE",
        "X-GM-MSGID",
        "X-GM-THRID",
        "X-GM-LABELS",
    }
    for t in tokens:
        if t not in valid_tokens:
            return False
    return True


def _extract_literal_spec(items: str, msg: Message) -> tuple[str, bytes] | None:
    if not msg.rfc822:
        return None
    hdr_end = msg.rfc822.find(b"\r\n\r\n")
    header_bytes = msg.rfc822[: hdr_end + 2] if hdr_end >= 0 else msg.rfc822

    # Check BODY.PEEK[HEADER.FIELDS (...)] or without PEEK
    m_fields = re.search(
        r"BODY(?:\.PEEK)?\[HEADER\.FIELDS\s*\(([^)]*)\)\]", items, re.IGNORECASE
    )
    if m_fields:
        field_names = [f.strip().lower() for f in m_fields.group(1).split()]
        msg_obj = email.message_from_bytes(header_bytes)
        selected_lines: list[bytes] = []
        for name in field_names:
            for val in msg_obj.get_all(name, []):
                selected_lines.append(f"{name.capitalize()}: {val}\r\n".encode())
        payload = b"".join(selected_lines) + b"\r\n"
        return f"BODY[HEADER.FIELDS ({m_fields.group(1)})]", payload

    if "BODY.PEEK[HEADER]" in items or "BODY[HEADER]" in items:
        return "BODY[HEADER]", header_bytes

    if "RFC822.HEADER" in items:
        return "RFC822.HEADER", header_bytes

    if re.search(r"\bRFC822(?!\.)\b", items):
        return "RFC822", msg.rfc822

    if "BODY.PEEK[]" in items or "BODY[]" in items:
        return "BODY[]", msg.rfc822

    return None


def _format_address_list(header_val: str | None) -> str:
    if not header_val:
        return "NIL"
    addrs = email.utils.getaddresses([header_val])
    if not addrs:
        return "NIL"
    parts: list[str] = []
    for name, addr in addrs:
        if not name and not addr:
            continue
        if "@" in addr:
            mailbox, host = addr.rsplit("@", 1)
        else:
            mailbox, host = addr, ""
        name_str = f'"{_esc_str(name)}"' if name else "NIL"
        parts.append(f'({name_str} NIL "{_esc_str(mailbox)}" "{_esc_str(host)}")')
    return "(" + " ".join(parts) + ")" if parts else "NIL"


def _build_envelope(msg: Message) -> str:
    msg_obj = email.message_from_bytes(msg.rfc822) if msg.rfc822 else email.message.Message()
    date_val = msg_obj.get("Date") or msg.date or ""
    date_str = f'"{_esc_str(date_val)}"' if date_val else "NIL"

    subj_val = msg_obj.get("Subject") or msg.subject or ""
    subj_str = f'"{_esc_str(subj_val)}"' if subj_val else "NIL"

    from_str = _format_address_list(msg_obj.get("From") or msg.from_addr)
    sender_hdr = msg_obj.get("Sender")
    sender_str = _format_address_list(sender_hdr) if sender_hdr else from_str
    reply_to_hdr = msg_obj.get("Reply-To")
    reply_to_str = _format_address_list(reply_to_hdr) if reply_to_hdr else from_str

    to_str = _format_address_list(msg_obj.get("To") or msg.to_addr)
    cc_str = _format_address_list(msg_obj.get("Cc"))
    bcc_str = _format_address_list(msg_obj.get("Bcc"))

    in_reply_to_val = msg_obj.get("In-Reply-To")
    in_reply_to_str = f'"{_esc_str(in_reply_to_val)}"' if in_reply_to_val else "NIL"

    mid_val = msg_obj.get("Message-ID") or msg.message_id or ""
    mid_str = f'"{_esc_str(mid_val)}"' if mid_val else "NIL"

    return (
        f"({date_str} {subj_str} {from_str} {sender_str} {reply_to_str} "
        f"{to_str} {cc_str} {bcc_str} {in_reply_to_str} {mid_str})"
    )


async def start_gmail_mock(
    state: GmailState, host: str = "127.0.0.1", port: int = 0
) -> tuple[asyncio.Server, int]:
    async def _handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        handler = GmailIMAPHandler(reader, writer, state)
        await handler.run()

    state.loop = asyncio.get_running_loop()
    server = await asyncio.start_server(_handle, host, port)
    actual_port = server.sockets[0].getsockname()[1]
    return server, actual_port
