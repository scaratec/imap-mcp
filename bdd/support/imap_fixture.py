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
import time
from dataclasses import dataclass, field
from email.message import EmailMessage
from typing import Iterable

# Password is the same for all test users (see docker/dovecot/users/README.md).
TEST_PASSWORD = "test123"

# Per-instance user list. Keep in sync with docker/dovecot/users/*.passwd.
_USERS_BY_INSTANCE: dict[str, list[str]] = {
    "imap-a": ["gupta", "osthues"],
    "imap-b": ["personal", "archive"],
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
}


def resolve_account(account_id: str) -> tuple[str, str]:
    """Return the (instance, user) pair backing a feature-file account id.

    Raises KeyError if the account id is not part of the test harness's
    planned fixture. This is intentional: if a new feature needs a new
    account, the harness must be updated deliberately, not by accident.
    """
    return _ACCOUNT_ID_TO_INSTANCE_USER[account_id]

# System folders that the BDD fixture must not delete during reset.
# Only INBOX is truly system-level: Dovecot auto-creates it on LOGIN
# and won't let us DELETE it. Drafts/Sent/Trash are configured
# `auto=no` in the fixture (see docker/dovecot/conf/dovecot.conf) so
# they only exist after a scenario explicitly creates them — and must
# be cleared out at reset_user time so they do not leak into the
# next scenario's hidden_folders_count.
_SYSTEM_FOLDERS = frozenset({"INBOX"})


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
        conn.login(user, TEST_PASSWORD)
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

    def reset_user(self, instance: str, user: str) -> None:
        """Delete every non-system folder and empty all system folders."""
        conn = self.connect(instance, user)
        _, folder_lines = conn.list()
        folders = [self._parse_folder_name(line) for line in folder_lines or []]

        # Empty system folders in place.
        for folder in folders:
            if folder in _SYSTEM_FOLDERS:
                self._empty_folder(conn, folder)

        # Delete non-system folders, deepest first so parents go last.
        for folder in sorted(
            (f for f in folders if f not in _SYSTEM_FOLDERS),
            key=lambda f: f.count("/"),
            reverse=True,
        ):
            try:
                conn.delete(folder)
            except imaplib.IMAP4.error:
                pass

    def _empty_folder(self, conn: imaplib.IMAP4, folder: str) -> None:
        status, _ = conn.select(folder)
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

    # ----------------------------------------------------------- creation

    def create_folder(self, instance: str, user: str, folder: str) -> None:
        """Create a folder (and any implicit parents) if missing."""
        conn = self.connect(instance, user)
        conn.create(folder)

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
        attachments: Iterable[tuple[str, str, bytes]] = (),
        omit_message_id: bool = False,
    ) -> SeededMessage:
        """Append a message to `folder` and return metadata for assertions.

        `attachments` is a sequence of (filename, mime_type, bytes) tuples.

        Setting `omit_message_id=True` skips the Message-ID header
        entirely — used by 5-tuple-fallback scenarios that need a
        message identified solely by from/date/subject/size/4kb-hash.
        """
        msg = EmailMessage()
        msg["From"] = sender
        msg["To"] = to
        msg["Subject"] = subject
        if not omit_message_id:
            if message_id is None:
                message_id = email.utils.make_msgid(domain="bdd.local")
            msg["Message-ID"] = message_id
        msg["Date"] = date or email.utils.formatdate(localtime=False)
        for header, value in (extra_headers or {}).items():
            msg[header] = value
        msg.set_content(body)
        for filename, mime_type, payload in attachments:
            maintype, _, subtype = mime_type.partition("/")
            msg.add_attachment(
                payload, maintype=maintype, subtype=subtype, filename=filename
            )

        raw = msg.as_bytes()
        internaldate = imaplib.Time2Internaldate(time.time())
        flag_literal = "(" + " ".join(flags) + ")" if flags else None

        conn = self.connect(instance, user)
        conn.create(folder)
        status, response = conn.append(folder, flag_literal, internaldate, raw)
        if status != "OK":
            raise RuntimeError(f"APPEND to {folder} failed: {response!r}")

        if omit_message_id:
            uid = self._lookup_uid_by_subject_and_from(conn, folder, subject, sender)
            message_id = ""
        else:
            assert message_id is not None
            uid = self._lookup_uid_by_message_id(conn, folder, message_id)
        return SeededMessage(
            uid=uid,
            message_id=message_id,
            subject=subject,
            flags=tuple(flags),
        )

    def _lookup_uid_by_subject_and_from(
        self, conn: imaplib.IMAP4, folder: str, subject: str, sender: str
    ) -> int:
        """UID lookup for messages without a Message-ID — returns the
        highest UID currently in the folder.

        For the BDD scenarios that exercise `omit_message_id` the
        message we just appended is the most recent one in the
        folder; that's the simplest reliable identifier when no
        Message-ID is available.
        """
        _ = (subject, sender)
        conn.select(folder)
        status, data = conn.uid("SEARCH", None, "ALL")
        if status != "OK" or not data or not data[0]:
            return 0
        uids = [int(x) for x in data[0].split()]
        return max(uids) if uids else 0

    def _lookup_uid_by_message_id(
        self, conn: imaplib.IMAP4, folder: str, message_id: str
    ) -> int:
        conn.select(folder)
        status, data = conn.uid("SEARCH", None, "HEADER", "Message-ID", message_id)
        if status != "OK" or not data or not data[0]:
            raise RuntimeError(
                f"Could not locate newly appended message {message_id} in {folder}"
            )
        return int(data[0].split()[-1])

    # ---------------------------------------------------------- inspection

    def search_by_message_id(
        self, instance: str, user: str, folder: str, message_id: str
    ) -> list[int]:
        """Independent IMAP SEARCH for a specific Message-ID; for Then-step use."""
        conn = self.connect(instance, user)
        status, _ = conn.select(folder)
        if status != "OK":
            return []
        status, data = conn.uid("SEARCH", None, "HEADER", "Message-ID", message_id)
        if status != "OK" or not data or not data[0]:
            return []
        return [int(token) for token in data[0].split()]

    def folder_uids(self, instance: str, user: str, folder: str) -> list[int]:
        conn = self.connect(instance, user)
        status, _ = conn.select(folder)
        if status != "OK":
            return []
        status, data = conn.uid("SEARCH", None, "ALL")
        if status != "OK" or not data or not data[0]:
            return []
        return [int(token) for token in data[0].split()]

    def fetch_flags(
        self, instance: str, user: str, folder: str, uid: int
    ) -> tuple[str, ...]:
        conn = self.connect(instance, user)
        conn.select(folder)
        status, data = conn.uid("FETCH", str(uid), "(FLAGS)")
        if status != "OK" or not data or data[0] is None:
            return ()
        raw = data[0].decode() if isinstance(data[0], bytes) else str(data[0])
        # raw looks like: 'N (UID <n> FLAGS (\\Seen $tag))'
        start = raw.find("FLAGS (")
        if start == -1:
            return ()
        end = raw.find(")", start)
        return tuple(raw[start + len("FLAGS (") : end].split())

    def list_folders(self, instance: str, user: str) -> list[str]:
        conn = self.connect(instance, user)
        _, folder_lines = conn.list()
        return [self._parse_folder_name(line) for line in folder_lines or []]

    @staticmethod
    def _parse_folder_name(line: bytes) -> str:
        # RFC 3501 LIST response: (flags) "/" "Folder Name"
        text = line.decode() if isinstance(line, bytes) else line
        return text.rsplit(" ", 1)[-1].strip('"')
