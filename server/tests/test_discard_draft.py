"""Unit tests for discard_draft (ADR 0034).

The BDD suite covers the tool end to end against Dovecot and the Gmail
mock. These tests pin what is cheaper to pin here: the `\\Trash` lookup
on LIST results, the tool surface entry, and that every refusal is
decided before the first mutating IMAP command.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from imap_mcp.config import (
    Account,
    AccountsFile,
    Caller,
    CallersFile,
    Configuration,
    FolderPolicy,
    PolicyFile,
)
from imap_mcp.dispatch import _build_tool_list
from imap_mcp.handlers import drafts
from imap_mcp.handlers._common import WRITE_TOOL_CAP, error_envelope
from imap_mcp.imap_core import FolderInfo, find_trash_folder
from imap_mcp.policy import PolicyDecisionPoint

ACCOUNT = "acct"
DRAFT_ID = "8f14e45f-ceea-4e7a-9d2b-0a1c3b5d7e90"


# ------------------------------------------------------------ trash lookup


def test_trash_folder_is_found_by_its_special_use_flag() -> None:
    folders = [
        FolderInfo(path="INBOX", message_count=0, flags="\\HasNoChildren"),
        FolderInfo(path="Papierkorb", message_count=0, flags="\\HasNoChildren \\Trash"),
        FolderInfo(path="Trash", message_count=0, flags="\\HasNoChildren"),
    ]
    assert find_trash_folder(folders) == "Papierkorb"


def test_trash_flag_compares_case_insensitively() -> None:
    folders = [FolderInfo(path="Deleted Items", message_count=0, flags="\\TRASH")]
    assert find_trash_folder(folders) == "Deleted Items"


def test_no_trash_flag_means_no_trash_folder() -> None:
    folders = [
        FolderInfo(path="INBOX", message_count=0, flags=""),
        FolderInfo(path="INBOX/Drafts", message_count=0, flags="\\Drafts"),
        FolderInfo(path="Trash", message_count=0, flags="\\HasNoChildren"),
    ]
    assert find_trash_folder(folders) is None


# ------------------------------------------------------------ tool surface


def test_discard_draft_is_on_the_surface_with_its_schema() -> None:
    tools = {tool.name: tool for tool in _build_tool_list()}
    tool = tools["discard_draft"]
    assert tool.inputSchema["required"] == ["account", "folder", "draft_id"]
    assert set(tool.inputSchema["properties"]) == {"account", "folder", "draft_id"}
    assert tool.inputSchema["additionalProperties"] is False
    description = tool.description or ""
    for phrase in ("trash", "move_out", "accept_incoming", "Never deletes"):
        assert phrase in description


def test_discard_draft_is_gated_like_move() -> None:
    assert WRITE_TOOL_CAP["discard_draft"] == WRITE_TOOL_CAP["move"] == "move_out"


def test_trash_unavailable_is_in_the_error_enumeration() -> None:
    envelope = error_envelope(error_type="trash_unavailable")
    assert envelope["error"]["type"] == "trash_unavailable"


# ------------------------------------------------------------ handler order


def _context(*folders: FolderPolicy) -> Any:
    configuration = Configuration(
        accounts_file=AccountsFile(accounts=[Account(id=ACCOUNT)]),
        callers_file=CallersFile(callers=[Caller(id="agent", policy="p")]),
        policies={"p": PolicyFile(name="p", accounts={ACCOUNT: list(folders)})},
    )
    account = configuration.accounts_file.accounts[0]
    return SimpleNamespace(
        caller_id="agent",
        pdp=PolicyDecisionPoint(configuration),
        account_by_id=lambda account_id: account if account_id == ACCOUNT else None,
    )


@pytest.fixture
def imap(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Replace every IMAP touchpoint of the handler with a recorder."""
    calls: dict[str, Any] = {
        "mailboxes": [
            FolderInfo(path="INBOX", message_count=0),
            FolderInfo(path="Drafts", message_count=0, flags="\\Drafts"),
            FolderInfo(path="Trash", message_count=0, flags="\\Trash"),
        ],
        "uids": [41, 42],
        "moved": [],
    }

    async def password_for(context: Any, account_id: str) -> tuple[Any, str]:
        return context.account_by_id(account_id), "secret"

    async def resolve(context: Any, account_id: str, folder: str) -> str:
        return folder

    async def no_aliases(context: Any, account_id: str) -> dict[str, str]:
        return {}

    async def list_mailboxes(account: Any, password: str) -> list[FolderInfo]:
        return list(calls["mailboxes"])

    async def uids_carrying_handle(
        account: Any, password: str, folder: str, draft_id: str
    ) -> list[int]:
        return list(calls["uids"])

    async def move_uids(
        account: Any, password: str, folder: str, uids: list[int], target: str
    ) -> str:
        calls["moved"].append((folder, list(uids), target))
        return "native_move"

    monkeypatch.setattr(drafts, "_password_for", password_for)
    monkeypatch.setattr(drafts, "_resolve_imap_folder", resolve)
    monkeypatch.setattr(drafts, "_ensure_folder_aliases", no_aliases)
    monkeypatch.setattr(drafts, "imap_list_mailboxes", list_mailboxes)
    monkeypatch.setattr(drafts, "_uids_carrying_handle", uids_carrying_handle)
    monkeypatch.setattr(drafts, "imap_move_uids", move_uids)
    return calls


def _drafts(*, move_out: bool) -> FolderPolicy:
    return FolderPolicy(path="Drafts", mode="blacklist", default="FULL", move_out=move_out)


def _trash(*, accept_incoming: bool) -> FolderPolicy:
    return FolderPolicy(
        path="Trash", mode="blacklist", default="METADATA", accept_incoming=accept_incoming
    )


async def _discard(context: Any) -> dict[str, Any]:
    arguments = {"account": ACCOUNT, "folder": "Drafts", "draft_id": DRAFT_ID}
    return dict(await drafts.handle_discard_draft(context, arguments))


async def test_all_revisions_move_to_the_flagged_trash(imap: dict[str, Any]) -> None:
    response = await _discard(_context(_drafts(move_out=True), _trash(accept_incoming=True)))
    assert response["decision"] == "ALLOW"
    assert response["result"] == "OK"
    assert response["discarded_count"] == 2
    assert response["moved_to"] == "Trash"
    assert imap["moved"] == [("Drafts", [41, 42], "Trash")]


async def test_move_out_is_checked_before_accept_incoming(imap: dict[str, Any]) -> None:
    response = await _discard(_context(_drafts(move_out=False), _trash(accept_incoming=False)))
    assert response["decision"] == "DENY"
    assert response["missing_capability"] == "move_out"
    assert imap["moved"] == []


async def test_missing_accept_incoming_on_trash_denies(imap: dict[str, Any]) -> None:
    response = await _discard(_context(_drafts(move_out=True), _trash(accept_incoming=False)))
    assert response["decision"] == "DENY"
    assert response["reason"] == "capability_missing"
    assert response["missing_capability"] == "accept_incoming"
    assert imap["moved"] == []


async def test_trash_outside_the_policy_denies_without_naming_it(
    imap: dict[str, Any],
) -> None:
    response = await _discard(_context(_drafts(move_out=True)))
    assert response["decision"] == "DENY"
    assert response["reason"] == "folder_hidden"
    assert "target_folder" not in response
    assert imap["moved"] == []


async def test_account_without_trash_reports_trash_unavailable(
    imap: dict[str, Any],
) -> None:
    imap["mailboxes"] = [FolderInfo(path="Drafts", message_count=0, flags="\\Drafts")]
    response = await _discard(_context(_drafts(move_out=True)))
    assert response["result"] == "ERROR"
    assert response["error"]["type"] == "trash_unavailable"
    assert imap["moved"] == []


async def test_unknown_handle_reports_draft_not_found(imap: dict[str, Any]) -> None:
    imap["uids"] = []
    response = await _discard(_context(_drafts(move_out=True), _trash(accept_incoming=True)))
    assert response["result"] == "ERROR"
    assert response["error"]["type"] == "draft_not_found"
    assert imap["moved"] == []


async def test_unlisted_drafts_folder_reports_folder_absent(imap: dict[str, Any]) -> None:
    imap["mailboxes"] = [FolderInfo(path="Trash", message_count=0, flags="\\Trash")]
    response = await _discard(_context(_drafts(move_out=True), _trash(accept_incoming=True)))
    assert response["result"] == "ERROR"
    assert response["error"]["type"] == "folder_absent"
    assert imap["moved"] == []
