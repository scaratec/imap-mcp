"""Listing/search handlers: search, list_messages, plus criteria helpers.

ADR 0024 anchors the duration grammar; ADR 0026 introduces the explicit
`scope` argument and renames `default_scope` to `applied_scope`; ADR 0036
adds the calendar bounds `since`/`before` and `outside_scope_total`.
"""

from __future__ import annotations

import math
import re
from datetime import date, datetime, timedelta
from typing import TYPE_CHECKING, Any, Literal, NamedTuple, NotRequired, TypedDict

from ..imap_core import FolderNotSelectable
from ..imap_core import (
    count_uids as imap_count_uids,
)
from ..imap_core import (
    fetch_envelopes_batch as imap_fetch_envelopes_batch,
)
from ..imap_core import (
    gmail_fetch_msgid as imap_gmail_fetch_msgid,
)
from ..imap_core import (
    gmail_search_by_msgid as imap_gmail_search_by_msgid,
)
from ..imap_core import (
    search_uids as imap_search_uids,
)
from ..policy import (
    MessageFacts,
    _match_single_predicate,
    evaluate_message_against_folder,
    level_rank,
    parse_duration,
)
from ._common import (
    _facts_from_envelope,
    _is_google_provider,
    _password_for,
    _resolve_imap_folder,
    error_envelope,
)

if TYPE_CHECKING:
    from ..context import ServerContext


AppliedScope = Literal["recent_7d", "explicit_window", "all_time"]


class GmailSearchEntry(TypedDict, total=False):
    uid: int
    gm_msgid: NotRequired[int]
    canonical_all_mail_uid: NotRequired[int]


class SearchResponse(TypedDict, total=False):
    decision: Literal["ALLOW", "DENY"]
    reason: NotRequired[str]
    account: str
    folder: str
    uids: NotRequired[list[int]]
    matched_total: NotRequired[int]
    matched_visible: NotRequired[int]
    filtered_out: NotRequired[int]
    page_offset: NotRequired[int]
    page_limit: NotRequired[int]
    has_more: NotRequired[bool]
    applied_scope: NotRequired[AppliedScope]
    outside_scope_total: NotRequired[int]
    gmail_results: NotRequired[list[GmailSearchEntry]]


# Functional TypedDict form so the JSON-wire key `from` can be expressed
# as the Python-keyword key, eliminating the old `from_` placeholder
# (ADR 0026 §4).
MessageEntry = TypedDict(
    "MessageEntry",
    {
        "uid": int,
        "from": str,
        "to": list[str],
        "subject": str,
        "date": str | None,
        "has_attachment": bool,
        "size_bytes": int,
    },
)


class ListMessagesResponse(TypedDict, total=False):
    decision: Literal["ALLOW", "DENY"]
    reason: NotRequired[str]
    account: str
    folder: str
    messages: NotRequired[list[dict[str, Any]]]
    matched_total: NotRequired[int]
    matched_visible: NotRequired[int]
    filtered_out: NotRequired[int]
    page_offset: NotRequired[int]
    page_limit: NotRequired[int]
    has_more: NotRequired[bool]
    applied_scope: NotRequired[AppliedScope]
    outside_scope_total: NotRequired[int]


def _deny_search(*, reason: str, account: str, folder: str) -> SearchResponse:
    return {"decision": "DENY", "reason": reason, "account": account, "folder": folder}


def _deny_list_messages(*, reason: str, account: str, folder: str) -> ListMessagesResponse:
    return {"decision": "DENY", "reason": reason, "account": account, "folder": folder}


# ADR 0024 durations are relative to now; ADR 0036 calendar bounds are
# ISO dates with IMAP SEARCH SINCE / BEFORE semantics (RFC 3501 §6.4.4).
_DURATION_KEYS = ("newer_than", "older_than")
_CALENDAR_KEYS = ("since", "before")
_CALENDAR_DATE_RE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")
# The default window of ADR 0026 §5.
RECENT_WINDOW_DAYS = 7
# Criteria the IMAP SEARCH answers exactly, so no envelope is needed to
# post-filter them. Durations are absent because sub-day rounding
# (ADR 0024) makes the IMAP layer over-fetch.
_ENVELOPE_FREE_KEYS = frozenset(
    {
        "from",
        "from_domain",
        "to",
        "to_contains",
        "subject_contains",
        "size_gt",
        "size_lt",
        "flagged",
        *_CALENDAR_KEYS,
    }
)
_IMAP_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


def parse_calendar_date(value: object) -> date | None:
    """Return the day an ISO `YYYY-MM-DD` string names, None otherwise.

    Impossible days such as 2026-02-30 are None too.
    """
    if not isinstance(value, str) or not _CALENDAR_DATE_RE.fullmatch(value):
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def validate_calendar_bounds(criteria: dict[str, Any]) -> str | None:
    """Check the calendar bounds of `criteria` (ADR 0036).

    Returns the invalid-arguments message, or None when the criteria may
    reach IMAP. Runs before any IMAP command.
    """
    bounds: dict[str, date] = {}
    for key in _CALENDAR_KEYS:
        if key not in criteria:
            continue
        parsed = parse_calendar_date(criteria[key])
        if parsed is None:
            return (
                f"Invalid criteria.{key} {criteria[key]!r}: must be an ISO calendar "
                "date YYYY-MM-DD (ADR 0036)"
            )
        bounds[key] = parsed
    if not bounds:
        return None
    if "since" in bounds and "before" in bounds and bounds["since"] >= bounds["before"]:
        return (
            f"criteria.since {criteria['since']!r} must be earlier than "
            f"criteria.before {criteria['before']!r} (ADR 0036)"
        )
    durations = [key for key in _DURATION_KEYS if key in criteria]
    if durations:
        return (
            f"criteria cannot combine the calendar bound {', '.join(bounds)} with "
            f"the duration {', '.join(durations)} (ADR 0036)"
        )
    return None


def _imap_date(day: date) -> str:
    """RFC 3501 `date` (e.g. `26-Oct-2026`), independent of the locale."""
    return f"{day.day:02d}-{_IMAP_MONTHS[day.month - 1]}-{day.year}"


def _criteria_match(criteria: dict[str, Any], facts: MessageFacts) -> bool:
    """Post-filter: check all MCP criteria against envelope facts.

    Catches predicates that cannot be expressed as IMAP SEARCH terms
    (e.g. has_attachment) and refines inexact IMAP matches. Calendar
    bounds are skipped: they compare the IMAP internal date, which only
    the IMAP SEARCH sees, and it answers them exactly.
    """
    return all(
        _match_single_predicate(key, value, facts=facts)
        for key, value in criteria.items()
        if key not in _CALENDAR_KEYS
    )


def _criteria_to_imap_search(criteria: dict[str, Any]) -> str:
    """Translate MCP search criteria dict to an IMAP SEARCH string.

    Duration values are parsed via the single grammar source
    (`policy.parse_duration`).  Sub-day values are rounded to whole days
    in the over-inclusive direction so the IMAP-layer result is always
    a superset of the true match set; the per-message post-filter in
    `_criteria_match` then narrows the window back to second resolution.
    See ADR 0024. Calendar bounds map one-to-one onto SINCE / BEFORE
    (ADR 0036); `validate_calendar_bounds` has checked them already.
    """
    from ..audit import _now_utc

    parts: list[str] = []
    for key, value in criteria.items():
        if key == "from":
            parts.append(f'FROM "{value}"')
        elif key == "from_domain":
            parts.append(f'FROM "@{value}"')
        elif key == "to" or key == "to_contains":
            parts.append(f'TO "{value}"')
        elif key == "subject_contains":
            parts.append(f'SUBJECT "{value}"')
        elif key == "newer_than":
            seconds = parse_duration(str(value))
            days = max(1, math.ceil(seconds / 86400))
            since = _now_utc() - timedelta(days=days)
            parts.append(f"SINCE {_imap_date(since)}")
        elif key == "older_than":
            seconds = parse_duration(str(value))
            days = max(0, math.floor(seconds / 86400))
            before = _now_utc() - timedelta(days=days)
            parts.append(f"BEFORE {_imap_date(before)}")
        elif key == "since":
            parts.append(f"SINCE {_imap_date(date.fromisoformat(str(value)))}")
        elif key == "before":
            parts.append(f"BEFORE {_imap_date(date.fromisoformat(str(value)))}")
        elif key == "size_gt":
            parts.append(f"LARGER {int(value)}")
        elif key == "size_lt":
            parts.append(f"SMALLER {int(value)}")
        elif key == "flagged":
            if bool(value):
                parts.append("FLAGGED")
            else:
                parts.append("UNFLAGGED")
        elif key == "has_attachment":
            pass
    if not parts:
        return "ALL"
    return " ".join(parts)


class ScopedSearch(NamedTuple):
    """The IMAP SEARCH strings for one criteria/scope pair."""

    applied_scope: AppliedScope
    imap_criteria: str
    # The same criteria before the default window starts; set exactly
    # when applied_scope is recent_7d (ADR 0036).
    outside_criteria: str | None


def _join_search(criteria: str, term: str) -> str:
    return term if criteria == "ALL" else f"{criteria} {term}"


def _scoped_search(
    criteria: dict[str, Any], scope_arg: str | None, *, now: datetime | None = None
) -> ScopedSearch:
    """Decide the time scope (ADR 0026 §5) and build the IMAP searches.

    Any time bound in `criteria` makes the window explicit; otherwise
    `scope` picks all time or the seven-day default. SINCE and BEFORE
    on the same window start split the matches without overlap, so the
    outside search counts exactly what the window hid.
    """
    base = _criteria_to_imap_search(criteria)
    if any(key in criteria for key in (*_DURATION_KEYS, *_CALENDAR_KEYS)):
        return ScopedSearch("explicit_window", base, None)
    if (scope_arg or "recent").lower() == "all":
        return ScopedSearch("all_time", base, None)
    if now is None:
        from ..audit import _now_utc

        now = _now_utc()
    window_start = _imap_date(now - timedelta(days=RECENT_WINDOW_DAYS))
    return ScopedSearch(
        "recent_7d",
        _join_search(base, f"SINCE {window_start}"),
        _join_search(base, f"BEFORE {window_start}"),
    )


async def handle_search(context: ServerContext, arguments: dict[str, Any]) -> SearchResponse:
    account_id = str(arguments["account"])
    folder_path = str(arguments["folder"])
    criteria_raw = arguments.get("criteria") or {}
    limit = int(arguments.get("limit") or 50)
    offset = int(arguments.get("offset") or 0)
    scope_arg = arguments.get("scope")
    folder_decision = context.pdp.decide_folder_access(context.caller_id, account_id, folder_path)
    if not folder_decision.allowed:
        return _deny_search(reason=folder_decision.reason, account=account_id, folder=folder_path)
    assert folder_decision.folder_policy is not None
    minimum_for_tool = level_rank("METADATA")
    if (
        level_rank(folder_decision.visibility) < minimum_for_tool
        and not any(
            level_rank(rule.grant) >= minimum_for_tool
            for rule in folder_decision.folder_policy.rules
            if rule.grant is not None
        )
        and folder_decision.folder_policy.mode == "whitelist"
    ):
        return _deny_search(
            reason="visibility_below_METADATA", account=account_id, folder=folder_path
        )

    scoped = _scoped_search(criteria_raw, scope_arg)

    account, password = await _password_for(context, account_id)
    imap_folder = await _resolve_imap_folder(context, account_id, folder_path)
    all_uids = await imap_search_uids(account, password, imap_folder, scoped.imap_criteria)
    matched_total = len(all_uids)
    outside_scope_total: int | None = None
    if scoped.outside_criteria is not None:
        try:
            outside_scope_total = await imap_count_uids(
                account, password, imap_folder, scoped.outside_criteria
            )
        except FolderNotSelectable:
            # ADR 0025: an authorized folder that does not exist is
            # folder_absent, not an empty result.
            return error_envelope(  # type: ignore[return-value]
                error_type="folder_absent",
                reason=folder_decision.reason,
                extra={"account": account_id, "folder": folder_path},
            )

    fp = folder_decision.folder_policy
    pdp_predetermined = (
        fp.mode == "blacklist" and not fp.rules and level_rank(fp.default) >= minimum_for_tool
    )
    criteria_needs_envelope = criteria_raw and any(
        k not in _ENVELOPE_FREE_KEYS for k in criteria_raw
    )

    if pdp_predetermined and not criteria_needs_envelope:
        visible_uids = list(all_uids)
    else:
        all_envelopes = await imap_fetch_envelopes_batch(account, password, imap_folder, all_uids)
        envelope_by_uid = {e.uid: e for e in all_envelopes}
        visible_uids = []
        for candidate_uid in all_uids:
            envelope = envelope_by_uid.get(candidate_uid)
            if envelope is None:
                continue
            facts = _facts_from_envelope(envelope)
            if criteria_raw and not _criteria_match(criteria_raw, facts):
                continue
            message_decision = evaluate_message_against_folder(fp, facts=facts)
            if (
                message_decision.allowed
                and level_rank(message_decision.visibility) >= minimum_for_tool
            ):
                visible_uids.append(candidate_uid)
    filtered_out = matched_total - len(visible_uids)

    all_visible = visible_uids
    page = all_visible[offset : offset + limit]
    has_more = (offset + limit) < len(all_visible)

    results_with_gmail: list[GmailSearchEntry] | None = None
    account_obj = context.account_by_id(account_id)
    if account_obj is not None and _is_google_provider(account_obj) and page and len(page) <= 10:
        results_with_gmail = []
        for vuid in page:
            entry: GmailSearchEntry = {"uid": vuid}
            try:
                gm_msgid = await imap_gmail_fetch_msgid(account, password, imap_folder, vuid)
                if gm_msgid is not None:
                    entry["gm_msgid"] = gm_msgid
                    imap_all_mail = await _resolve_imap_folder(
                        context, account_id, "[Gmail]/All Mail"
                    )
                    all_mail_hits = await imap_gmail_search_by_msgid(
                        account, password, imap_all_mail, gm_msgid
                    )
                    if all_mail_hits:
                        entry["canonical_all_mail_uid"] = all_mail_hits[0]
            except Exception:  # noqa: BLE001, S110 - canonical UID is optional
                pass
            results_with_gmail.append(entry)

    result: SearchResponse = {
        "decision": "ALLOW",
        "reason": "rule_matched" if all_visible else "folder_default_applied",
        "account": account_id,
        "folder": folder_path,
        "uids": page,
        "matched_total": matched_total,
        "matched_visible": len(all_visible),
        "filtered_out": filtered_out,
        "page_offset": offset,
        "page_limit": limit,
        "has_more": has_more,
        "applied_scope": scoped.applied_scope,
    }
    if outside_scope_total is not None:
        result["outside_scope_total"] = outside_scope_total
    if results_with_gmail is not None:
        result["gmail_results"] = results_with_gmail
    return result


async def handle_list_messages(
    context: ServerContext, arguments: dict[str, Any]
) -> ListMessagesResponse:
    account_id = str(arguments["account"])
    folder_path = str(arguments["folder"])
    criteria_raw = arguments.get("criteria") or {}
    limit = int(arguments.get("limit") or 20)
    offset = int(arguments.get("offset") or 0)
    scope_arg = arguments.get("scope")

    folder_decision = context.pdp.decide_folder_access(context.caller_id, account_id, folder_path)
    if not folder_decision.allowed:
        return _deny_list_messages(
            reason=folder_decision.reason, account=account_id, folder=folder_path
        )
    assert folder_decision.folder_policy is not None
    minimum_for_tool = level_rank("METADATA")
    fp = folder_decision.folder_policy
    if (
        level_rank(fp.default) < minimum_for_tool
        and not any(
            level_rank(rule.grant) >= minimum_for_tool
            for rule in fp.rules
            if rule.grant is not None
        )
        and fp.mode == "whitelist"
    ):
        return _deny_list_messages(
            reason="visibility_below_METADATA", account=account_id, folder=folder_path
        )

    scoped = _scoped_search(criteria_raw, scope_arg)

    account, password = await _password_for(context, account_id)
    imap_folder = await _resolve_imap_folder(context, account_id, folder_path)
    all_uids = await imap_search_uids(account, password, imap_folder, scoped.imap_criteria)
    matched_total = len(all_uids)
    outside_scope_total: int | None = None
    if scoped.outside_criteria is not None:
        try:
            outside_scope_total = await imap_count_uids(
                account, password, imap_folder, scoped.outside_criteria
            )
        except FolderNotSelectable:
            # ADR 0025: an authorized folder that does not exist is
            # folder_absent, not an empty result.
            return error_envelope(  # type: ignore[return-value]
                error_type="folder_absent",
                reason=folder_decision.reason,
                extra={"account": account_id, "folder": folder_path},
            )

    pdp_predetermined = (
        fp.mode == "blacklist" and not fp.rules and level_rank(fp.default) >= minimum_for_tool
    )
    criteria_needs_envelope = criteria_raw and any(
        k not in _ENVELOPE_FREE_KEYS for k in criteria_raw
    )

    if pdp_predetermined and not criteria_needs_envelope:
        visible_uids = list(all_uids)
        all_envelopes_map: dict[int, Any] = {}
    else:
        all_envelopes = await imap_fetch_envelopes_batch(account, password, imap_folder, all_uids)
        all_envelopes_map = {e.uid: e for e in all_envelopes}
        visible_uids = []
        for candidate_uid in all_uids:
            envelope = all_envelopes_map.get(candidate_uid)
            if envelope is None:
                continue
            facts = _facts_from_envelope(envelope)
            if criteria_raw and not _criteria_match(criteria_raw, facts):
                continue
            message_decision = evaluate_message_against_folder(fp, facts=facts)
            if (
                message_decision.allowed
                and level_rank(message_decision.visibility) >= minimum_for_tool
            ):
                visible_uids.append(candidate_uid)
    filtered_out = matched_total - len(visible_uids)

    page_uids = visible_uids[offset : offset + limit]
    has_more = (offset + limit) < len(visible_uids)

    if all_envelopes_map:
        envelope_by_uid = {u: all_envelopes_map[u] for u in page_uids if u in all_envelopes_map}
    else:
        envelopes = await imap_fetch_envelopes_batch(account, password, imap_folder, page_uids)
        envelope_by_uid = {e.uid: e for e in envelopes}

    messages: list[dict[str, Any]] = []
    for uid in page_uids:
        env = envelope_by_uid.get(uid)
        if env is None:
            continue
        # Surface user-set keywords as `tags` so the bulk_mark_tagged
        # verification path can read them back. System flags (Seen,
        # Flagged, Draft, Deleted, Recent) are excluded — the caller
        # cares about its own tag operations, not the IMAP machinery.
        _SYSTEM_FLAGS = frozenset(
            {"\\Seen", "\\Flagged", "\\Draft", "\\Deleted", "\\Recent", "\\Answered"}
        )
        tags = [f for f in (env.flags or ()) if f not in _SYSTEM_FLAGS]
        messages.append(
            {
                "uid": env.uid,
                "from": env.from_address,
                "to": env.to_addresses,
                "subject": env.subject,
                "date": env.date,
                "has_attachment": env.has_attachment,
                "size_bytes": env.size_bytes,
                "tags": tags,
            }
        )

    result: ListMessagesResponse = {
        "decision": "ALLOW",
        "reason": "rule_matched" if visible_uids else "folder_default_applied",
        "account": account_id,
        "folder": folder_path,
        "messages": messages,
        "matched_total": matched_total,
        "matched_visible": len(visible_uids),
        "filtered_out": filtered_out,
        "page_offset": offset,
        "page_limit": limit,
        "has_more": has_more,
        "applied_scope": scoped.applied_scope,
    }
    if outside_scope_total is not None:
        result["outside_scope_total"] = outside_scope_total
    return result
