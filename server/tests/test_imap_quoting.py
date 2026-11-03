"""Unit tests for what the server puts on the IMAP wire for names.

Found by the acceptance test against a real German Gmail account after
2.2.0: Gmail answers `+X-GM-LABELS ("\\Trash")` with `BAD Could not
parse command` (a backslash inside an IMAP quoted string is an escape,
RFC 3501 §4.3), and the saga's delete step selected the drafts folder
under its UTF-8 name instead of `[Gmail]/Entw&APw-rfe`.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from imap_mcp import imap_core, saga
from imap_mcp.imap_core import (
    _parse_gmail_label_list,
    _quote_gmail_label,
    _quote_mailbox,
)

# ------------------------------------------------------------ label quoting


@pytest.mark.parametrize(
    "label",
    ["\\Trash", "\\Spam", "\\Inbox", "\\Drafts", "\\Sent", "\\Starred", "\\Important"],
)
def test_system_label_travels_as_flag_atom(label: str) -> None:
    assert _quote_gmail_label(label) == label


@pytest.mark.parametrize("label", ["Hornbach", "Rechnungseing&AOQ-nge", "a/b.c-d_e"])
def test_atom_label_is_sent_unquoted(label: str) -> None:
    assert _quote_gmail_label(label) == label


@pytest.mark.parametrize(
    ("label", "wire"),
    [
        ("My Label", '"My Label"'),
        ('say "hi"', '"say \\"hi\\""'),
        ("back\\slash", '"back\\\\slash"'),
        ("[Gmail]/Foo", '"[Gmail]/Foo"'),
        ("", '""'),
        ("\\", '"\\\\"'),
        ("\\Not An Atom", '"\\\\Not An Atom"'),
    ],
)
def test_other_labels_are_rfc3501_quoted_strings(label: str, wire: str) -> None:
    assert _quote_gmail_label(label) == wire


def test_quoted_string_never_holds_a_bare_backslash() -> None:
    wire = _quote_gmail_label("a\\b c")
    body = wire[1:-1]
    i = 0
    while i < len(body):
        if body[i] == "\\":
            assert body[i + 1] in ("\\", '"')
            i += 2
        else:
            i += 1


@pytest.mark.parametrize(
    "labels",
    [
        ["\\Inbox", "Hornbach", "My Label"],
        ['say "hi"', "back\\slash", "\\Important"],
    ],
)
def test_parse_reads_back_what_quote_writes(labels: list[str]) -> None:
    raw = " ".join(_quote_gmail_label(label) for label in labels)
    assert _parse_gmail_label_list(raw) == labels


# ---------------------------------------------------------- mailbox quoting


@pytest.mark.parametrize(
    ("name", "wire"),
    [
        ("[Gmail]/Entwürfe", '"[Gmail]/Entw&APw-rfe"'),
        ("[Gmail]/Papierkorb", '"[Gmail]/Papierkorb"'),
        ("INBOX", '"INBOX"'),
        ("A&B", '"A&-B"'),
        ('Say "x"', '"Say \\"x\\""'),
        ("back\\slash", '"back\\\\slash"'),
    ],
)
def test_mailbox_is_mutf7_encoded_and_quoted(name: str, wire: str) -> None:
    assert _quote_mailbox(name) == wire


# --------------------------------------------------- commands on the wire


class _FakeIMAP:
    """Records every command; answers OK unless told otherwise."""

    def __init__(self, capabilities: set[str] | None = None) -> None:
        self.sent: list[tuple[str, ...]] = []
        self.capabilities = capabilities or set()

    async def select(self, mailbox: str) -> tuple[str, list[bytes]]:
        self.sent.append(("SELECT", mailbox))
        return "OK", []

    async def uid(self, command: str, *args: str) -> tuple[str, list[bytes]]:
        self.sent.append(("UID", command.upper(), *args))
        return "OK", []

    async def expunge(self) -> tuple[str, list[bytes]]:
        self.sent.append(("EXPUNGE",))
        return "OK", []

    async def logout(self) -> None:
        self.sent.append(("LOGOUT",))

    def has_capability(self, capability: str) -> bool:
        return capability in self.capabilities


@pytest.fixture
def fake_imap(monkeypatch: pytest.MonkeyPatch) -> _FakeIMAP:
    fake = _FakeIMAP(capabilities={"UIDPLUS"})

    async def open_imap(account: Any, *, timeout: int = 10) -> _FakeIMAP:
        return fake

    async def authenticate(imap: Any, account: Any, password: str) -> None:
        return None

    monkeypatch.setattr(imap_core, "_open_imap", open_imap)
    monkeypatch.setattr(imap_core, "_authenticate_imap", authenticate)
    return fake


ACCOUNT: Any = SimpleNamespace(id="acct")


async def test_saga_delete_selects_the_encoded_mailbox(fake_imap: _FakeIMAP) -> None:
    await saga._delete_source(ACCOUNT, "pw", "[Gmail]/Entwürfe", 7)
    assert fake_imap.sent[0] == ("SELECT", '"[Gmail]/Entw&APw-rfe"')


async def test_label_swap_sends_system_labels_as_atoms(fake_imap: _FakeIMAP) -> None:
    await imap_core.gmail_label_swap(ACCOUNT, "pw", "INBOX", 5, "\\Inbox", "My Label")
    stores = [c for c in fake_imap.sent if c[:2] == ("UID", "STORE")]
    assert stores == [
        ("UID", "STORE", "5", "+X-GM-LABELS", '("My Label")'),
        ("UID", "STORE", "5", "-X-GM-LABELS", "(\\Inbox)"),
    ]


@pytest.mark.parametrize("label", ["\\Trash", "\\Spam"])
async def test_label_swap_to_trash_or_spam_removes_nothing(
    fake_imap: _FakeIMAP, label: str
) -> None:
    await imap_core.gmail_label_swap(ACCOUNT, "pw", "[Gmail]/Entwürfe", 3, "\\Drafts", label)
    assert fake_imap.sent[0] == ("SELECT", '"[Gmail]/Entw&APw-rfe"')
    mutating = [c for c in fake_imap.sent if c[0] == "UID"]
    assert mutating == [("UID", "STORE", "3", "+X-GM-LABELS", f"({label})")]


async def test_draft_leaves_drafts_by_deleted_and_uid_expunge(fake_imap: _FakeIMAP) -> None:
    await imap_core.gmail_label_swap(ACCOUNT, "pw", "[Gmail]/Entwürfe", 3, "\\Drafts", "Angebote")
    mutating = [c for c in fake_imap.sent if c[0] == "UID"]
    assert mutating == [
        ("UID", "STORE", "3", "+X-GM-LABELS", "(Angebote)"),
        ("UID", "STORE", "3", "+FLAGS", "(\\Deleted)"),
        ("UID", "EXPUNGE", "3"),
    ]


async def test_draft_without_uidplus_relies_on_auto_expunge(fake_imap: _FakeIMAP) -> None:
    fake_imap.capabilities = set()
    await imap_core.gmail_label_swap(ACCOUNT, "pw", "[Gmail]/Entwürfe", 3, "\\Drafts", "Angebote")
    assert ("UID", "EXPUNGE", "3") not in fake_imap.sent
    assert ("EXPUNGE",) not in fake_imap.sent


# --------------------------------------------------- capability refresh


class _FakeProtocol:
    def __init__(self, after: set[str] | None) -> None:
        self.capabilities = {"IMAP4rev1", "AUTH=PLAIN"}
        self._after = after

    async def capability(self) -> None:
        if self._after is None:
            self.capabilities = {"garbage"}
            raise RuntimeError("server not IMAP4 compliant")
        self.capabilities = set(self._after)


async def test_capabilities_are_reread_after_authentication() -> None:
    client: Any = SimpleNamespace(
        protocol=_FakeProtocol({"IMAP4rev1", "MOVE", "UIDPLUS"}), timeout=5
    )
    await imap_core._refresh_capabilities(client)
    assert {"MOVE", "UIDPLUS"} <= client.protocol.capabilities


async def test_failed_refresh_keeps_the_known_capabilities() -> None:
    client: Any = SimpleNamespace(protocol=_FakeProtocol(None), timeout=5)
    await imap_core._refresh_capabilities(client)
    assert client.protocol.capabilities == {"IMAP4rev1", "AUTH=PLAIN"}


@pytest.mark.parametrize(
    "lines",
    [
        [b"CAPABILITY IMAP4rev1 MOVE UIDPLUS", b"user authenticated (Success)"],
        [b"[CAPABILITY IMAP4rev1 MOVE UIDPLUS] Logged in"],
    ],
)
async def test_capabilities_sent_with_authentication_need_no_command(lines: list[bytes]) -> None:
    protocol = _FakeProtocol(None)
    client: Any = SimpleNamespace(protocol=protocol, timeout=5)
    await imap_core._refresh_capabilities(client, lines)
    assert protocol.capabilities == {"IMAP4rev1", "MOVE", "UIDPLUS"}
