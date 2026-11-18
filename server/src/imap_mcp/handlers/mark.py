"""Flag/keyword mutation handlers: mark_seen, bulk_mark_seen, mark_tagged, bulk_mark_tagged.

Adheres to:
- ADR 0044: Normalized response envelope and opaque not_found
- ADR 0045: Write visibility floors (METADATA for seen, ENVELOPE for tagged) and forbidden_flag
- ADR 0048: Pre-effect intent and post-effect outcome audit records
- ADR 0062: Closed reason and error code registry
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, Any, Literal, NotRequired, TypedDict

from ..imap_core import (
    fetch_envelope as imap_fetch_envelope,
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
from ..imap_core import (
    store_flag as imap_store_flag,
)
from ..imap_core import (
    store_flags_batch as imap_store_flags_batch,
)
from ..imap_core import (
    store_keywords as imap_store_keywords,
)
from ..imap_core import (
    store_keywords_batch as imap_store_keywords_batch,
)
from ..policy import level_rank, match_single_predicate, rank_to_level, req_vis
from ._common import (
    _facts_from_envelope,
    _is_google_provider,
    _password_for,
    _resolve_imap_folder,
    error_envelope,
)

if TYPE_CHECKING:
    from ..context import ServerContext


class MarkResponse(TypedDict, total=False):
    decision: Literal["ALLOW", "DENY"]
    result: NotRequired[Literal["OK", "ERROR"]]
    reason: NotRequired[str]
    error: NotRequired[dict[str, str]]
    account: str
    folder: str
    uid: NotRequired[int]
    marked_count: NotRequired[int]
    tagged_count: NotRequired[int]
    applied_scope: NotRequired[str]
    _audit_detail_reason: NotRequired[str]


def _deny_mark(
    *,
    reason: str,
    account: str,
    folder: str,
    uid: int | None = None,
) -> MarkResponse:
    res: MarkResponse = {
        "decision": "DENY",
        "reason": reason,
        "account": account,
        "folder": folder,
    }
    if uid is not None:
        res["uid"] = uid
    return res


def _not_found_mark(
    *, account: str, folder: str, uid: int, reason: str, detail_reason: str
) -> MarkResponse:
    return error_envelope(  # type: ignore[return-value]
        error_type="not_found",
        detail="Message not found",
        reason=reason,
        extra={
            "account": account,
            "folder": folder,
            "uid": uid,
            "_audit_detail_reason": detail_reason,
        },
    )


def _write_audit_intent(context: ServerContext, tool: str, arguments: dict[str, Any]) -> str:
    """Write pre-effect intent record (ADR 0048). Aborts on audit failure."""
    req_id = context.request_id or str(uuid.uuid4())
    if context.audit is not None:
        context.audit.write(
            {
                "request_id": req_id,
                "phase": "intent",
                "caller_id": context.caller_id,
                "tool": tool,
                "decision": "ALLOW",
                "result": "PENDING",
            }
        )
    return req_id


async def handle_mark_seen(context: ServerContext, arguments: dict[str, Any]) -> MarkResponse:
    account_id = str(arguments["account"])
    folder_path = str(arguments["folder"])
    uid = int(arguments["uid"])
    seen = bool(arguments["seen"])

    folder_decision = context.pdp.decide_folder_access(context.caller_id, account_id, folder_path)
    if not folder_decision.allowed:
        return _deny_mark(
            reason=folder_decision.reason, account=account_id, folder=folder_path, uid=uid
        )
    assert folder_decision.folder_policy is not None
    if not folder_decision.folder_policy.mark_seen:
        return _deny_mark(
            reason="capability_missing", account=account_id, folder=folder_path, uid=uid
        )

    account, password = await _password_for(context, account_id)
    imap_folder = await _resolve_imap_folder(context, account_id, folder_path)
    stats = await imap_folder_stats(account, password, imap_folder)
    if stats.kind == "absent":
        return error_envelope(  # type: ignore[return-value]
            error_type="folder_absent",
            detail="Folder does not exist on IMAP server",
            reason=folder_decision.reason,
            extra={"account": account_id, "folder": folder_path, "uid": uid},
        )
    if stats.kind == "select_failed":
        return error_envelope(  # type: ignore[return-value]
            error_type="select_failed",
            detail=stats.imap_response or "SELECT mailbox failed",
            reason=folder_decision.reason,
            extra={"account": account_id, "folder": folder_path, "uid": uid},
        )
    envelope = await imap_fetch_envelope(account, password, imap_folder, uid)
    if envelope is None:
        return _not_found_mark(
            account=account_id,
            folder=folder_path,
            uid=uid,
            reason=folder_decision.reason,
            detail_reason="object_missing",
        )

    facts = _facts_from_envelope(envelope)
    eff_vis, eff_reason = context.pdp.evaluate_message_effective(
        context.caller_id, account_id, folder_path, facts
    )

    if level_rank(eff_vis) < level_rank("METADATA"):
        detail_cause = "message_capped" if eff_reason == "rule_matched" else "message_not_granted"
        return _not_found_mark(
            account=account_id,
            folder=folder_path,
            uid=uid,
            reason=eff_reason,
            detail_reason=detail_cause,
        )

    _write_audit_intent(context, "mark_seen", arguments)
    ok = await imap_store_flag(account, password, imap_folder, uid, r"\Seen", add=seen)
    if not ok:
        return _not_found_mark(
            account=account_id,
            folder=folder_path,
            uid=uid,
            reason=eff_reason,
            detail_reason="object_missing",
        )

    return {
        "decision": "ALLOW",
        "result": "OK",
        "reason": eff_reason,
        "account": account_id,
        "folder": folder_path,
        "uid": uid,
    }


async def handle_bulk_mark_seen(context: ServerContext, arguments: dict[str, Any]) -> MarkResponse:
    from .search import _scoped_search

    account_id = str(arguments["account"])
    folder_path = str(arguments["folder"])
    seen = bool(arguments["seen"])
    criteria_raw = arguments.get("criteria") or {}

    folder_decision = context.pdp.decide_folder_access(context.caller_id, account_id, folder_path)
    if not folder_decision.allowed:
        return _deny_mark(reason=folder_decision.reason, account=account_id, folder=folder_path)
    assert folder_decision.folder_policy is not None
    if not folder_decision.folder_policy.mark_seen:
        return _deny_mark(reason="capability_missing", account=account_id, folder=folder_path)

    tool_floor = level_rank("METADATA")
    required_floor = max(tool_floor, level_rank(req_vis(criteria_raw)))

    max_vis = context.pdp.folder_max_visibility(folder_decision.folder_policy)
    if level_rank(max_vis) < required_floor:
        needed = req_vis(criteria_raw)
        return _deny_mark(
            reason=f"visibility_below_{needed}", account=account_id, folder=folder_path
        )

    account, password = await _password_for(context, account_id)
    imap_folder = await _resolve_imap_folder(context, account_id, folder_path)
    scoped = _scoped_search(criteria_raw, arguments.get("scope"))
    uids = await imap_search_uids(account, password, imap_folder, scoped.imap_criteria)
    if not uids:
        return {
            "decision": "ALLOW",
            "result": "OK",
            "reason": folder_decision.reason,
            "account": account_id,
            "folder": folder_path,
            "marked_count": 0,
            "applied_scope": scoped.applied_scope,
        }

    # Fetch envelopes to filter authorized messages >= required_floor
    envelopes = await imap_fetch_envelopes_batch(account, password, imap_folder, uids)
    eligible_uids: list[int] = []
    for env in envelopes:
        facts = _facts_from_envelope(env)
        # Check criteria
        if criteria_raw and not all(
            match_single_predicate(k, v, facts=facts)
            for k, v in criteria_raw.items()
            if k not in ("since", "before")
        ):
            continue
        eff_vis, _ = context.pdp.evaluate_message_effective(
            context.caller_id, account_id, folder_path, facts
        )
        if level_rank(eff_vis) >= required_floor:
            eligible_uids.append(env.uid)

    if not eligible_uids:
        return {
            "decision": "ALLOW",
            "result": "OK",
            "reason": folder_decision.reason,
            "account": account_id,
            "folder": folder_path,
            "marked_count": 0,
            "applied_scope": scoped.applied_scope,
        }

    _write_audit_intent(context, "bulk_mark_seen", arguments)
    count = await imap_store_flags_batch(
        account, password, imap_folder, eligible_uids, r"\Seen", add=seen
    )
    return {
        "decision": "ALLOW",
        "result": "OK",
        "reason": folder_decision.reason,
        "account": account_id,
        "folder": folder_path,
        "marked_count": count,
        "applied_scope": scoped.applied_scope,
    }


async def handle_mark_tagged(context: ServerContext, arguments: dict[str, Any]) -> MarkResponse:
    account_id = str(arguments["account"])
    folder_path = str(arguments["folder"])
    uid = int(arguments["uid"])
    tags = list(arguments["tags"])
    mode = str(arguments["mode"])

    # Reject any system flag starting with backslash (ADR 0045)
    for tag in tags:
        if tag.startswith("\\"):
            return _deny_mark(
                reason="forbidden_flag",
                account=account_id,
                folder=folder_path,
                uid=uid,
            )

    folder_decision = context.pdp.decide_folder_access(context.caller_id, account_id, folder_path)
    if not folder_decision.allowed:
        return _deny_mark(
            reason=folder_decision.reason, account=account_id, folder=folder_path, uid=uid
        )
    assert folder_decision.folder_policy is not None
    if not folder_decision.folder_policy.mark_tagged:
        return _deny_mark(
            reason="capability_missing", account=account_id, folder=folder_path, uid=uid
        )

    account, password = await _password_for(context, account_id)
    imap_folder = await _resolve_imap_folder(context, account_id, folder_path)
    envelope = await imap_fetch_envelope(account, password, imap_folder, uid)
    if envelope is None:
        return _not_found_mark(
            account=account_id,
            folder=folder_path,
            uid=uid,
            reason=folder_decision.reason,
            detail_reason="object_missing",
        )

    facts = _facts_from_envelope(envelope)
    eff_vis, eff_reason = context.pdp.evaluate_message_effective(
        context.caller_id, account_id, folder_path, facts
    )

    if level_rank(eff_vis) < level_rank("METADATA"):
        detail_cause = "message_capped" if eff_reason == "rule_matched" else "message_not_granted"
        return _not_found_mark(
            account=account_id,
            folder=folder_path,
            uid=uid,
            reason=eff_reason,
            detail_reason=detail_cause,
        )

    if level_rank(eff_vis) < level_rank("ENVELOPE"):
        return _deny_mark(
            reason="visibility_below_ENVELOPE",
            account=account_id,
            folder=folder_path,
            uid=uid,
        )

    # Google label addition/removal authorization check (ADR 0045 §1)
    if _is_google_provider(account):
        for tag in tags:
            tag_canonical = context.pdp.canonical_folder(account_id, tag)
            if mode in ("add", "replace"):
                fp_target = context.pdp.folder_policy(context.caller_id, account_id, tag_canonical)
                if fp_target is None or not fp_target.accept_incoming:
                    return _deny_mark(
                        reason="capability_missing",
                        account=account_id,
                        folder=folder_path,
                        uid=uid,
                    )
                eff_target_vis, _ = context.pdp.evaluate_message_effective(
                    context.caller_id, account_id, tag_canonical, facts
                )
                if level_rank(eff_target_vis) > level_rank(eff_vis):
                    return _deny_mark(
                        reason="relocation_would_escalate",
                        account=account_id,
                        folder=folder_path,
                        uid=uid,
                    )
            if mode in ("remove", "replace"):
                fp_src = context.pdp.folder_policy(context.caller_id, account_id, tag_canonical)
                if fp_src is None or not fp_src.move_out:
                    return _deny_mark(
                        reason="capability_missing",
                        account=account_id,
                        folder=folder_path,
                        uid=uid,
                    )

    _write_audit_intent(context, "mark_tagged", arguments)
    ok = await imap_store_keywords(account, password, imap_folder, uid, tags, mode=mode)
    if not ok:
        return _not_found_mark(
            account=account_id,
            folder=folder_path,
            uid=uid,
            reason=eff_reason,
            detail_reason="object_missing",
        )

    return {
        "decision": "ALLOW",
        "result": "OK",
        "reason": eff_reason,
        "account": account_id,
        "folder": folder_path,
        "uid": uid,
    }


async def handle_bulk_mark_tagged(
    context: ServerContext, arguments: dict[str, Any]
) -> MarkResponse:
    from .search import _scoped_search

    account_id = str(arguments["account"])
    folder_path = str(arguments["folder"])
    tags = list(arguments["tags"])
    mode = str(arguments["mode"])
    criteria_raw = arguments.get("criteria") or {}

    for tag in tags:
        if tag.startswith("\\"):
            return _deny_mark(
                reason="forbidden_flag",
                account=account_id,
                folder=folder_path,
            )

    folder_decision = context.pdp.decide_folder_access(context.caller_id, account_id, folder_path)
    if not folder_decision.allowed:
        return _deny_mark(reason=folder_decision.reason, account=account_id, folder=folder_path)
    assert folder_decision.folder_policy is not None
    if not folder_decision.folder_policy.mark_tagged:
        return _deny_mark(reason="capability_missing", account=account_id, folder=folder_path)

    tool_floor = level_rank("ENVELOPE")
    required_floor = max(tool_floor, level_rank(req_vis(criteria_raw)))

    max_vis = context.pdp.folder_max_visibility(folder_decision.folder_policy)
    if level_rank(max_vis) < required_floor:
        needed = rank_to_level(required_floor)
        return _deny_mark(
            reason=f"visibility_below_{needed}", account=account_id, folder=folder_path
        )

    account, password = await _password_for(context, account_id)
    imap_folder = await _resolve_imap_folder(context, account_id, folder_path)
    scoped = _scoped_search(criteria_raw, arguments.get("scope"))
    uids = await imap_search_uids(account, password, imap_folder, scoped.imap_criteria)
    if not uids:
        return {
            "decision": "ALLOW",
            "result": "OK",
            "reason": folder_decision.reason,
            "account": account_id,
            "folder": folder_path,
            "tagged_count": 0,
            "applied_scope": scoped.applied_scope,
        }

    envelopes = await imap_fetch_envelopes_batch(account, password, imap_folder, uids)
    eligible_uids: list[int] = []
    for env in envelopes:
        facts = _facts_from_envelope(env)
        if criteria_raw and not all(
            match_single_predicate(k, v, facts=facts)
            for k, v in criteria_raw.items()
            if k not in ("since", "before")
        ):
            continue
        eff_vis, _ = context.pdp.evaluate_message_effective(
            context.caller_id, account_id, folder_path, facts
        )
        if level_rank(eff_vis) >= required_floor:
            eligible_uids.append(env.uid)

    if not eligible_uids:
        return {
            "decision": "ALLOW",
            "result": "OK",
            "reason": folder_decision.reason,
            "account": account_id,
            "folder": folder_path,
            "tagged_count": 0,
            "applied_scope": scoped.applied_scope,
        }

    _write_audit_intent(context, "bulk_mark_tagged", arguments)
    count = await imap_store_keywords_batch(
        account, password, imap_folder, eligible_uids, tags, mode=mode
    )
    return {
        "decision": "ALLOW",
        "result": "OK",
        "reason": folder_decision.reason,
        "account": account_id,
        "folder": folder_path,
        "tagged_count": count,
        "applied_scope": scoped.applied_scope,
    }
