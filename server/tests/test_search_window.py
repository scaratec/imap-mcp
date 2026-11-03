"""Unit tests for search windows and calendar bounds (ADR 0036).

The BDD suite witnesses search against Dovecot. These tests pin what is
cheaper to enumerate here: the calendar-date grammar and its edge
cases, the validation order between the bounds, the IMAP SEARCH strings
each scope produces, that every tool sharing the criteria schema
validates the same way, and that `outside_scope_total` is reported
symmetrically and never replaced by a silent zero.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest

from imap_mcp import imap_core
from imap_mcp.config import (
    Account,
    AccountsFile,
    Caller,
    CallersFile,
    Configuration,
    FolderPolicy,
    PolicyFile,
)
from imap_mcp.dispatch import _build_tool_list, _validate_arguments_against_surface
from imap_mcp.handlers import search
from imap_mcp.policy import MessageFacts, PolicyDecisionPoint

NOW = datetime(2026, 11, 2, 12, 0, tzinfo=UTC)
CRITERIA_TOOLS = ("search", "list_messages", "bulk_mark_seen", "bulk_mark_tagged")
# The other required arguments of each tool, so a refusal can only come
# from the criteria under test.
TOOL_REQUIRED_EXTRAS: dict[str, dict[str, Any]] = {
    "search": {},
    "list_messages": {},
    "bulk_mark_seen": {"seen": True},
    "bulk_mark_tagged": {"tags": ["Erledigt"], "mode": "add"},
}

# ------------------------------------------------------------ calendar grammar


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("2026-07-01", (2026, 7, 1)),
        ("2024-02-29", (2024, 2, 29)),
        ("2026-12-31", (2026, 12, 31)),
    ],
)
def test_iso_calendar_dates_parse(value: str, expected: tuple[int, int, int]) -> None:
    parsed = search.parse_calendar_date(value)
    assert parsed is not None
    assert (parsed.year, parsed.month, parsed.day) == expected


@pytest.mark.parametrize(
    "value",
    [
        "01.07.2026",
        "2026-02-30",
        "2025-02-29",
        "2026-13-01",
        "2026-7-1",
        "20260701",
        "2026-07-01T00:00:00",
        "2026-07-01\n",
        " 2026-07-01",
        "２０２６-07-01",
        "",
        None,
        20260701,
        ["2026-07-01"],
    ],
)
def test_anything_but_an_iso_calendar_date_is_refused(value: object) -> None:
    assert search.parse_calendar_date(value) is None


def test_imap_dates_do_not_depend_on_the_locale() -> None:
    assert search._imap_date(datetime(2026, 10, 26, tzinfo=UTC)) == "26-Oct-2026"
    assert search._imap_date(datetime(2026, 3, 1, tzinfo=UTC)) == "01-Mar-2026"


# ------------------------------------------------------------ bound validation


@pytest.mark.parametrize(
    "criteria",
    [
        {},
        {"since": "2026-07-01"},
        {"before": "2026-10-01"},
        {"since": "2026-07-01", "before": "2026-07-02"},
        {"from_domain": "nordlicht.example.org", "since": "2026-07-01"},
        {"newer_than": "30d", "older_than": "7d"},
    ],
)
def test_valid_windows_pass(criteria: dict[str, Any]) -> None:
    assert search.validate_calendar_bounds(criteria) is None


@pytest.mark.parametrize(
    ("criteria", "names"),
    [
        ({"since": "01.07.2026"}, ("since",)),
        ({"before": "2026-02-30"}, ("before",)),
        ({"since": None}, ("since",)),
        ({"since": "2026-10-01", "before": "2026-07-01"}, ("since", "before")),
        ({"since": "2026-07-01", "before": "2026-07-01"}, ("since", "before")),
        ({"since": "2026-07-01", "newer_than": "30d"}, ("since", "newer_than")),
        ({"before": "2026-07-01", "older_than": "30d"}, ("before", "older_than")),
    ],
)
def test_invalid_windows_name_the_offending_members(
    criteria: dict[str, Any], names: tuple[str, ...]
) -> None:
    message = search.validate_calendar_bounds(criteria)
    assert message is not None
    for name in names:
        assert name in message


def test_a_malformed_date_is_reported_before_the_mix_with_a_duration() -> None:
    message = search.validate_calendar_bounds({"since": "01.07.2026", "newer_than": "30d"})
    assert message is not None
    assert "ISO calendar date" in message


@pytest.mark.parametrize("tool", CRITERIA_TOOLS)
@pytest.mark.parametrize(
    "criteria",
    [
        {"since": "01.07.2026"},
        {"since": "2026-10-01", "before": "2026-07-01"},
        {"since": "2026-07-01", "newer_than": "30d"},
    ],
)
def test_every_tool_with_criteria_refuses_an_invalid_window(
    tool: str, criteria: dict[str, Any]
) -> None:
    arguments = {
        "account": "a",
        "folder": "INBOX",
        "criteria": criteria,
        **TOOL_REQUIRED_EXTRAS[tool],
    }
    message = _validate_arguments_against_surface(tool, arguments)
    assert message is not None
    assert "criteria" in message


@pytest.mark.parametrize("tool", CRITERIA_TOOLS)
def test_every_tool_with_criteria_accepts_a_calendar_window(tool: str) -> None:
    arguments = {
        "account": "a",
        "folder": "INBOX",
        "criteria": {"since": "2026-07-01", "before": "2026-10-01"},
        **TOOL_REQUIRED_EXTRAS[tool],
    }
    assert _validate_arguments_against_surface(tool, arguments) is None


def test_the_criteria_schema_carries_the_calendar_bounds_for_every_tool() -> None:
    tools = {tool.name: tool for tool in _build_tool_list()}
    for name in CRITERIA_TOOLS:
        properties = tools[name].inputSchema["properties"]["criteria"]["properties"]
        for key in ("since", "before"):
            assert properties[key]["type"] == "string"
            assert properties[key]["format"] == "date"


@pytest.mark.parametrize("name", ["search", "list_messages"])
def test_listing_descriptions_explain_the_window_and_the_bounds(name: str) -> None:
    tools = {tool.name: tool for tool in _build_tool_list()}
    description = tools[name].description or ""
    for phrase in ("outside_scope_total", "since", "before", "YYYY-MM-DD", '"all"'):
        assert phrase in description


# ------------------------------------------------------------ IMAP translation


def test_calendar_bounds_map_onto_since_and_before() -> None:
    imap = search._criteria_to_imap_search({"since": "2026-07-01", "before": "2026-10-01"})
    assert imap == "SINCE 01-Jul-2026 BEFORE 01-Oct-2026"


def test_the_default_window_counts_what_lies_before_it() -> None:
    scoped = search._scoped_search({"from_domain": "nordlicht.example.org"}, None, now=NOW)
    assert scoped.applied_scope == "recent_7d"
    assert scoped.imap_criteria == 'FROM "@nordlicht.example.org" SINCE 26-Oct-2026'
    assert scoped.outside_criteria == 'FROM "@nordlicht.example.org" BEFORE 26-Oct-2026'


def test_the_default_window_without_criteria_drops_all() -> None:
    scoped = search._scoped_search({}, "recent", now=NOW)
    assert scoped.imap_criteria == "SINCE 26-Oct-2026"
    assert scoped.outside_criteria == "BEFORE 26-Oct-2026"


@pytest.mark.parametrize(
    "criteria",
    [
        {"since": "2026-07-01"},
        {"before": "2026-07-01"},
        {"newer_than": "30d"},
        {"older_than": "30d"},
    ],
)
@pytest.mark.parametrize("scope", [None, "recent", "all"])
def test_any_time_bound_makes_the_window_explicit(
    criteria: dict[str, Any], scope: str | None
) -> None:
    scoped = search._scoped_search(criteria, scope, now=NOW)
    assert scoped.applied_scope == "explicit_window"
    assert scoped.outside_criteria is None
    assert "26-Oct-2026" not in scoped.imap_criteria


def test_scope_all_searches_all_time_without_an_outside_count() -> None:
    scoped = search._scoped_search({"subject_contains": "Rechnung"}, "all", now=NOW)
    assert scoped.applied_scope == "all_time"
    assert scoped.imap_criteria == 'SUBJECT "Rechnung"'
    assert scoped.outside_criteria is None


def test_the_post_filter_leaves_calendar_bounds_to_imap() -> None:
    facts = MessageFacts(
        from_address="rechnung@nordlicht.example.org",
        to_addresses=(),
        subject="Ihre Rechnung",
        has_attachment=False,
        flagged=False,
        size_bytes=1,
        date_iso="2026-09-29T07:00:00+00:00",
    )
    criteria = {"from_domain": "nordlicht.example.org", "since": "2026-07-01"}
    assert search._criteria_match(criteria, facts)
    assert not search._criteria_match({**criteria, "subject_contains": "Angebot"}, facts)


# ------------------------------------------------------------ handlers

ACCOUNT = "acct"


def _context() -> Any:
    folder = FolderPolicy(path="INBOX", mode="blacklist", default="ENVELOPE")
    configuration = Configuration(
        accounts_file=AccountsFile(accounts=[Account(id=ACCOUNT)]),
        callers_file=CallersFile(callers=[Caller(id="agent", policy="p")]),
        policies={"p": PolicyFile(name="p", accounts={ACCOUNT: [folder]})},
    )
    account = configuration.accounts_file.accounts[0]
    return SimpleNamespace(
        caller_id="agent",
        pdp=PolicyDecisionPoint(configuration),
        account_by_id=lambda account_id: account if account_id == ACCOUNT else None,
    )


@pytest.fixture
def imap(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Replace the IMAP touchpoints of the listing handlers with a recorder."""
    calls: dict[str, Any] = {"searches": [], "counts": [], "count_error": None}

    async def password_for(context: Any, account_id: str) -> tuple[Any, str]:
        return context.account_by_id(account_id), "secret"

    async def resolve(context: Any, account_id: str, folder: str) -> str:
        return folder

    async def search_uids(account: Any, password: str, folder: str, criteria: str) -> list[int]:
        calls["searches"].append(criteria)
        return [1]

    async def count_uids(account: Any, password: str, folder: str, criteria: str) -> int:
        calls["counts"].append(criteria)
        if calls["count_error"] is not None:
            raise calls["count_error"]
        return 5

    async def envelopes(account: Any, password: str, folder: str, uids: list[int]) -> list[Any]:
        return [
            imap_core.Envelope(
                uid=uid,
                from_address="rechnung@nordlicht.example.org",
                to_addresses=[],
                subject="Ihre Rechnung",
                date="2026-10-30T07:00:00+00:00",
                message_id=None,
                has_attachment=False,
                size_bytes=1,
            )
            for uid in uids
        ]

    monkeypatch.setattr(search, "_password_for", password_for)
    monkeypatch.setattr(search, "_resolve_imap_folder", resolve)
    monkeypatch.setattr(search, "imap_search_uids", search_uids)
    monkeypatch.setattr(search, "imap_count_uids", count_uids)
    monkeypatch.setattr(search, "imap_fetch_envelopes_batch", envelopes)
    monkeypatch.setattr("imap_mcp.audit._now_utc", lambda: NOW)
    return calls


async def _call(tool: str, criteria: dict[str, Any], scope: str | None = None) -> dict[str, Any]:
    arguments: dict[str, Any] = {"account": ACCOUNT, "folder": "INBOX", "criteria": criteria}
    if scope is not None:
        arguments["scope"] = scope
    if tool == "search":
        return dict(await search.handle_search(_context(), arguments))
    return dict(await search.handle_list_messages(_context(), arguments))


@pytest.mark.parametrize("tool", ["search", "list_messages"])
async def test_the_default_window_reports_the_outside_count(
    imap: dict[str, Any], tool: str
) -> None:
    response = await _call(tool, {"from_domain": "nordlicht.example.org"})
    assert response["applied_scope"] == "recent_7d"
    assert response["outside_scope_total"] == 5
    assert imap["counts"] == ['FROM "@nordlicht.example.org" BEFORE 26-Oct-2026']


@pytest.mark.parametrize("tool", ["search", "list_messages"])
@pytest.mark.parametrize(
    ("criteria", "scope"),
    [
        ({"from_domain": "nordlicht.example.org"}, "all"),
        ({"since": "2026-07-01", "before": "2026-10-01"}, None),
        ({"newer_than": "30d"}, None),
    ],
)
async def test_no_outside_count_without_the_default_window(
    imap: dict[str, Any], tool: str, criteria: dict[str, Any], scope: str | None
) -> None:
    response = await _call(tool, criteria, scope)
    assert "outside_scope_total" not in response
    assert imap["counts"] == []


@pytest.mark.parametrize("tool", ["search", "list_messages"])
async def test_a_failed_count_fails_the_call(imap: dict[str, Any], tool: str) -> None:
    imap["count_error"] = RuntimeError("count SEARCH failed: NO")
    with pytest.raises(RuntimeError, match="count SEARCH failed"):
        await _call(tool, {"from_domain": "nordlicht.example.org"})


# ------------------------------------------------------------ count search


class _FakeImap:
    def __init__(self, select: str, search: tuple[str, list[bytes]]) -> None:
        self._select = select
        self._search = search
        self.released = False

    async def select(self, folder: str) -> tuple[str, list[bytes]]:
        return self._select, []

    async def uid_search(self, criteria: str) -> tuple[str, list[bytes]]:
        return self._search


def _patch_connection(monkeypatch: pytest.MonkeyPatch, fake: _FakeImap) -> None:
    @asynccontextmanager
    async def session(account: Any, password: str, **_: Any) -> AsyncIterator[_FakeImap]:
        try:
            yield fake
        finally:
            fake.released = True

    monkeypatch.setattr(imap_core, "_imap_session", session)


async def test_count_uids_counts_the_search_result(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeImap("OK", ("OK", [b"2 3 4", b"SEARCH completed"]))
    _patch_connection(monkeypatch, fake)
    assert await imap_core.count_uids(Account(id=ACCOUNT), "pw", "INBOX", "BEFORE x") == 3
    assert fake.released


async def test_count_uids_of_nothing_is_zero(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_connection(monkeypatch, _FakeImap("OK", ("OK", [b"", b"SEARCH completed"])))
    assert await imap_core.count_uids(Account(id=ACCOUNT), "pw", "INBOX", "BEFORE x") == 0


@pytest.mark.parametrize(
    ("select", "search_result"),
    [("NO", ("OK", [b"1"])), ("OK", ("NO", [b"failed"])), ("OK", ("BAD", []))],
)
async def test_a_refused_count_raises_instead_of_reading_zero(
    monkeypatch: pytest.MonkeyPatch, select: str, search_result: tuple[str, list[bytes]]
) -> None:
    fake = _FakeImap(select, search_result)
    _patch_connection(monkeypatch, fake)
    with pytest.raises(RuntimeError):
        await imap_core.count_uids(Account(id=ACCOUNT), "pw", "INBOX", "BEFORE x")
    assert fake.released
