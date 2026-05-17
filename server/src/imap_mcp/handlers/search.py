"""Listing/search handlers: search, list_messages, plus criteria helpers."""

from __future__ import annotations

from typing import Any, TYPE_CHECKING

from ..imap_core import (
    fetch_envelopes_batch as imap_fetch_envelopes_batch,
    gmail_fetch_msgid as imap_gmail_fetch_msgid,
    gmail_search_by_msgid as imap_gmail_search_by_msgid,
    search_uids as imap_search_uids,
)
from ..policy import (
    MessageFacts,
    _match_single_predicate,
    evaluate_message_against_folder,
    level_rank,
)
from ._common import (
    _deny,
    _facts_from_envelope,
    _is_google_provider,
    _password_for,
    _resolve_imap_folder,
)

if TYPE_CHECKING:
    from ..context import ServerContext


def _criteria_match(criteria: dict[str, Any], facts: MessageFacts) -> bool:
    """Post-filter: check all MCP criteria against envelope facts.

    Catches predicates that cannot be expressed as IMAP SEARCH terms
    (e.g. has_attachment) and refines inexact IMAP matches.
    """
    return all(_match_single_predicate(key, value, facts=facts) for key, value in criteria.items())


def _criteria_to_imap_search(criteria: dict[str, Any]) -> str:
    """Translate MCP search criteria dict to an IMAP SEARCH string.

    Empty criteria (after this function returns "ALL") receive a 7-day
    default scope in the caller — this function only handles explicit
    predicates.
    """
    parts: list[str] = []
    for key, value in criteria.items():
        if key == "from":
            parts.append(f'FROM "{value}"')
        elif key == "from_domain":
            parts.append(f'FROM "@{value}"')
        elif key == "to":
            parts.append(f'TO "{value}"')
        elif key == "to_contains":
            parts.append(f'TO "{value}"')
        elif key == "subject_contains":
            parts.append(f'SUBJECT "{value}"')
        elif key == "newer_than":
            from datetime import timedelta

            from ..audit import _now_utc

            days = int(str(value).rstrip("d"))
            since = _now_utc() - timedelta(days=days)
            parts.append(f"SINCE {since.strftime('%d-%b-%Y')}")
        elif key == "older_than":
            from datetime import timedelta

            from ..audit import _now_utc

            days = int(str(value).rstrip("d"))
            before = _now_utc() - timedelta(days=days)
            parts.append(f"BEFORE {before.strftime('%d-%b-%Y')}")
        elif key == "size_gt":
            parts.append(f"LARGER {int(value)}")
        elif key == "size_lt":
            parts.append(f"SMALLER {int(value)}")
        elif key == "has_attachment":
            pass
    if not parts:
        return "ALL"
    return " ".join(parts)


async def handle_search(context: "ServerContext", arguments: dict[str, Any]) -> dict[str, Any]:
    account_id = str(arguments["account"])
    folder_path = str(arguments["folder"])
    criteria_raw = arguments.get("criteria") or {}
    limit = int(arguments.get("limit") or 50)
    offset = int(arguments.get("offset") or 0)
    base = {"account": account_id, "folder": folder_path}
    folder_decision = context.pdp.decide_folder_access(context.caller_id, account_id, folder_path)
    if not folder_decision.allowed:
        return _deny(reason=folder_decision.reason, **base)
    assert folder_decision.folder_policy is not None
    minimum_for_tool = level_rank("METADATA")
    if level_rank(folder_decision.visibility) < minimum_for_tool and not any(
        level_rank(rule.grant) >= minimum_for_tool  # type: ignore[arg-type]
        for rule in folder_decision.folder_policy.rules
        if rule.grant is not None
    ):
        if folder_decision.folder_policy.mode == "whitelist":
            return _deny(reason="visibility_below_METADATA", **base)

    imap_criteria = _criteria_to_imap_search(criteria_raw)
    applied_default_scope = False
    if imap_criteria == "ALL" and not criteria_raw:
        from datetime import timedelta

        from ..audit import _now_utc

        since = _now_utc() - timedelta(days=7)
        imap_criteria = f"SINCE {since.strftime('%d-%b-%Y')}"
        applied_default_scope = True

    account, password = await _password_for(context, account_id)
    imap_folder = await _resolve_imap_folder(context, account_id, folder_path)
    all_uids = await imap_search_uids(account, password, imap_folder, imap_criteria)
    matched_total = len(all_uids)

    fp = folder_decision.folder_policy
    pdp_predetermined = (
        fp.mode == "blacklist" and not fp.rules and level_rank(fp.default) >= minimum_for_tool
    )
    criteria_needs_envelope = criteria_raw and any(
        k
        not in (
            "newer_than",
            "older_than",
            "from",
            "from_domain",
            "to",
            "to_contains",
            "subject_contains",
            "size_gt",
            "size_lt",
        )
        for k in criteria_raw
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

    results_with_gmail: list[dict[str, Any]] | None = None
    account_obj = context.account_by_id(account_id)
    if account_obj is not None and _is_google_provider(account_obj) and page and len(page) <= 10:
        results_with_gmail = []
        for vuid in page:
            entry: dict[str, Any] = {"uid": vuid}
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
            except Exception:
                pass
            results_with_gmail.append(entry)

    result: dict[str, Any] = {
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
    }
    if applied_default_scope:
        result["default_scope"] = "newer_than_7d"
    if results_with_gmail is not None:
        result["gmail_results"] = results_with_gmail
    return result


async def handle_list_messages(
    context: "ServerContext", arguments: dict[str, Any]
) -> dict[str, Any]:
    account_id = str(arguments["account"])
    folder_path = str(arguments["folder"])
    criteria_raw = arguments.get("criteria") or {}
    limit = int(arguments.get("limit") or 20)
    offset = int(arguments.get("offset") or 0)

    base = {"account": account_id, "folder": folder_path}
    folder_decision = context.pdp.decide_folder_access(context.caller_id, account_id, folder_path)
    if not folder_decision.allowed:
        return _deny(reason=folder_decision.reason, **base)
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
        return _deny(reason="visibility_below_METADATA", **base)

    imap_criteria = _criteria_to_imap_search(criteria_raw)
    applied_default_scope = False
    if imap_criteria == "ALL" and not criteria_raw:
        from datetime import timedelta

        from ..audit import _now_utc

        since = _now_utc() - timedelta(days=7)
        imap_criteria = f"SINCE {since.strftime('%d-%b-%Y')}"
        applied_default_scope = True

    account, password = await _password_for(context, account_id)
    imap_folder = await _resolve_imap_folder(context, account_id, folder_path)
    all_uids = await imap_search_uids(account, password, imap_folder, imap_criteria)
    matched_total = len(all_uids)

    pdp_predetermined = (
        fp.mode == "blacklist" and not fp.rules and level_rank(fp.default) >= minimum_for_tool
    )
    criteria_needs_envelope = criteria_raw and any(
        k
        not in (
            "newer_than",
            "older_than",
            "from",
            "from_domain",
            "to",
            "to_contains",
            "subject_contains",
            "size_gt",
            "size_lt",
        )
        for k in criteria_raw
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

    messages = []
    for uid in page_uids:
        env = envelope_by_uid.get(uid)
        if env is None:
            continue
        messages.append(
            {
                "uid": env.uid,
                "from": env.from_address,
                "to": env.to_addresses,
                "subject": env.subject,
                "date": env.date,
                "has_attachment": env.has_attachment,
                "size_bytes": env.size_bytes,
            }
        )

    result: dict[str, Any] = {
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
    }
    if applied_default_scope:
        result["default_scope"] = "newer_than_7d"
    return result
