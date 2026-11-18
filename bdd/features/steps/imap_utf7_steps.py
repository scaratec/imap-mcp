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

from behave import given, then, use_step_matcher
from behave.runner import Context

from support.imap_fixture import resolve_account

use_step_matcher("parse")


# ------------------------------------------------------------------ Given

@given('the IMAP traffic for "{account_id}" is captured through a proxy')
def step_imap_proxy_capture(context: Context, account_id: str) -> None:
    """Start the MITM proxy in pass-through mode (no fault injection).

    Needed when scenarios want to assert on the IMAP command log to
    verify that wire-level mailbox names are correctly encoded.
    """
    from features.steps.policy_steps import _start_imap_proxy

    _start_imap_proxy(context, account_id)


@given('the IMAP proxy for "{account_id}" injects raw LIST entry "{entry}"')
def step_imap_proxy_inject_list_entry(
    context: Context, account_id: str, entry: str
) -> None:
    try:
        decoded_entry = entry.encode("utf-8").decode("unicode_escape")
    except Exception:
        decoded_entry = entry
    config_path = context.scratch_dir / f"imap-proxy-{account_id}.json"
    if config_path.exists():
        try:
            config = json.loads(config_path.read_text(encoding="utf-8"))
        except Exception:
            config = {}
    else:
        config = {}
    config["inject_list_entry"] = decoded_entry
    config_path.write_text(json.dumps(config), encoding="utf-8")


@given('the IMAP account "{account_id}" exists')
def step_imap_account_exists(context: Context, account_id: str) -> None:
    """Register the account in accounts.yaml without creating any folders."""
    from features.steps.policy_steps import _ensure_account_registered, _ensure_builder

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
    uid = context.imap.find_unique_uid_by_decoded_subject(
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
