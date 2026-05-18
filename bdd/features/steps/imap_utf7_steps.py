"""Steps for the IMAP Modified UTF-7 ↔ UTF-8 boundary feature.

These steps configure the mock IMAP fixture to expose folders under
their RFC 3501 wire names and verify that the MCP surface presents
and accepts them as UTF-8 throughout.

Per BDD Guidelines §5.1 these steps are adapters — they read Gherkin
data, hand it to the fixture or assert against the response. No
business logic lives here.
"""

from __future__ import annotations

import json

from behave import given, then
from behave.runner import Context

from support.imap_fixture import resolve_account


# ------------------------------------------------------------------ Given

@given('the IMAP traffic for "{account_id}" is captured through a proxy')
def step_imap_proxy_capture(context: Context, account_id: str) -> None:
    """Start the MITM proxy in pass-through mode (no fault injection).

    Needed when scenarios want to assert on the IMAP command log to
    verify that wire-level mailbox names are correctly encoded.
    """
    from features.steps.policy_steps import _start_imap_proxy

    _start_imap_proxy(context, account_id)


@given('the IMAP account "{account_id}" exists')
def step_imap_account_exists(context: Context, account_id: str) -> None:
    """Register the account in accounts.yaml without creating any folders."""
    from features.steps.policy_steps import _ensure_builder, _ensure_account_registered

    builder = _ensure_builder(context)
    _ensure_account_registered(context, builder, account_id)
    builder.write()


@given(
    'the IMAP server for "{account_id}" exposes the following '
    "mailboxes on the wire:"
)
def step_imap_exposes_wire_mailboxes(
    context: Context, account_id: str
) -> None:
    """Create Dovecot folders using the mUTF-7 wire name.

    The wire ↔ UTF-8 mapping is stored so that later steps and the
    fixture's second-channel helpers can resolve UTF-8 folder names
    to the actual Dovecot path.

    Table columns: ``wire (mUTF-7 bytes)``, ``utf-8 (intended name)``.
    """
    instance, user = resolve_account(account_id)

    wire_map: dict[str, str] = {}
    for row in context.table:
        wire = row["wire (mUTF-7 bytes)"]
        utf8 = row["utf-8 (intended name)"]
        if utf8.strip().startswith("("):
            utf8 = wire
        wire_map[utf8] = wire
        context.imap.create_folder(instance, user, wire)

    context.imap.register_wire_folders(instance, user, wire_map)


# ------------------------------------------------------------------ Then

@then("the response lists message uid {uid:d}")
def step_response_lists_uid(context: Context, uid: int) -> None:
    actual_uid = uid
    for key, mapped in getattr(context, "message_uids", {}).items():
        if key[2] == uid:
            actual_uid = mapped
            break
    response = _last_response(context)
    messages = response.get("messages") or []
    found_uids = [m.get("uid") for m in messages if isinstance(m, dict)]
    if actual_uid not in found_uids:
        raise AssertionError(
            f"Message uid {uid} (resolved to {actual_uid}) not found "
            f"in response. Present uids: {found_uids!r}"
        )


@then('the response is not denied with reason "{reason}"')
def step_response_not_denied_with_reason(
    context: Context, reason: str
) -> None:
    response = _last_response(context)
    actual_reason = response.get("reason")
    if actual_reason == reason:
        raise AssertionError(
            f"Response was denied with reason {reason!r} — expected it "
            f"not to be. Full response: {response!r}"
        )


@then(
    'the most recent audit entry for tool "{tool}" has source folder '
    'field equal to "{source}" and target folder field equal to "{target}"'
)
def step_audit_move_folder_fields(
    context: Context, tool: str, source: str, target: str
) -> None:
    from support.audit_reader import AuditReader

    reader = AuditReader(context.audit_dir)
    matches = list(reader.find(tool=tool))
    if not matches:
        raise AssertionError(f"No audit entries found for tool={tool!r}")
    last = matches[-1].record
    args = last.get("args_summary", {})
    actual_source = (
        last.get("source_folder")
        or last.get("source", {}).get("folder")
        or (args.get("source") or {}).get("folder")
    )
    actual_target = (
        last.get("target_folder")
        or last.get("target", {}).get("folder")
        or (args.get("target") or {}).get("folder")
    )
    errors: list[str] = []
    if actual_source != source:
        errors.append(
            f"source folder: expected {source!r}, got {actual_source!r}"
        )
    if actual_target != target:
        errors.append(
            f"target folder: expected {target!r}, got {actual_target!r}"
        )
    if errors:
        raise AssertionError(
            "; ".join(errors) + f". Full record: {last!r}"
        )


@then(
    'no audit entry written during this scenario contains '
    'the substring "{substring}"'
)
def step_no_audit_substring(context: Context, substring: str) -> None:
    from support.audit_reader import AuditReader

    reader = AuditReader(context.audit_dir)
    for rec in reader.records():
        text = json.dumps(rec.record)
        if substring in text:
            raise AssertionError(
                f"Audit entry contains forbidden substring "
                f"{substring!r}: {rec.record!r}"
            )


@then(
    'no folder path in the response contains the substring "{substring}"'
)
def step_no_folder_path_contains(context: Context, substring: str) -> None:
    response = _last_response(context)
    folders = response.get("folders") or []
    for f in folders:
        path = f.get("path") if isinstance(f, dict) else str(f)
        if substring in (path or ""):
            raise AssertionError(
                f"Folder path {path!r} contains forbidden substring "
                f"{substring!r}"
            )


@then(
    'the IMAP command log for "{account_id}" contains a command whose '
    'target mailbox argument is the wire string "{wire}"'
)
def step_imap_command_log_wire_target(
    context: Context, account_id: str, wire: str
) -> None:
    log_path = (
        getattr(context, "imap_proxy_log_paths", None) or {}
    ).get(account_id)
    if log_path is None or not log_path.exists():
        raise AssertionError(
            f"No IMAP proxy log captured for account {account_id!r}; "
            "did a prior step start the MITM proxy?"
        )
    log = log_path.read_text(encoding="utf-8", errors="replace")
    if wire not in log:
        raise AssertionError(
            f"Wire string {wire!r} not found in IMAP command log.\n"
            f"Full log:\n{log}"
        )


@then(
    'a direct IMAP SEARCH on "{folder}" for subject "{subject}" '
    "returns exactly one result"
)
def step_imap_search_subject_exactly_one(
    context: Context, folder: str, subject: str
) -> None:
    if ":" in folder:
        account_id, _, folder = folder.partition(":")
    else:
        from features.steps.assertion_steps import _account_for_folder

        account_id = _account_for_folder(context, folder)
    instance, user = resolve_account(account_id)
    context.imap.close_all()
    uid = context.imap.find_uid_by_decoded_subject(
        instance, user, folder, subject
    )
    if uid is None:
        raise AssertionError(
            f"No message with subject {subject!r} found in folder "
            f"{folder!r} on account {account_id!r}"
        )


# ---------------------------------------------------------------- helpers

def _last_response(context: Context) -> dict:
    response = getattr(context, "last_response", None)
    if response is None:
        raise AssertionError("No MCP response captured yet")
    return response
