"""In-memory message store with Gmail label semantics.

A Message is a single RFC822 blob that carries a stable X-GM-MSGID,
a thread ID (X-GM-THRID), a set of labels, and a set of IMAP flags.
A "folder" is a label projection: selecting folder F shows all
messages whose label set contains F.  UIDs are per-folder and stable
within a session (mapping: (label, gm_msgid) -> uid).

[Gmail]/All Mail shows every message not in Trash or Spam; a message
in Trash (or Spam) is shown in that mailbox only.
"""

from __future__ import annotations

import base64
import itertools
from dataclasses import dataclass, field
from typing import Any


_msgid_counter = itertools.count(1_800_000_000_000_000_000)


def decode_mutf7(wire: str) -> str:
    """RFC 3501 §5.1.3 Modified UTF-7 → UTF-8 (mock-side, vendored).

    The mock is a standalone package and intentionally does not import
    from sc-imap-mcp; this mirrors imap_core.decode_mutf7 so the mock
    can accept the same wire form real Gmail does and resolve labels
    back to their UTF-8 form internally.
    """
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


@dataclass
class Message:
    gm_msgid: int
    gm_thrid: int
    rfc822: bytes
    labels: set[str] = field(default_factory=set)
    flags: set[str] = field(default_factory=set)
    message_id: str | None = None
    from_addr: str = ""
    to_addr: str = ""
    subject: str = ""
    date: str = ""


@dataclass(frozen=True)
class CommandRecord:
    """One IMAP command as the mock received it.

    `mailbox` is the mailbox argument exactly as it travelled on the
    wire (unquoted, still Modified UTF-7), or None for commands that do
    not address a mailbox. `user` is the LOGIN name of the connection
    that sent it, so a test can tell its own direct commands apart
    from the server's.
    """

    user: str | None
    command: str
    mailbox: str | None


@dataclass(frozen=True)
class LabelStoreRecord:
    """One `UID STORE ... X-GM-LABELS` as the mock parsed it.

    `labels` is the label list after RFC 3501 parsing (quoted strings
    unescaped, atoms verbatim), or None when the list did not parse and
    the command was answered BAD, as real Gmail does. `status` is the
    tagged response the mock sent.
    """

    user: str | None
    op: str
    raw: str
    labels: tuple[str, ...] | None
    status: str


SYSTEM_LABELS = frozenset({
    "\\Inbox", "\\Sent", "\\Drafts", "\\Trash",
    "\\Spam", "\\Starred", "\\Important",
})

# Real Gmail does not expose the drafts mailbox as an X-GM-LABELS
# label: a draft shows `X-GM-LABELS ()`, and STORE of `\Drafts` is
# answered OK without effect. Internally the mock still marks drafts
# with this label to know which messages the drafts mailbox shows.
HIDDEN_LABELS = frozenset({"\\Drafts"})

# A message carrying one of these labels is shown in that mailbox only;
# Gmail removes it from every other view at once. Trash wins over Spam.
EXCLUSIVE_LABELS = ("\\Trash", "\\Spam")

FOLDER_TO_LABEL: dict[str, str] = {
    "INBOX": "\\Inbox",
    "[Gmail]/All Mail": "__ALL_MAIL__",
    "[Gmail]/Sent Mail": "\\Sent",
    "[Gmail]/Drafts": "\\Drafts",
    "[Gmail]/Trash": "\\Trash",
    "[Gmail]/Spam": "\\Spam",
    "[Gmail]/Starred": "\\Starred",
    "[Gmail]/Important": "\\Important",
}

LABEL_TO_FOLDER: dict[str, str] = {v: k for k, v in FOLDER_TO_LABEL.items() if v != "__ALL_MAIL__"}


class GmailState:
    """Holds all messages and manages UID assignment per folder."""

    def __init__(self) -> None:
        self.messages: list[Message] = []
        self._uid_maps: dict[str, dict[int, int]] = {}
        self._uid_counters: dict[str, int] = {}
        self._uidvalidity: dict[str, int] = {}
        self._created_folders: set[str] = set()
        self.password: str = "test"
        self.total_connections: int = 0
        self._localized_folders: dict[str, tuple[str, str]] = {}
        # Test hooks: when record_store_operations is True the server
        # appends every X-GM-LABELS STORE op as (uid, '+'|'-', label)
        # so scenarios can verify the ADD-before-REMOVE order.
        self.record_store_operations: bool = False
        self.store_operations: list[tuple[int, str, str]] = []
        # Test hook: (status, text) injected into the next STORE response
        # to simulate a backend rejection (e.g. OVERQUOTA).
        self.next_store_rejection: tuple[str, str] | None = None
        # Every command received, in order, across all connections.
        self.command_log: list[CommandRecord] = []
        # Every X-GM-LABELS STORE with its parse result and status.
        self.label_store_log: list[LabelStoreRecord] = []

    def reset(self) -> None:
        self.label_store_log.clear()
        self.messages.clear()
        self._uid_maps.clear()
        self._uid_counters.clear()
        self._created_folders.clear()
        self._localized_folders.clear()
        self.total_connections = 0
        self.record_store_operations = False
        self.store_operations.clear()
        self.next_store_rejection = None
        self.command_log.clear()

    def set_localized_folders(
        self, mapping: list[tuple[str, str, str]]
    ) -> None:
        """Configure folder localization.

        Each entry is (canonical, localized, flags).  The LIST command
        will emit `localized` instead of `canonical`, but carry the
        same RFC 6154 special-use flags so the client can resolve
        the canonical name.
        """
        self._localized_folders.clear()
        for canonical, localized, flags in mapping:
            self._localized_folders[canonical] = (localized, flags)

    def create_folder(self, name: str) -> None:
        self._created_folders.add(name)

    def add_message(self, msg: Message) -> None:
        # Real Gmail: every message in the drafts mailbox carries \Draft.
        if "\\Drafts" in msg.labels:
            msg.flags.add("\\Draft")
        self.messages.append(msg)
        for label in msg.labels:
            folder = self._label_to_folder(label)
            if self._visible_in(folder, msg):
                self._assign_uid(folder, msg.gm_msgid)
        if self._visible_in("[Gmail]/All Mail", msg):
            self._assign_uid("[Gmail]/All Mail", msg.gm_msgid)

    def delete_message(self, msg: Message) -> None:
        """Remove a message for good (expunged from Trash or Spam)."""
        self.messages = [m for m in self.messages if m.gm_msgid != msg.gm_msgid]
        for uid_map in self._uid_maps.values():
            uid_map.pop(msg.gm_msgid, None)

    def uid_was_assigned(self, folder: str, uid: int) -> bool:
        """True when `uid` was handed out in `folder` at some point."""
        return 0 < uid <= self._uid_counters.get(folder, 0)

    def new_msgid(self) -> int:
        return next(_msgid_counter)

    def _resolve_localized(self, folder: str) -> str:
        """Map a localized folder name back to the canonical name."""
        for canonical, (localized, _flags) in self._localized_folders.items():
            if folder == localized:
                return canonical
        return folder

    def mailbox_for_wire_name(self, wire: str) -> str | None:
        """Resolve a mailbox name received on the wire to internal state.

        Real Gmail knows a localized system folder only under its
        localized name: on a German account `[Gmail]/Drafts` does not
        exist. So a canonical name whose folder is localized resolves
        to None (no such mailbox); the localized name resolves to the
        canonical key the state uses internally; anything else passes
        through.
        """
        if wire in self._localized_folders:
            return None
        return self._resolve_localized(wire)

    def folder_exists(self, folder: str) -> bool:
        """True when `folder` (internal name) is a mailbox Gmail would list."""
        if folder.upper() == "INBOX":
            return True
        known = set(self.all_folders())
        return folder in known or decode_mutf7(folder) in known

    def _visible_in(self, folder: str, msg: Message) -> bool:
        """True when Gmail shows `msg` in the mailbox `folder` (internal name).

        A message in Trash (or Spam) is shown there only; All Mail
        shows everything else; any other mailbox shows the messages
        carrying its label.
        """
        label = FOLDER_TO_LABEL.get(folder, folder)
        for exclusive in EXCLUSIVE_LABELS:
            if exclusive in msg.labels:
                return label == exclusive
        if label == "__ALL_MAIL__":
            return True
        return label in msg.labels or folder in msg.labels

    def messages_in_folder(self, folder: str) -> list[tuple[int, Message]]:
        folder = self._resolve_localized(folder)
        result = []
        for msg in self.messages:
            if self._visible_in(folder, msg):
                uid = self._get_uid(folder, msg.gm_msgid)
                if uid is not None:
                    result.append((uid, msg))
        return result

    def message_by_uid(self, folder: str, uid: int) -> Message | None:
        uid_map = self._uid_maps.get(folder, {})
        for gm_msgid, mapped_uid in uid_map.items():
            if mapped_uid == uid:
                for msg in self.messages:
                    if msg.gm_msgid == gm_msgid and self._visible_in(folder, msg):
                        return msg
        return None

    def uid_for(self, folder: str, gm_msgid: int) -> int | None:
        return self._get_uid(folder, gm_msgid)

    def add_label(self, msg: Message, label: str) -> None:
        msg.labels.add(label)
        folder = self._label_to_folder(label)
        self._assign_uid(folder, msg.gm_msgid)
        self._sync_uids(msg)

    def remove_label(self, msg: Message, label: str) -> None:
        # Drop the per-folder UID mapping too, mirroring real Gmail:
        # once the label is gone, the message is no longer visible in
        # that folder's UID namespace.
        msg.labels.discard(label)
        folder = self._label_to_folder(label)
        uid_map = self._uid_maps.get(folder)
        if uid_map is not None:
            uid_map.pop(msg.gm_msgid, None)
        self._sync_uids(msg)

    def leave_mailbox(self, msg: Message, folder: str) -> None:
        """Take `msg` out of mailbox `folder` (internal name).

        Unlike `remove_label` this also works for the drafts mailbox,
        whose label Gmail does not expose to STORE: it is how a
        `\\Deleted` message leaves a mailbox under auto-expunge.
        """
        label = FOLDER_TO_LABEL.get(folder, folder)
        msg.labels.discard(label)
        msg.labels.discard(folder)
        uid_map = self._uid_maps.get(folder)
        if uid_map is not None:
            uid_map.pop(msg.gm_msgid, None)
        self._sync_uids(msg)

    def _sync_uids(self, msg: Message) -> None:
        """Align the per-mailbox UID maps with where `msg` is now shown.

        A mailbox the message left loses its UID; a mailbox it
        (re)appears in hands out a fresh UID, never the old one.
        """
        for folder, uid_map in self._uid_maps.items():
            if msg.gm_msgid in uid_map and not self._visible_in(folder, msg):
                uid_map.pop(msg.gm_msgid)
        folders = {self._label_to_folder(label) for label in msg.labels}
        folders.add("[Gmail]/All Mail")
        for folder in folders:
            if self._visible_in(folder, msg) and self._get_uid(folder, msg.gm_msgid) is None:
                self._assign_uid(folder, msg.gm_msgid)

    def labels_visible_from(self, folder: str, msg: Message) -> list[str]:
        """Labels as Gmail shows them from a given folder's perspective."""
        current_label = FOLDER_TO_LABEL.get(folder, folder)
        shown = [label for label in msg.labels if label not in HIDDEN_LABELS]
        if current_label == "__ALL_MAIL__":
            return sorted(shown)
        return sorted(label for label in shown if label != current_label)

    def all_folders(self) -> list[str]:
        system = [
            "INBOX",
            "[Gmail]/All Mail", "[Gmail]/Drafts", "[Gmail]/Important",
            "[Gmail]/Sent Mail", "[Gmail]/Spam", "[Gmail]/Starred",
            "[Gmail]/Trash",
        ]
        custom_labels = self._all_labels() | self._created_folders
        custom = sorted(
            label for label in custom_labels
            if label not in SYSTEM_LABELS
            and label not in FOLDER_TO_LABEL.values()
            and label not in system
        )
        return system + custom

    def uidvalidity(self, folder: str) -> int:
        return self._uidvalidity.setdefault(folder, 1)

    def _all_labels(self) -> set[str]:
        labels: set[str] = set()
        for msg in self.messages:
            labels.update(msg.labels)
        return labels

    def _label_to_folder(self, label: str) -> str:
        return LABEL_TO_FOLDER.get(label, label)

    def _assign_uid(self, folder: str, gm_msgid: int) -> int:
        uid_map = self._uid_maps.setdefault(folder, {})
        if gm_msgid in uid_map:
            return uid_map[gm_msgid]
        counter = self._uid_counters.get(folder, 0) + 1
        self._uid_counters[folder] = counter
        uid_map[gm_msgid] = counter
        return counter

    def _get_uid(self, folder: str, gm_msgid: int) -> int | None:
        uid_map = self._uid_maps.get(folder)
        if uid_map is None:
            return None
        return uid_map.get(gm_msgid)
