"""In-memory message store with Gmail label semantics.

A Message is a single RFC822 blob that carries a stable X-GM-MSGID,
a thread ID (X-GM-THRID), a set of labels, and a set of IMAP flags.
A "folder" is a label projection: selecting folder F shows all
messages whose label set contains F.  UIDs are per-folder and stable
within a session (mapping: (label, gm_msgid) -> uid).

[Gmail]/All Mail shows every non-Trash message.
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass, field
from typing import Any


_msgid_counter = itertools.count(1_800_000_000_000_000_000)


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


SYSTEM_LABELS = frozenset({
    "\\Inbox", "\\Sent", "\\Drafts", "\\Trash",
    "\\Spam", "\\Starred", "\\Important",
})

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

    def reset(self) -> None:
        self.messages.clear()
        self._uid_maps.clear()
        self._uid_counters.clear()
        self._created_folders.clear()
        self._localized_folders.clear()
        self.total_connections = 0
        self.record_store_operations = False
        self.store_operations.clear()
        self.next_store_rejection = None

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
        self.messages.append(msg)
        for label in msg.labels:
            self._assign_uid(self._label_to_folder(label), msg.gm_msgid)
        self._assign_uid("[Gmail]/All Mail", msg.gm_msgid)

    def new_msgid(self) -> int:
        return next(_msgid_counter)

    def _resolve_localized(self, folder: str) -> str:
        """Map a localized folder name back to the canonical name."""
        for canonical, (localized, _flags) in self._localized_folders.items():
            if folder == localized:
                return canonical
        return folder

    def messages_in_folder(self, folder: str) -> list[tuple[int, Message]]:
        folder = self._resolve_localized(folder)
        label = FOLDER_TO_LABEL.get(folder, folder)
        if label == "__ALL_MAIL__":
            result = []
            for msg in self.messages:
                if "\\Trash" not in msg.labels:
                    uid = self._get_uid(folder, msg.gm_msgid)
                    if uid is not None:
                        result.append((uid, msg))
            return result
        result = []
        for msg in self.messages:
            if label in msg.labels or folder in msg.labels:
                uid = self._get_uid(folder, msg.gm_msgid)
                if uid is not None:
                    result.append((uid, msg))
        return result

    def message_by_uid(self, folder: str, uid: int) -> Message | None:
        uid_map = self._uid_maps.get(folder, {})
        for gm_msgid, mapped_uid in uid_map.items():
            if mapped_uid == uid:
                for msg in self.messages:
                    if msg.gm_msgid == gm_msgid:
                        return msg
        return None

    def uid_for(self, folder: str, gm_msgid: int) -> int | None:
        return self._get_uid(folder, gm_msgid)

    def add_label(self, msg: Message, label: str) -> None:
        msg.labels.add(label)
        folder = self._label_to_folder(label)
        self._assign_uid(folder, msg.gm_msgid)

    def remove_label(self, msg: Message, label: str) -> None:
        # Drop the per-folder UID mapping too, mirroring real Gmail:
        # once the label is gone, the message is no longer visible in
        # that folder's UID namespace.
        msg.labels.discard(label)
        folder = self._label_to_folder(label)
        uid_map = self._uid_maps.get(folder)
        if uid_map is not None:
            uid_map.pop(msg.gm_msgid, None)

    def labels_visible_from(self, folder: str, msg: Message) -> list[str]:
        """Labels as Gmail shows them from a given folder's perspective."""
        current_label = FOLDER_TO_LABEL.get(folder, folder)
        if current_label == "__ALL_MAIL__":
            return sorted(msg.labels)
        return sorted(l for l in msg.labels if l != current_label)

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
