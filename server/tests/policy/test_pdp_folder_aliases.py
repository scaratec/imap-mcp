"""Unit tests for Gmail special-use folder normalization in the PDP (ADR 0033).

The alias map `{canonical: wire}` is handed to the PDP after an IMAP
LIST. Requested paths and policy keys are normalized through it, and
two entries for one physical folder that disagree fail closed with
`policy_alias_conflict`.
"""

from __future__ import annotations

from imap_mcp.config import (
    Account,
    AccountsFile,
    Caller,
    CallersFile,
    Configuration,
    FolderPolicy,
    PolicyFile,
)
from imap_mcp.policy import PolicyDecisionPoint

ACCOUNT = "gmail-de"
ALIASES = {"[Gmail]/Trash": "[Gmail]/Papierkorb", "[Gmail]/Drafts": "[Gmail]/Entwürfe"}


def _pdp(*folders: FolderPolicy, aliases: dict[str, str] | None = ALIASES) -> PolicyDecisionPoint:
    configuration = Configuration(
        accounts_file=AccountsFile(accounts=[Account(id=ACCOUNT, provider="google")]),
        callers_file=CallersFile(callers=[Caller(id="agent", policy="p")]),
        policies={"p": PolicyFile(name="p", accounts={ACCOUNT: list(folders)})},
    )
    pdp = PolicyDecisionPoint(configuration)
    if aliases is not None:
        pdp.set_folder_aliases(ACCOUNT, aliases)
    return pdp


def test_localized_entry_governs_canonical_and_localized_request() -> None:
    pdp = _pdp(FolderPolicy(path="[Gmail]/Papierkorb", mode="blacklist", default="METADATA"))
    for requested in ("[Gmail]/Trash", "[Gmail]/Papierkorb"):
        decision = pdp.decide_folder_access("agent", ACCOUNT, requested)
        assert decision.allowed
        assert decision.visibility == "METADATA"


def test_canonical_entry_governs_localized_request() -> None:
    pdp = _pdp(FolderPolicy(path="[Gmail]/Drafts", mode="blacklist", default="FULL"))
    assert pdp.decide_folder_access("agent", ACCOUNT, "[Gmail]/Entwürfe").allowed


def test_disagreeing_alias_entries_deny_with_policy_alias_conflict() -> None:
    pdp = _pdp(
        FolderPolicy(path="[Gmail]/Trash", mode="whitelist", default="NONE"),
        FolderPolicy(path="[Gmail]/Papierkorb", mode="blacklist", default="FULL"),
    )
    for requested in ("[Gmail]/Trash", "[Gmail]/Papierkorb"):
        decision = pdp.decide_folder_access("agent", ACCOUNT, requested)
        assert not decision.allowed
        assert decision.reason == "policy_alias_conflict"
        assert decision.visibility == "NONE"
    visibility = pdp.visible_folders_for("agent", ACCOUNT, ["INBOX", "[Gmail]/Trash"])
    assert visibility.visible_folder_paths == []


def test_identical_alias_entries_are_accepted() -> None:
    pdp = _pdp(
        FolderPolicy(path="[Gmail]/Trash", mode="blacklist", default="FULL", move_out=True),
        FolderPolicy(path="[Gmail]/Papierkorb", mode="blacklist", default="FULL", move_out=True),
    )
    decision = pdp.decide_folder_access("agent", ACCOUNT, "[Gmail]/Trash")
    assert decision.allowed
    assert decision.folder_policy is not None and decision.folder_policy.move_out


def test_without_alias_map_paths_compare_literally() -> None:
    pdp = _pdp(
        FolderPolicy(path="[Gmail]/Papierkorb", mode="blacklist", default="FULL"),
        aliases=None,
    )
    assert pdp.decide_folder_access("agent", ACCOUNT, "[Gmail]/Trash").reason == "folder_hidden"
    assert pdp.decide_folder_access("agent", ACCOUNT, "[Gmail]/Papierkorb").allowed


def test_localized_policy_entry_is_visible_under_canonical_name() -> None:
    pdp = _pdp(FolderPolicy(path="[Gmail]/Papierkorb", mode="blacklist", default="COUNT"))
    visibility = pdp.visible_folders_for("agent", ACCOUNT, ["INBOX", "[Gmail]/Trash"])
    assert visibility.visible_folder_paths == ["[Gmail]/Trash"]
    assert visibility.hidden_folder_count == 1
