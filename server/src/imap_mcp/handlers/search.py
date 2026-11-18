"""Listing/search handlers: search, list_messages.

Adheres to:
- ADR 0040: Google multi-label composition, opaque object_id
- ADR 0041: Closed 18-predicate direction-aware grammar with UTC INTERNALDATE comparisons
- ADR 0043: Non-interference transparency — removal of matched_total, filtered_out, outside_scope_total
- ADR 0044: Normalized response envelope and folder_absent / select_failed handling
"""

from __future__ import annotations

import math
from datetime import date, datetime, timedelta
from typing import TYPE_CHECKING, Any, Literal, NamedTuple, NotRequired, TypedDict

from ..config import parse_duration_seconds
from ..imap_core import (
    Envelope,
    FolderNotSelectable,
    search_uids_with_validity,
)
from ..imap_core import (
    count_uids as imap_count_uids,
)
from ..imap_core import (
    fetch_envelopes_batch as imap_fetch_envelopes_batch,
)
from ..imap_core import (
    folder_stats as imap_folder_stats,
)
from ..imap_core import (
    search_uids as imap_search_uids,
)

__all__ = [
    "AppliedScope",
    "Completeness",
    "ListMessagesResponse",
    "ScopedSearch",
    "SearchResponse",
    "_criteria_match",
    "_criteria_to_imap_search",
    "_scoped_search",
    "handle_list_messages",
    "handle_search",
    "imap_count_uids",
    "imap_search_uids",
    "parse_calendar_date",
    "validate_calendar_bounds",
]
from ..policy import (
    MessageFacts,
    level_rank,
    match_single_predicate,
    normalize_domain,
    rank_to_level,
    req_vis,
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
Completeness = Literal["complete", "policy_may_hide", "window_limited", "window_and_policy_limited"]


class SearchResponse(TypedDict, total=False):
    decision: Literal["ALLOW", "DENY"]
    result: NotRequired[Literal["OK", "ERROR"]]
    reason: NotRequired[str]
    error: NotRequired[dict[str, str]]
    account: str
    folder: str
    uids: NotRequired[list[int]]
    total_visible: NotRequired[int]
    returned_count: NotRequired[int]
    page_offset: NotRequired[int]
    page_limit: NotRequired[int]
    has_more: NotRequired[bool]
    applied_scope: NotRequired[AppliedScope]
    completeness: NotRequired[Completeness]
    _suppressed_by_policy: NotRequired[bool]


class ListMessagesResponse(TypedDict, total=False):
    decision: Literal["ALLOW", "DENY"]
    result: NotRequired[Literal["OK", "ERROR"]]
    reason: NotRequired[str]
    error: NotRequired[dict[str, str]]
    account: str
    folder: str
    messages: NotRequired[list[dict[str, Any]]]
    total_visible: NotRequired[int]
    returned_count: NotRequired[int]
    page_offset: NotRequired[int]
    page_limit: NotRequired[int]
    has_more: NotRequired[bool]
    applied_scope: NotRequired[AppliedScope]
    completeness: NotRequired[Completeness]
    _suppressed_by_policy: NotRequired[bool]


RECENT_WINDOW_DAYS = 7
_IMAP_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


def _imap_date(day: date) -> str:
    return f"{day.day:02d}-{_IMAP_MONTHS[day.month - 1]}-{day.year}"


def parse_calendar_date(value: object) -> date | None:
    if not isinstance(value, str):
        return None
    try:
        # Strictly enforce YYYY-MM-DD
        parts = value.split("-")
        if len(parts) != 3 or len(parts[0]) != 4 or len(parts[1]) != 2 or len(parts[2]) != 2:
            return None
        return date.fromisoformat(value)
    except ValueError:
        return None


def _criteria_match(criteria: dict[str, Any], facts: Any) -> bool:
    return all(match_single_predicate(k, v, facts=facts) for k, v in criteria.items())


def validate_calendar_bounds(criteria: dict[str, Any]) -> str | None:
    bounds: dict[str, date] = {}
    for key in ("since", "before"):
        if key not in criteria:
            continue
        parsed = parse_calendar_date(criteria[key])
        if parsed is None:
            return f"Invalid criteria.{key} {criteria[key]!r}: must be an ISO calendar date YYYY-MM-DD (ADR 0036)"
        bounds[key] = parsed

    if "since" in bounds and "before" in bounds and bounds["since"] >= bounds["before"]:
        return f"criteria.since {criteria['since']!r} must be strictly earlier than criteria.before {criteria['before']!r}"

    durations = [k for k in ("newer_than", "older_than") if k in criteria]
    if bounds and durations:
        return (
            f"criteria cannot combine the calendar bound {', '.join(bounds)} with "
            f"the duration {', '.join(durations)} (ADR 0036)"
        )
    return None


def _criteria_to_imap_search(criteria: dict[str, Any]) -> str:
    """IMAP SEARCH translation with +/- 1 day padding for UTC timezone independence (ADR 0041)."""
    from ..audit import _now_utc

    parts: list[str] = []
    now = _now_utc()

    for key, value in criteria.items():
        if key == "from_address":
            parts.append(f'FROM "{value}"')
        elif key == "from_domain":
            parts.append(f'FROM "@{normalize_domain(str(value))}"')
        elif key == "to_address":
            parts.append(f'TO "{value}"')
        elif key == "to_domain":
            parts.append(f'TO "@{normalize_domain(str(value))}"')
        elif key == "recipient_address":
            parts.append(f'OR TO "{value}" CC "{value}"')
        elif key == "recipient_domain":
            dom = normalize_domain(str(value))
            parts.append(f'OR TO "@{dom}" CC "@{dom}"')
        elif key == "cc_address":
            parts.append(f'CC "{value}"')
        elif key == "cc_domain":
            parts.append(f'CC "@{normalize_domain(str(value))}"')
        elif key == "subject_contains":
            parts.append(f'SUBJECT "{value}"')
        elif key == "flagged":
            parts.append("FLAGGED" if bool(value) else "UNFLAGGED")
        elif key == "size_gt":
            parts.append(f"LARGER {int(value)}")
        elif key == "size_lt":
            parts.append(f"SMALLER {int(value)}")
        elif key == "newer_than":
            sec = parse_duration_seconds(str(value))
            # Pad by 1 extra day to be superset
            days = max(1, math.ceil(sec / 86400)) + 1
            since_day = (now - timedelta(days=days)).date()
            parts.append(f"SINCE {_imap_date(since_day)}")
        elif key == "older_than":
            sec = parse_duration_seconds(str(value))
            days = max(0, math.floor(sec / 86400)) - 1
            if days >= 0:
                before_day = (now - timedelta(days=days)).date()
                parts.append(f"BEFORE {_imap_date(before_day)}")
        elif key == "since":
            d = date.fromisoformat(str(value)) - timedelta(days=1)
            parts.append(f"SINCE {_imap_date(d)}")
        elif key == "before":
            d = date.fromisoformat(str(value)) + timedelta(days=1)
            parts.append(f"BEFORE {_imap_date(d)}")

    if not parts:
        return "ALL"
    return " ".join(parts)


class ScopedSearch(NamedTuple):
    applied_scope: AppliedScope
    imap_criteria: str
    outside_criteria: str | None = None


def _scoped_search(
    criteria: dict[str, Any], scope_arg: str | None, *, now: datetime | None = None
) -> ScopedSearch:
    base = _criteria_to_imap_search(criteria)
    has_time = any(k in criteria for k in ("newer_than", "older_than", "since", "before"))

    if has_time:
        return ScopedSearch("explicit_window", base, None)
    if (scope_arg or "recent").lower() == "all":
        return ScopedSearch("all_time", base, None)

    # 7-day default window
    if now is None:
        from ..audit import _now_utc

        now = _now_utc()
    window_start = (now - timedelta(days=RECENT_WINDOW_DAYS)).date()
    imap_start = _imap_date(window_start)
    since_term = f"SINCE {imap_start}"
    before_term = f"BEFORE {imap_start}"
    joined_since = since_term if base == "ALL" else f"{base} {since_term}"
    joined_before = before_term if base == "ALL" else f"{base} {before_term}"
    return ScopedSearch("recent_7d", joined_since, joined_before)


async def _run_search_pipeline(
    context: ServerContext,
    arguments: dict[str, Any],
    tool_floor_level: str,
    *,
    is_search_tool: bool = False,
) -> tuple[Any, Any, Any, Any, list[Any], ScopedSearch, str, Completeness, bool]:
    account_id = str(arguments["account"])
    folder_path = str(arguments["folder"])
    criteria_raw = arguments.get("criteria") or {}
    scope_arg = arguments.get("scope")

    folder_decision = context.pdp.decide_folder_access(context.caller_id, account_id, folder_path)
    if not folder_decision.allowed:
        raise _DenialException(folder_decision.reason)

    assert folder_decision.folder_policy is not None
    fp = folder_decision.folder_policy

    required_floor = max(level_rank(tool_floor_level), level_rank(req_vis(criteria_raw)))
    max_vis = context.pdp.folder_max_visibility(fp)
    if level_rank(max_vis) < required_floor:
        needed = rank_to_level(required_floor)
        raise _DenialException(f"visibility_below_{needed}")

    account, password = await _password_for(context, account_id)
    imap_folder = await _resolve_imap_folder(context, account_id, folder_path)

    stats = await imap_folder_stats(account, password, imap_folder)
    if stats.kind == "absent":
        raise _FolderAbsentException(f"Folder {folder_path} absent")
    if stats.kind == "select_failed":
        raise _SelectFailedException(stats.imap_response or f"SELECT {folder_path} failed")

    scoped = _scoped_search(criteria_raw, scope_arg)

    try:
        raw_uids, uv = await search_uids_with_validity(
            account, password, imap_folder, scoped.imap_criteria
        )
        if uv is not None and getattr(context, "session_uidvalidity", None) is not None:
            context.session_uidvalidity[(account_id, folder_path)] = uv
    except FolderNotSelectable as exc:
        raise _SelectFailedException(exc.response_text or str(exc)) from exc
    except Exception as exc:
        raise _SelectFailedException(str(exc)) from exc

    # Fast path check (LIM 0023, ADR 0043)
    fast_path_allowed = (
        is_search_tool
        and tool_floor_level == "METADATA"
        and not _is_google_provider(account)
        and fp.mode == "blacklist"
        and len(fp.rules) == 0
        and level_rank(fp.default) >= required_floor
        and scoped.applied_scope == "all_time"
        and not any(
            k in criteria_raw
            for k in (
                "has_attachment",
                "all_recipients_in_domains",
                "newer_than",
                "older_than",
                "since",
                "before",
            )
        )
    )
    if fast_path_allowed:
        sorted_uids = sorted(raw_uids)
        visible_envelopes: list[tuple[Any, MessageFacts, str]] = []
        for u in sorted_uids:
            dummy_env = Envelope(
                uid=u,
                from_address="",
                to_addresses=[],
                subject="",
                message_id="",
                date=None,
                flags=(),
            )
            dummy_facts = _facts_from_envelope(dummy_env)
            visible_envelopes.append((dummy_env, dummy_facts, "folder_default_applied"))
        may_hide = context.pdp.folder_may_hide(account_id, folder_path, fp)
        completeness: Completeness = (
            ("window_and_policy_limited" if may_hide else "window_limited")
            if scoped.applied_scope == "recent_7d"
            else ("policy_may_hide" if may_hide else "complete")
        )
        suppressed_by_policy = False
        top_reason = "folder_default_applied"
        return (
            account,
            imap_folder,
            fp,
            [],
            visible_envelopes,
            scoped,
            top_reason,
            completeness,
            suppressed_by_policy,
        )

    # Envelopes batch fetch
    envelopes = await imap_fetch_envelopes_batch(account, password, imap_folder, raw_uids)
    env_by_uid = {e.uid: e for e in envelopes}

    from ..audit import _now_utc

    now_utc = _now_utc()

    suppressed_by_policy = False
    visible_envelopes = []

    _is_google_provider(account)

    for uid in sorted(raw_uids):
        env = env_by_uid.get(uid)
        if env is None:
            continue
        facts = _facts_from_envelope(env)

        if _is_google_provider(account):
            from dataclasses import replace

            canonical_curr = context.pdp.canonical_folder(account_id, folder_path)
            if (
                canonical_curr not in ("[Gmail]/All Mail", "[Gmail]/Starred", "[Gmail]/Important")
                and canonical_curr not in facts.labels
            ):
                facts = replace(facts, labels=facts.labels + (canonical_curr,))

        if any("provenance" in r.match for r in fp.rules):
            from dataclasses import replace

            from ._common import resolve_provenance_own

            is_own = await resolve_provenance_own(context, account, password, imap_folder, uid, env)
            if is_own:
                facts = replace(facts, provenance_own=True)

        # Effective visibility check
        eff_vis, eff_reason = context.pdp.evaluate_message_effective(
            context.caller_id, account_id, folder_path, facts, now_utc=now_utc
        )

        if level_rank(eff_vis) < required_floor:
            suppressed_by_policy = True
            continue

        # Exact UTC criteria check
        if criteria_raw:
            matches_criteria = all(
                match_single_predicate(k, v, facts=facts, now_utc=now_utc)
                for k, v in criteria_raw.items()
            )
            if not matches_criteria:
                continue

        visible_envelopes.append((env, facts, eff_reason))

    # Derive static completeness (ADR 0043 §2)
    may_hide = context.pdp.folder_may_hide(account_id, folder_path, fp)
    if scoped.applied_scope == "recent_7d":
        completeness = "window_and_policy_limited" if may_hide else "window_limited"
    else:
        completeness = "policy_may_hide" if may_hide else "complete"

    top_reason = (
        "rule_matched"
        if (fp.mode == "whitelist" and visible_envelopes)
        else "folder_default_applied"
    )

    return (
        account,
        imap_folder,
        fp,
        envelopes,
        visible_envelopes,
        scoped,
        top_reason,
        completeness,
        suppressed_by_policy,
    )


class _DenialException(Exception):
    def __init__(self, reason: str):
        self.reason = reason


class _FolderAbsentException(Exception):
    pass


class _SelectFailedException(Exception):
    def __init__(self, detail: str):
        self.detail = detail


async def handle_search(context: ServerContext, arguments: dict[str, Any]) -> SearchResponse:
    account_id = str(arguments["account"])
    folder_path = str(arguments["folder"])
    limit = int(arguments.get("limit") or 50)
    offset = int(arguments.get("offset") or 0)

    try:
        (
            _account,
            _imap_folder,
            _fp,
            _envelopes,
            visible_envelopes,
            scoped,
            top_reason,
            completeness,
            suppressed_by_policy,
        ) = await _run_search_pipeline(context, arguments, "METADATA", is_search_tool=True)
    except _DenialException as e:
        return {
            "decision": "DENY",
            "reason": e.reason,
            "account": account_id,
            "folder": folder_path,
        }
    except _FolderAbsentException:
        return error_envelope(  # type: ignore[return-value]
            error_type="folder_absent",
            detail="Folder does not exist on IMAP server",
            extra={"account": account_id, "folder": folder_path},
        )
    except _SelectFailedException as e:
        return error_envelope(  # type: ignore[return-value]
            error_type="select_failed",
            detail=e.detail,
            extra={"account": account_id, "folder": folder_path},
        )

    all_uids = [env.uid for env, _, _ in visible_envelopes]
    page_uids = all_uids[offset : offset + limit]
    has_more = (offset + limit) < len(all_uids)

    res: SearchResponse = {
        "decision": "ALLOW",
        "result": "OK",
        "reason": top_reason,
        "account": account_id,
        "folder": folder_path,
        "uids": page_uids,
        "total_visible": len(all_uids),
        "returned_count": len(page_uids),
        "page_offset": offset,
        "page_limit": limit,
        "has_more": has_more,
        "applied_scope": scoped.applied_scope,
        "completeness": completeness,
    }
    if suppressed_by_policy:
        res["_suppressed_by_policy"] = True
    return res


async def handle_list_messages(
    context: ServerContext, arguments: dict[str, Any]
) -> ListMessagesResponse:
    account_id = str(arguments["account"])
    folder_path = str(arguments["folder"])
    limit = int(arguments.get("limit") or 20)
    offset = int(arguments.get("offset") or 0)

    try:
        (
            account,
            _imap_folder,
            _fp,
            _envelopes,
            visible_envelopes,
            scoped,
            top_reason,
            completeness,
            suppressed_by_policy,
        ) = await _run_search_pipeline(context, arguments, "METADATA")
    except _DenialException as e:
        return {
            "decision": "DENY",
            "reason": e.reason,
            "account": account_id,
            "folder": folder_path,
        }
    except _FolderAbsentException:
        return error_envelope(  # type: ignore[return-value]
            error_type="folder_absent",
            detail="Folder does not exist on IMAP server",
            extra={"account": account_id, "folder": folder_path},
        )
    except _SelectFailedException as e:
        return error_envelope(  # type: ignore[return-value]
            error_type="select_failed",
            detail=e.detail,
            extra={"account": account_id, "folder": folder_path},
        )

    page_items = visible_envelopes[offset : offset + limit]
    has_more = (offset + limit) < len(visible_envelopes)

    messages: list[dict[str, Any]] = []
    _SYSTEM_FLAGS = frozenset(
        {"\\Seen", "\\Flagged", "\\Draft", "\\Deleted", "\\Recent", "\\Answered"}
    )

    for env, facts, eff_reason in page_items:
        eff_vis, _ = context.pdp.evaluate_message_effective(
            context.caller_id, account_id, folder_path, facts
        )
        vis_rank = level_rank(eff_vis)

        # Tags (user keywords)
        raw_flags = env.flags or ()
        tags = [f for f in raw_flags if f not in _SYSTEM_FLAGS]

        entry: dict[str, Any] = {
            "uid": env.uid,
            "date": env.date,
            "size_bytes": env.size_bytes,
            "tags": tags,
        }

        # Deterministic projection based on eff_vis (ADR 0043 §3.2)
        if vis_rank == level_rank("METADATA"):
            entry["from"] = None
            entry["subject"] = None
            entry["has_attachment"] = None
            entry["redacted_fields"] = ["envelope", "headers", "body", "attachments"]
        elif vis_rank == level_rank("ENVELOPE"):
            entry["from"] = env.from_address
            entry["to"] = env.to_addresses
            entry["subject"] = env.subject
            entry["has_attachment"] = None
            entry["redacted_fields"] = ["headers", "body", "attachments"]
        else:  # BODY or FULL
            entry["from"] = env.from_address
            entry["to"] = env.to_addresses
            entry["subject"] = env.subject
            entry["has_attachment"] = env.has_attachment
            entry["redacted_fields"] = (
                ["attachment_bytes"] if vis_rank == level_rank("BODY") else []
            )

        if _is_google_provider(account):
            obj_id = context.pdp.make_google_object_id(
                context.caller_id, account_id, facts.x_gm_msgid, context.provenance_hmac_key
            )
            if obj_id is not None:
                entry["object_id"] = obj_id

        messages.append(entry)

    res: ListMessagesResponse = {
        "decision": "ALLOW",
        "result": "OK",
        "reason": top_reason,
        "account": account_id,
        "folder": folder_path,
        "messages": messages,
        "total_visible": len(visible_envelopes),
        "returned_count": len(page_items),
        "page_offset": offset,
        "page_limit": limit,
        "has_more": has_more,
        "applied_scope": scoped.applied_scope,
        "completeness": completeness,
    }
    if suppressed_by_policy:
        res["_suppressed_by_policy"] = True
    return res
