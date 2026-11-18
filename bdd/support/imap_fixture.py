"""IMAP fixture for the BDD harness.

Provides seeding, inspection, and reset operations against the two
dovecot test instances defined in `docker-compose.yml`. All operations
use the standard `imaplib` client so that failures surface as real IMAP
errors — the same surface the server itself sees.

This module is the "second channel" that scenarios use to verify state
independently of the MCP response (BDD Guidelines §13.2 Prüfung 1).
"""

from __future__ import annotations

import email
import email.message
import email.utils
import imaplib
import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from email.message import EmailMessage
from email.policy import SMTP

# Password is the same for all test users (see docker/dovecot/users/README.md).
TEST_PASSWORD = "test123"

def _encode_mutf7(utf8: str) -> str:
    """RFC 3501 §5.1.3 Modified UTF-7 mailbox name encoding.

    Mirrors `imap_core._quote_mailbox`'s inner encode step so the
    fixture can talk to Dovecot using the same wire form that the
    server uses. ASCII characters in 0x20-0x7E pass through; `&`
    becomes `&-`; everything else is base64'd UTF-16BE between `&`
    and `-`.
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


# Per-instance user list. Keep in sync with docker/dovecot/users/*.passwd.
# mock-gmail is an in-process mock that resets via GmailState.reset(),
# not via IMAP commands — its user entry is listed for completeness but
# reset_user skips it (see guard below).
_USERS_BY_INSTANCE: dict[str, list[str]] = {
    "imap-a": ["gupta", "osthues"],
    "imap-b": ["personal", "archive", "info"],
    "mock-gmail": ["test"],
}

# Mapping from the `account_id` used in feature files to a concrete
# (instance, user) pair on our Dovecot fixture. Kept as an explicit
# dict rather than derived by splitting strings so that the feature
# file's vocabulary is a stable public name and the mapping is the
# fixture's private concern.
_ACCOUNT_ID_TO_INSTANCE_USER: dict[str, tuple[str, str]] = {
    "gupta-scaratec": ("imap-a", "gupta"),
    "osthues-mail": ("imap-a", "osthues"),
    "personal": ("imap-b", "personal"),
    "archive": ("imap-b", "archive"),
    "gmail-archive": ("mock-gmail", "test"),
    "gmail-ronly": ("imap-b", "archive"),  # Alias to archive for oauth tests
    "scaratec-gmail": ("mock-gmail", "test"),
    "gupta-gmail-de": ("mock-gmail", "test"),
    "archive-srv": ("imap-b", "archive"),
    "ext-user": ("imap-a", "gupta"),
    "info-bg": ("imap-b", "info"),
    "accounting@scaratec.example": ("imap-a", "gupta"),
    "accounting": ("imap-a", "gupta"),
    "gupta@a.local": ("imap-a", "gupta"),
    "osthues@a.local": ("imap-a", "osthues"),
    "secondary": ("imap-b", "personal"),
    "g-work": ("mock-gmail", "test"),
    "g-none": ("mock-gmail", "test"),
    "gupta-scaratec-google": ("mock-gmail", "test"),
}


def resolve_account(account_id: str) -> tuple[str, str]:
    """Return the (instance, user) pair backing a feature-file account id."""
    if account_id in _ACCOUNT_ID_TO_INSTANCE_USER:
        return _ACCOUNT_ID_TO_INSTANCE_USER[account_id]
    if "@" in account_id:
        local = account_id.split("@", 1)[0]
        if local in _USERS_BY_INSTANCE["imap-a"]:
            return ("imap-a", local)
        if local in _USERS_BY_INSTANCE["imap-b"]:
            return ("imap-b", local)
    raise KeyError(f"Account id {account_id!r} not mapped in test harness fixture")


def resolve_uid(
    context: object,
    account: str,
    folder: str,
    uid_hint: int,
    *,
    allow_unseeded_probe: bool = False,
) -> int:
    """Resolve a UID hint from a Gherkin step to the real IMAP/mock UID.

    Strictly binds to (account, canonical_or_wire_folder, uid_hint).
    Raises AssertionError if the message was not seeded for (account, folder, uid_hint),
    unless (account, folder, uid_hint) is recorded in context.explicit_unseeded_uids
    OR 0 messages were seeded for (account, folder) in this scenario.
    """
    lookup: dict[tuple[str, str, int], int] = getattr(context, "message_uids", None) or {}

    if (account, folder, uid_hint) in lookup:
        return lookup[(account, folder, uid_hint)]

    norm_folder = folder.rstrip("/")
    for (acc, fld, hint), real_uid in lookup.items():
        if acc == account and hint == uid_hint:
            if fld.rstrip("/") == norm_folder:
                return real_uid
            imap_fixture = getattr(context, "imap", None)
            if imap_fixture is not None:
                try:
                    instance, user = resolve_account(account)
                    wire_fld = imap_fixture.to_wire(instance, user, fld).rstrip("/")
                    wire_in = imap_fixture.to_wire(instance, user, folder).rstrip("/")
                    if wire_fld == wire_in or wire_fld == norm_folder or fld.rstrip("/") == wire_in:
                        return real_uid
                except Exception:
                    pass

    explicit = getattr(context, "explicit_unseeded_uids", set())
    seeded_in_folder_count = sum(
        1
        for (acc, fld, _) in lookup
        if acc == account and fld.rstrip("/") == norm_folder
    )

    is_explicitly_unseeded = (
        (account, norm_folder, uid_hint) in explicit
        or (account, folder, uid_hint) in explicit
    )

    if is_explicitly_unseeded or seeded_in_folder_count == 0:
        return uid_hint

    raise AssertionError(
        f"No message seeded for ({account}, {folder}, {uid_hint}) - "
        f"folder has {seeded_in_folder_count} seeded message(s) and UID is not in explicit_unseeded_uids"
    )

# System folders that the BDD fixture must not delete during reset.
# Only INBOX is truly system-level: Dovecot auto-creates it on LOGIN
# and won't let us DELETE it. Drafts/Sent/Trash are configured
# `auto=no` in the fixture (see docker/dovecot/conf/dovecot.conf) so
# they only exist after a scenario explicitly creates them — and must
# be cleared out at reset_user time so they do not leak into the
# next scenario's hidden_folders_count.
_SYSTEM_FOLDERS = frozenset({"INBOX"})

# RFC 6154 §2 special-use attributes, lower-cased for comparison.
_SPECIAL_USE_FLAGS = frozenset(
    {"\\all", "\\archive", "\\drafts", "\\flagged", "\\junk", "\\sent", "\\trash"}
)


@dataclass
class SeededMessage:
    """A message inserted by the fixture, returned for later assertion."""

    uid: int
    message_id: str
    subject: str
    flags: tuple[str, ...]


@dataclass
class IMAPFixture:
    """Wraps the two dovecot instances for test seeding and inspection."""

    instances: dict[str, tuple[str, int]]
    _connections: dict[tuple[str, str], imaplib.IMAP4] = field(
        default_factory=dict, init=False
    )
    _folder_wire_map: dict[tuple[str, str], dict[str, str]] = field(
        default_factory=dict, init=False
    )

    def register_wire_folders(
        self, instance: str, user: str, mapping: dict[str, str]
    ) -> None:
        """Store a UTF-8 → mUTF-7 wire-name mapping for folder names.

        Steps that reference folders by UTF-8 name are transparently
        resolved to the wire form before talking to Dovecot.
        """
        key = (instance, user)
        self._folder_wire_map.setdefault(key, {}).update(mapping)

    def to_wire(self, instance: str, user: str, folder: str) -> str:
        """Resolve a UTF-8 folder name to its mUTF-7 wire form.

        Explicit mappings from `register_wire_folders` win first.
        ASCII-only inputs pass through unchanged. Non-ASCII inputs get
        the auto mUTF-7 encoder applied so the fixture can talk to
        Dovecot with the same wire form the server uses. Strings that
        already contain a `&` shift sequence are assumed to be wire
        already and are passed through verbatim.
        """
        explicit = self._folder_wire_map.get((instance, user), {}).get(folder)
        if explicit is not None:
            return explicit
        if all(0x20 <= ord(c) <= 0x7E for c in folder):
            return folder
        return _encode_mutf7(folder)

    # ---------------------------------------------------------- connection

    def connect(self, instance: str, user: str) -> imaplib.IMAP4:
        """Open or reuse an IMAP connection for (instance, user)."""
        key = (instance, user)
        conn = self._connections.get(key)
        if conn is not None:
            try:
                conn.noop()
                return conn
            except (imaplib.IMAP4.abort, imaplib.IMAP4.error, OSError):
                self._connections.pop(key, None)

        host, port = self.instances[instance]
        conn = imaplib.IMAP4(host, port)
        pwd = "test" if instance == "mock-gmail" else TEST_PASSWORD
        conn.login(user, pwd)
        self._connections[key] = conn
        return conn

    def close_all(self) -> None:
        for conn in self._connections.values():
            try:
                conn.logout()
            except (imaplib.IMAP4.abort, imaplib.IMAP4.error, OSError):
                pass
        self._connections.clear()

    # -------------------------------------------------------------- reset

    def reset_all_users(self) -> None:
        """Wipe every test user's mailbox on both dovecot instances."""
        for instance, users in _USERS_BY_INSTANCE.items():
            for user in users:
                self.reset_user(instance, user)
        self.close_all()

    def reset_user(self, instance: str, user: str) -> None:
        """Delete every non-system folder and empty all system folders."""
        # mock-gmail resets via GmailState.reset(), not via IMAP commands.
        if instance == "mock-gmail":
            return
        conn = self.connect(instance, user)
        _, folder_lines = conn.list()
        folders = [self._parse_folder_name(line) for line in folder_lines or []]

        # Empty system folders in place.
        for folder in folders:
            if folder in _SYSTEM_FOLDERS:
                self._empty_folder(conn, folder)

        # Delete non-system folders, deepest first so parents go last.
        # Folder names containing spaces must be quoted on the wire;
        # imaplib does not auto-quote DELETE arguments, so without
        # this an undeletable "Tom &- Jerry" survives into the next
        # scenario and inflates hidden_folders_count.
        for folder in sorted(
            (f for f in folders if f not in _SYSTEM_FOLDERS),
            key=lambda f: f.count("/"),
            reverse=True,
        ):
            quoted_target = f'"{folder}"' if " " in folder else folder
            status, res = conn.delete(quoted_target)
            if status != "OK":
                err_text = str(res)
                if "[NONEXISTENT]" not in err_text:
                    raise RuntimeError(f"Reset: failed to delete folder {folder!r} on {instance}/{user}: {res!r}")

        # Post-reset verification (M-16): only empty INBOX must remain
        _, post_lines = conn.list()
        remaining = [self._parse_folder_name(line) for line in post_lines or []]
        non_inbox = [f for f in remaining if f != "INBOX"]
        if non_inbox:
            raise RuntimeError(f"Reset verification failed on {instance}/{user}: non-INBOX folders remaining: {non_inbox}")
        st, _ = conn.select(self._q("INBOX"))
        if st == "OK":
            st, uids = conn.search(None, "ALL")
            if st == "OK" and uids and uids[0]:
                raise RuntimeError(f"Reset verification failed on {instance}/{user}: INBOX not empty: {uids[0]!r}")
            conn.close()

    def _empty_folder(self, conn: imaplib.IMAP4, folder: str) -> None:
        status, _ = conn.select(self._q(folder))
        if status != "OK":
            return
        status, uids = conn.search(None, "ALL")
        if status != "OK" or not uids[0]:
            conn.close()
            return
        for uid in uids[0].split():
            conn.store(uid, "+FLAGS", "\\Deleted")
        conn.expunge()
        conn.close()

    def empty_folder(self, instance: str, user: str, folder: str) -> None:
        conn = self.connect(instance, user)
        self._empty_folder(conn, folder)

    # ----------------------------------------------------------- creation

    def create_folder(self, instance: str, user: str, folder: str) -> None:
        """Create a folder (and any implicit parents) if missing.

        Resolves UTF-8 folder names to their mUTF-7 wire form if a
        mapping is registered.  Folder names containing spaces must be
        quoted for the IMAP protocol; Python's imaplib does not
        auto-quote CREATE arguments.
        """
        folder = self.to_wire(instance, user, folder)
        conn = self.connect(instance, user)
        status, res = conn.create(self._q(folder))
        if status != "OK" and "ALREADYEXISTS" not in str(res):
            raise RuntimeError(f"CREATE {folder!r} failed on {instance}/{user}: {res!r}")

    def seed_message(
        self,
        instance: str,
        user: str,
        folder: str,
        *,
        sender: str,
        to: str,
        subject: str,
        body: str,
        message_id: str | None = None,
        date: str | None = None,
        flags: Iterable[str] = (),
        extra_headers: dict[str, str] | None = None,
        html_body: str | None = None,
        attachments: Iterable[tuple[str, str, bytes]] = (),
        inline_attachments: Iterable[tuple[str, str, bytes]] = (),
        omit_message_id: bool = False,
        omit_date: bool = False,
        exact_size_bytes: int | None = None,
        internal_date: str | None = None,
    ) -> SeededMessage:
        folder = self.to_wire(instance, user, folder)
        """Append a message to `folder` and return metadata for assertions.

        `attachments` is a sequence of (filename, mime_type, bytes) tuples.

        Setting `omit_message_id=True` skips the Message-ID header
        entirely — used by 5-tuple-fallback scenarios that need a
        message identified solely by from/date/subject/size/4kb-hash,
        and by reply-builder scenarios that exercise the
        missing_message_id error path.

        Setting `omit_date=True` skips the Date header entirely so
        scenarios can verify how the server reacts to malformed
        sources (e.g. attribution lines built without a date prefix).
        """
        msg = EmailMessage(policy=SMTP)
        msg["From"] = sender
        msg["To"] = to
        msg["Subject"] = subject
        if not omit_message_id:
            if message_id is None:
                message_id = email.utils.make_msgid(domain="bdd.local")
            msg["Message-ID"] = message_id
        if not omit_date:
            msg["Date"] = date or email.utils.formatdate(localtime=False)
        for header, value in (extra_headers or {}).items():
            msg[header] = value
        if html_body and not body:
            msg.set_content(html_body, subtype="html")
        else:
            msg.set_content(body)
            if html_body:
                msg.add_alternative(html_body, subtype="html")
        # Collect raw-wire-filename substitutions to apply after the
        # message is serialized. EmailMessage normalizes any filename
        # we set on the object (RFC 2231-encoding it), which would hide
        # the RFC 2047 bug we are reproducing. So we attach with a
        # unique ASCII placeholder filename and byte-replace it in the
        # serialized output with the literal wire form the scenario
        # asked for — the placeholder is emitted verbatim as
        # `filename="<placeholder>"`, a stable replacement anchor.
        raw_wire_substitutions: list[tuple[str, str]] = []
        # Substitutions whose replacement bytes are non-ASCII (raw 8-bit
        # UTF-8 in a header). Kept separate from raw_wire_substitutions
        # because the replacement must be UTF-8 encoded, not ASCII.
        raw_8bit_substitutions: list[tuple[str, str]] = []
        for idx, att in enumerate(attachments):
            if isinstance(att, dict) and "raw_wire_filename" in att:
                mime_type = att["mime_type"]
                payload = att["payload"]
                maintype, _, subtype = mime_type.partition("/")
                placeholder = f"__RAW_WIRE_FILENAME_{idx}__"
                msg.add_attachment(
                    payload, maintype=maintype, subtype=subtype, filename=placeholder
                )
                raw_wire_substitutions.append(
                    (placeholder, att["raw_wire_filename"])
                )
                continue
            if isinstance(att, dict) and "raw_8bit_filename" in att:
                mime_type = att["mime_type"]
                payload = att["payload"]
                maintype, _, subtype = mime_type.partition("/")
                placeholder = f"__RAW_8BIT_FILENAME_{idx}__"
                msg.add_attachment(
                    payload, maintype=maintype, subtype=subtype, filename=placeholder
                )
                raw_8bit_substitutions.append(
                    (placeholder, att["raw_8bit_filename"])
                )
                continue
            filename, mime_type, payload = att
            maintype, _, subtype = mime_type.partition("/")
            msg.add_attachment(
                payload, maintype=maintype, subtype=subtype, filename=filename
            )
        for filename, mime_type, payload in inline_attachments:
            maintype, _, subtype = mime_type.partition("/")
            msg.add_attachment(
                payload,
                maintype=maintype,
                subtype=subtype,
                filename=filename,
                disposition="inline",
            )

        if exact_size_bytes is not None:
            current = len(msg.as_bytes())
            if current > exact_size_bytes:
                raise ValueError(
                    f"Message body ({current} bytes) exceeds target exact_size_bytes ({exact_size_bytes})"
                )
            if current < exact_size_bytes:
                diff = exact_size_bytes - current
                if diff >= 15:
                    msg["X-Pad-Base"] = "x"
                idx = 0
                while True:
                    diff = exact_size_bytes - len(msg.as_bytes())
                    if diff == 0:
                        break
                    if "X-Pad-Base" in msg and 0 < diff <= 40:
                        msg.replace_header("X-Pad-Base", msg["X-Pad-Base"] + ("x" * diff))
                        break
                    overhead = 12
                    chunk = min(50, max(1, diff - overhead))
                    name = f"X-P-{idx:04d}"
                    msg[name] = "x" * chunk
                    idx += 1

        raw = msg.as_bytes()
        for placeholder, wire in raw_wire_substitutions:
            raw = raw.replace(
                f'filename="{placeholder}"'.encode("ascii"),
                f'filename="{wire}"'.encode("ascii"),
            )
        for placeholder, wire in raw_8bit_substitutions:
            # Replacement carries raw 8-bit UTF-8 bytes so the header
            # parses to an email.header.Header, not a str — the exact
            # condition that triggers the .lower() crash.
            raw = raw.replace(
                f'filename="{placeholder}"'.encode("ascii"),
                'filename="'.encode("ascii") + wire.encode("utf-8") + b'"',
            )

        if exact_size_bytes is not None and len(raw) != exact_size_bytes:
            raise AssertionError(f"Could not pad message to exact size: {len(raw)} != {exact_size_bytes}")

        if internal_date:
            try:
                parsed_date = email.utils.parsedate_to_datetime(internal_date)
            except Exception:
                parsed_date = datetime.fromisoformat(internal_date)
            if parsed_date.tzinfo is None:
                parsed_date = parsed_date.replace(tzinfo=UTC)
            internaldate = parsed_date.astimezone(UTC).strftime('"%d-%b-%Y %H:%M:%S +0000"')
        elif date:
            parsed_date = email.utils.parsedate_to_datetime(date)
            if parsed_date.tzinfo is None:
                parsed_date = parsed_date.replace(tzinfo=UTC)
            internaldate = parsed_date.astimezone(UTC).strftime('"%d-%b-%Y %H:%M:%S +0000"')
        else:
            internaldate = datetime.now(tz=UTC).strftime('"%d-%b-%Y %H:%M:%S +0000"')
        flag_literal = "(" + " ".join(flags) + ")" if flags else None

        conn = self.connect(instance, user)
        quoted = self._q(folder)
        st_c, res_c = conn.create(quoted)
        if st_c != "OK" and "ALREADYEXISTS" not in str(res_c):
            pass
        status, response = conn.append(quoted, flag_literal, internaldate, raw)
        if status != "OK":
            raise RuntimeError(f"APPEND to {folder} failed: {response!r}")

        if omit_message_id:
            uid = self._lookup_uid_by_subject_and_from(conn, folder, subject, sender)
            message_id = ""
        else:
            if message_id is None:
                raise AssertionError("message_id is required")
            uid = self._lookup_uid_by_message_id(conn, folder, message_id)

        if exact_size_bytes is not None:
            conn.select(quoted)
            st, sz_data = conn.uid("FETCH", str(uid), "(RFC822.SIZE)")
            if st != "OK" or not sz_data or not sz_data[0]:
                raise AssertionError(f"Could not verify message size for uid {uid}")
            sz_str = sz_data[0].decode() if isinstance(sz_data[0], bytes) else str(sz_data[0])
            m_sz = re.search(r"RFC822\.SIZE\s+(\d+)", sz_str)
            if not m_sz or int(m_sz.group(1)) != exact_size_bytes:
                actual_sz = int(m_sz.group(1)) if m_sz else "unknown"
                raise AssertionError(f"Size verification failed: expected {exact_size_bytes}, got {actual_sz}")

        return SeededMessage(
            uid=uid,
            message_id=message_id,
            subject=subject,
            flags=tuple(flags),
        )

    def _lookup_uid_by_subject_and_from(
        self, conn: imaplib.IMAP4, folder: str, subject: str, sender: str
    ) -> int:
        """UID lookup for messages without a Message-ID by FROM and SUBJECT."""
        conn.select(self._q(folder))
        status, data = conn.uid("SEARCH", None, "FROM", sender, "SUBJECT", subject)
        if status == "OK" and data and data[0]:
            uids = [int(x) for x in data[0].split()]
            return max(uids) if uids else 0
        status, data = conn.uid("SEARCH", None, "ALL")
        if status == "OK" and data and data[0]:
            uids = [int(x) for x in data[0].split()]
            return max(uids) if uids else 0
        return 0

    @staticmethod
    def _q(folder: str) -> str:
        """Wrap a wire mailbox name in IMAP quoted-string form so
        spaces, brackets, and other reserved characters survive
        imaplib's raw argv pass-through."""
        if folder.startswith('"') and folder.endswith('"'):
            return folder
        escaped = folder.replace("\\", "\\\\").replace('"', '\\"')
        return f'"{escaped}"'

    def _lookup_uid_by_message_id(
        self, conn: imaplib.IMAP4, folder: str, message_id: str
    ) -> int:
        conn.select(self._q(folder))
        status, data = conn.uid("SEARCH", None, "HEADER", "Message-ID", message_id)
        if status != "OK" or not data or not data[0]:
            raise RuntimeError(
                f"Could not locate newly appended message {message_id} in {folder}"
            )
        return int(data[0].split()[-1])

    def append_verbatim(
        self,
        instance: str,
        user: str,
        folder: str,
        raw: bytes,
        flags: tuple[str, ...] = (),
    ) -> None:
        """APPEND raw bytes exactly as given, no header synthesis.

        `seed_message` composes a message from named fields; this one
        does not touch the bytes at all, which is what a scenario needs
        when the point is the absence of a header some other client
        would never have written.
        """
        folder = self.to_wire(instance, user, folder)
        conn = self.connect(instance, user)
        quoted = self._q(folder)
        conn.create(quoted)
        flag_literal = "(" + " ".join(flags) + ")" if flags else None
        internaldate = datetime.now(tz=UTC).strftime('"%d-%b-%Y %H:%M:%S +0000"')
        status, response = conn.append(
            quoted, flag_literal, internaldate, raw
        )
        if status != "OK":
            raise RuntimeError(f"Verbatim APPEND to {folder} failed: {response!r}")

    # ---------------------------------------------------------- inspection

    def search_by_message_id(
        self, instance: str, user: str, folder: str, message_id: str
    ) -> list[int]:
        """Independent IMAP SEARCH for a specific Message-ID; for Then-step use."""
        folder = self.to_wire(instance, user, folder)
        conn = self.connect(instance, user)
        status, _ = conn.select(self._q(folder))
        if status != "OK":
            return []
        status, data = conn.uid("SEARCH", None, "HEADER", "Message-ID", message_id)
        if status != "OK" or not data or not data[0]:
            return []
        return [int(token) for token in data[0].split()]

    def folder_uids(self, instance: str, user: str, folder: str) -> list[int]:
        folder = self.to_wire(instance, user, folder)
        conn = self.connect(instance, user)
        status, res = conn.select(self._q(folder))
        if status != "OK":
            raise RuntimeError(f"SELECT failed for {folder!r} on {instance}/{user}: {res!r}")
        status, data = conn.uid("SEARCH", None, "ALL")
        if status != "OK" or not data or not data[0]:
            return []
        return [int(token) for token in data[0].split()]

    def fetch_message(
        self, instance: str, user: str, folder: str, uid: int
    ) -> email.message.EmailMessage:
        """Fetch the parsed EmailMessage at <folder>/<uid>.

        Used by Then-steps that inspect message headers as a second
        channel (BDD Guidelines §13.2 Prüfung 1) — independent of the
        MCP server's view of the message.

        Raises if the UID is not present.
        """
        folder = self.to_wire(instance, user, folder)
        conn = self.connect(instance, user)
        conn.select(self._q(folder))
        status, data = conn.uid("FETCH", str(uid), "(RFC822)")
        if status != "OK" or not data or data[0] is None:
            raise RuntimeError(
                f"FETCH RFC822 on {folder!r} uid {uid} failed: {data!r}"
            )
        # imaplib returns a list of tuples for FETCH; the bytes payload
        # lives at index [0][1] for the matched UID.
        for item in data:
            if isinstance(item, tuple) and len(item) >= 2 and item[1]:
                raw = item[1]
                msg = email.message_from_bytes(raw, _class=EmailMessage)
                return msg
        raise RuntimeError(f"No RFC822 payload returned for uid {uid}")

    def fetch_raw(
        self, instance: str, user: str, folder: str, uid: int
    ) -> bytes:
        """Fetch the exact RFC822 bytes of <folder>/<uid> as stored."""
        folder = self.to_wire(instance, user, folder)
        conn = self.connect(instance, user)
        conn.select(self._q(folder))
        status, data = conn.uid("FETCH", str(uid), "(RFC822)")
        if status != "OK" or not data or data[0] is None:
            raise RuntimeError(
                f"FETCH RFC822 on {folder!r} uid {uid} failed: {data!r}"
            )
        for item in data:
            if isinstance(item, tuple) and len(item) >= 2 and item[1]:
                return item[1]
        raise RuntimeError(f"No RFC822 payload returned for uid {uid}")

    def fetch_header_block(self, instance: str, user: str, folder: str, uid: int) -> bytes:
        """Return the raw header block of <folder>/<uid> as stored.

        `BODY.PEEK[HEADER]` hands back the bytes the server keeps, before
        any parser decodes them, so a Then-step can inspect the wire form
        itself (e.g. that it is pure ASCII). Raises if the UID is absent.
        """
        wire = self.to_wire(instance, user, folder)
        conn = self.connect(instance, user)
        status, data = conn.select(self._q(wire), readonly=True)
        if status != "OK":
            raise RuntimeError(f"SELECT {folder!r} failed: {data!r}")
        status, data = conn.uid("FETCH", str(uid), "(BODY.PEEK[HEADER])")
        if status != "OK" or not data:
            raise RuntimeError(f"FETCH header of uid {uid} in {folder!r} failed: {data!r}")
        for item in data:
            if isinstance(item, tuple) and len(item) >= 2 and item[1]:
                return bytes(item[1])
        raise RuntimeError(f"No header block returned for uid {uid} in {folder!r}")

    def find_uids_by_decoded_subject(
        self, instance: str, user: str, folder: str, subject: str
    ) -> list[int]:
        """Locate all UIDs by RFC 2047-decoded Subject equality."""
        from email.header import decode_header, make_header

        matches: list[int] = []
        for uid in self.folder_uids(instance, user, folder):
            try:
                msg = self.fetch_message(instance, user, folder, uid)
            except RuntimeError:
                continue
            raw = msg.get("Subject", "") or ""
            decoded = str(make_header(decode_header(raw)))
            if decoded == subject:
                matches.append(uid)
        return matches

    def find_unique_uid_by_decoded_subject(
        self, instance: str, user: str, folder: str, subject: str
    ) -> int | None:
        """Locate exactly one UID by RFC 2047-decoded Subject equality."""
        hits = self.find_uids_by_decoded_subject(instance, user, folder, subject)
        if len(hits) == 1:
            return hits[0]
        if len(hits) > 1:
            raise AssertionError(
                f"Expected unique match for subject {subject!r} in {folder!r}, found {len(hits)}: {hits}"
            )
        return None

    def find_uid_by_decoded_subject(
        self, instance: str, user: str, folder: str, subject: str
    ) -> int | None:
        """Locate the first UID by RFC 2047-decoded Subject equality."""
        hits = self.find_uids_by_decoded_subject(instance, user, folder, subject)
        return hits[0] if hits else None

    def find_uid_by_in_reply_to(
        self, instance: str, user: str, folder: str, message_id: str
    ) -> int | None:
        """Locate a UID by exact In-Reply-To header match (ASCII-safe)."""
        for uid in self.folder_uids(instance, user, folder):
            try:
                msg = self.fetch_message(instance, user, folder, uid)
            except RuntimeError:
                continue
            if (msg.get("In-Reply-To", "") or "").strip() == message_id.strip():
                return uid
        return None

    def fetch_flags(
        self, instance: str, user: str, folder: str, uid: int
    ) -> tuple[str, ...]:
        uids = self.folder_uids(instance, user, folder)
        if uid not in uids:
            raise RuntimeError(f"UID {uid} does not exist in folder {folder!r}")
        folder_wire = self.to_wire(instance, user, folder)
        conn = self.connect(instance, user)
        conn.select(self._q(folder_wire))
        status, data = conn.uid("FETCH", str(uid), "(FLAGS)")
        if status != "OK" or not data or data[0] is None:
            raise RuntimeError(f"FETCH FLAGS failed for UID {uid} in folder {folder!r}")
        raw = data[0].decode() if isinstance(data[0], bytes) else str(data[0])
        # raw looks like: 'N (UID <n> FLAGS (\\Seen $tag))'
        start = raw.find("FLAGS (")
        if start == -1:
            return ()
        end = raw.find(")", start)
        return tuple(raw[start + len("FLAGS (") : end].split())

    def search_by_header(
        self, instance: str, user: str, folder: str, header: str, value: str
    ) -> list[int]:
        """Independent IMAP UID SEARCH HEADER with exact value comparison."""
        return self.search_header(instance, user, folder, header, value)

    def search_header(
        self, instance: str, user: str, folder: str, header: str, value: str
    ) -> list[int]:
        """Independent IMAP UID SEARCH HEADER, for Then-step use.

        Returns the UIDs whose `header` field holds exactly `value`.
        SEARCH HEADER matches a substring (RFC 3501 §6.4.4); each hit
        is fetched and compared so a step asking for equality gets it.
        A folder that cannot be selected fails loudly instead of
        reading as "zero results".
        """
        wire = self.to_wire(instance, user, folder)
        conn = self.connect(instance, user)
        status, data = conn.select(self._q(wire), readonly=True)
        if status != "OK":
            raise RuntimeError(f"SELECT {folder!r} failed: {data!r}")
        status, data = conn.uid("SEARCH", None, "HEADER", f'"{header}"', f'"{value}"')
        if status != "OK":
            raise RuntimeError(f"SEARCH HEADER {header} on {folder!r} failed: {data!r}")
        hits = [int(token) for token in (data[0] or b"").split()] if data else []
        exact: list[int] = []
        for uid in hits:
            status, fetched = conn.uid("FETCH", str(uid), "(BODY.PEEK[HEADER])")
            if status != "OK":
                raise RuntimeError(f"FETCH header of uid {uid} failed: {fetched!r}")
            raw = b"".join(
                item[1] for item in fetched or [] if isinstance(item, tuple) and len(item) >= 2
            )
            values = email.message_from_bytes(raw).get_all(header) or []
            if any(str(v).strip() == value for v in values):
                exact.append(uid)
        return exact

    def folder_special_use(self, instance: str, user: str) -> dict[str, tuple[str, ...]]:
        """Map every listed folder to the RFC 6154 special-use flags
        its LIST line carries (as the server reports them)."""
        conn = self.connect(instance, user)
        _, folder_lines = conn.list()
        reverse = {
            v: k for k, v in self._folder_wire_map.get((instance, user), {}).items()
        }
        result: dict[str, tuple[str, ...]] = {}
        for line in folder_lines or []:
            text = line.decode() if isinstance(line, bytes) else str(line)
            flags_part = text[text.find("(") + 1 : text.find(")")]
            special = tuple(
                flag for flag in flags_part.split() if flag.lower() in _SPECIAL_USE_FLAGS
            )
            name = self._parse_folder_name(line)
            result[reverse.get(name, name)] = special
        return result

    def list_folders(self, instance: str, user: str) -> list[str]:
        conn = self.connect(instance, user)
        _, folder_lines = conn.list()
        raw_names = [self._parse_folder_name(line) for line in folder_lines or []]
        reverse = {
            v: k
            for k, v in self._folder_wire_map.get(
                (instance, user), {}
            ).items()
        }
        return [reverse.get(n, n) for n in raw_names]

    @staticmethod
    def _parse_folder_name(line: bytes) -> str:
        # RFC 3501 LIST response: (flags) "sep" name
        # name is either an atom (unquoted, no spaces) or a quoted
        # string. rsplit(" ", 1) is wrong for quoted names with
        # spaces — "Tom &- Jerry" parses as just "Jerry" and the
        # folder cannot be deleted at reset, leaking into the next
        # scenario's hidden_folders_count.
        text = line.decode() if isinstance(line, bytes) else line
        text = text.rstrip()
        if text.endswith('"'):
            start = text.rfind('"', 0, -1)
            if start >= 0:
                return text[start + 1 : -1]
        return text.rsplit(" ", 1)[-1]
