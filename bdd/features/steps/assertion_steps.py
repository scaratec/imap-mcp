"""Assertion steps — structural comparisons against the stored response.

These steps read the most recent tool response out of
`context.last_response` (populated by mcp_steps) and compare
individual fields to the expected value literal from the feature
file. No business logic, no derivation: equality is equality.

The comparisons are done by parsing the right-hand side of the Then
step as JSON so that feature files can express lists, numbers, nulls
and strings uniformly. For example:

    Then the response field accounts equals ["gupta-scaratec"]
    Then the response field hidden_accounts_count equals 1
    Then the response field reason equals "sender_not_whitelisted"

In all three cases the right-hand side is valid JSON. If a future
scenario needs a value that does not parse as JSON, a new, narrower
step with explicit parsing is added; we do not try to "fix" the
JSON-parsing path heuristically.
"""

from __future__ import annotations

import email.utils
import hashlib
import json
import os
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from behave import given, then, use_step_matcher, when
from behave.runner import Context


def _last_response(context: Context) -> dict[str, Any]:
    response = getattr(context, "last_response", None)
    if response is None:
        raise AssertionError(
            "No MCP tool response has been captured yet. Place a "
            "`When ... calls <tool>` step before any `Then the "
            "response ...` step."
        )
    return response


def _folder_paths(entries: list[Any]) -> list[str]:
    """Extract folder path strings from a folders list.

    Handles both the legacy string format ``["INBOX"]`` and the new
    object format ``[{"path": "INBOX", "message_count": 5}]``.
    """
    return [
        e["path"] if isinstance(e, dict) else e
        for e in entries
    ]


def _parse_expected(raw: str) -> Any:
    m = re.match(r'^the sha256 hex digest of byte "([^"]+)" repeated (\d+) times$', raw.strip())
    if m:
        import hashlib
        b = m.group(1).encode("utf-8")
        n = int(m.group(2))
        return hashlib.sha256(b * n).hexdigest()
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise AssertionError(
            f"Expected value {raw!r} is not valid JSON: {exc}. "
            "Scenario Then-values must be expressible as JSON "
            "(string in quotes, list, number, true/false/null)."
        )


def _resolve_dotted_field(payload: dict, field: str) -> tuple[bool, object]:
    """Resolve a dotted path like `error.type` against a nested response."""
    parts = field.split(".")
    cursor: object = payload
    for part in parts:
        if not isinstance(cursor, dict) or part not in cursor:
            return False, None
        cursor = cursor[part]
    return True, cursor


@then("the response field {field} equals in exact order {expected}")
def step_response_field_equals_in_exact_order(context: Context, field: str, expected: str) -> None:
    response = _last_response(context)
    found, actual = _resolve_dotted_field(response, field)
    if not found:
        raise AssertionError(
            f"Response has no field {field!r}. Available top-level fields: "
            f"{sorted(response.keys())}"
        )
    expected_value = _parse_expected(expected)
    if field == "uids" and isinstance(expected_value, list):
        expected_value = _resolve_uid_hints(context, expected_value)
    if actual != expected_value:
        raise AssertionError(
            f"Field {field!r}: expected in exact order {expected_value!r}, got {actual!r}"
        )


@then("the response field {field} equals {expected}")
def step_response_field_equals(context: Context, field: str, expected: str) -> None:
    response = _last_response(context)
    found, actual = _resolve_dotted_field(response, field)
    if not found:
        raise AssertionError(
            f"Response has no field {field!r}. Available top-level fields: "
            f"{sorted(response.keys())}"
        )
    expected_value = _parse_expected(expected)
    # UID translation: feature-file hints (e.g. [201, 202]) are not the
    # server-side UIDs IMAP assigns; the seed step records the mapping
    # in context.message_uids, and this assertion resolves the hint so
    # the feature file stays readable.
    if field == "uids" and isinstance(expected_value, list):
        expected_value = _resolve_uid_hints(context, expected_value)
    if field == "folders" and isinstance(actual, list) and isinstance(expected_value, list):
        actual = _folder_paths(actual)
    if actual != expected_value:
        raise AssertionError(
            f"Field {field!r}: expected {expected_value!r}, got {actual!r}"
        )


@then('the response field {field} contains key "{key}" equal to "{expected}"')
def step_response_field_contains_key_equal(
    context: Context, field: str, key: str, expected: str
) -> None:
    response = _last_response(context)
    found, actual = _resolve_dotted_field(response, field)
    if not found or not isinstance(actual, dict):
        raise AssertionError(
            f"Response has no dict field {field!r}. Available top-level fields: "
            f"{sorted(response.keys())}"
        )
    key_lower = key.lower()
    matched_k = None
    for k in actual:
        if k.lower() == key_lower:
            matched_k = k
            break
    if matched_k is None:
        raise AssertionError(
            f"Field {field!r} does not contain key {key!r}. Available keys: {list(actual.keys())}"
        )
    actual_val = str(actual[matched_k])
    if actual_val != expected:
        raise AssertionError(
            f"Field {field!r}[{matched_k!r}]: expected {expected!r}, got {actual_val!r}"
        )


@then("the visible account ids equal {expected}")
def step_visible_account_ids_equal(context: Context, expected: str) -> None:
    response = _last_response(context)
    accounts = response.get("accounts", [])
    actual = [a["id"] if isinstance(a, dict) else a for a in accounts]
    expected_value = _parse_expected(expected)
    if actual != expected_value:
        raise AssertionError(
            f"Visible account IDs: expected {expected_value!r}, got {actual!r}"
        )


@then('the IMAP message at "{folder}" uid {uid:d} has flag "{flag}"')
def step_imap_message_has_flag(
    context: Context, folder: str, uid: int, flag: str
) -> None:
    account_id = _account_for_folder(context, folder)
    from support.imap_fixture import resolve_account, resolve_uid

    instance, user = resolve_account(account_id)
    actual_uid = resolve_uid(context, account_id, folder, uid)
    flags = context.imap.fetch_flags(instance, user, folder, actual_uid)
    if flag not in flags:
        raise AssertionError(
            f"Message at {folder!r} uid {actual_uid} flags {flags!r} do not "
            f"include {flag!r}"
        )


@then('the IMAP message at "{folder}" uid {uid:d} does NOT have flag "{flag}"')
@then('the IMAP message at "{folder}" uid {uid:d} does not have flag "{flag}"')
def step_imap_message_does_not_have_flag(
    context: Context, folder: str, uid: int, flag: str
) -> None:
    account_id = _account_for_folder(context, folder)
    from support.imap_fixture import resolve_account, resolve_uid

    instance, user = resolve_account(account_id)
    actual_uid = resolve_uid(context, account_id, folder, uid)
    flags = context.imap.fetch_flags(instance, user, folder, actual_uid)
    if flag in flags:
        raise AssertionError(
            f"Message at {folder!r} uid {actual_uid} unexpectedly has flag {flag!r}"
        )


@then('the IMAP message at "{folder}" uid {uid:d} has keyword "{keyword}"')
def step_imap_message_has_keyword(
    context: Context, folder: str, uid: int, keyword: str
) -> None:
    step_imap_message_has_flag(context, folder, uid, keyword)


@then('the IMAP message at "{folder}" uid {uid:d} does NOT have keyword "{keyword}"')
def step_imap_message_does_not_have_keyword(
    context: Context, folder: str, uid: int, keyword: str
) -> None:
    step_imap_message_does_not_have_flag(context, folder, uid, keyword)




@then(
    'the IMAP folder "{folder}" does not contain a message with subject "{subject}"'
)
def step_imap_folder_does_not_contain_subject(
    context: Context, folder: str, subject: str
) -> None:
    _assert_subject_presence(context, folder, subject, should_exist=False, exact_count=None)


@then(
    'the IMAP folder "{folder}" contains exactly one message with subject "{subject}"'
)
def step_imap_folder_contains_exactly_one_subject(
    context: Context, folder: str, subject: str
) -> None:
    _assert_subject_presence(context, folder, subject, should_exist=True, exact_count=1)


_COUNT_WORDS = {
    "zero": 0,
    "one": 1,
    "two": 2,
    "exactly one": 1,
    "exactly two": 2,
    "no": 0,
}


@then(
    'a direct IMAP SEARCH on "{folder}" for message-id "{message_id}" '
    "returns {count} result"
)
def step_imap_search_message_id_single(
    context: Context, folder: str, message_id: str, count: str
) -> None:
    _assert_message_id_search(
        context, folder, message_id, _resolve_count(count)
    )


@then(
    'a direct IMAP SEARCH on "{folder}" for message-id "{message_id}" '
    "returns {count} results"
)
def step_imap_search_message_id_multi(
    context: Context, folder: str, message_id: str, count: str
) -> None:
    _assert_message_id_search(
        context, folder, message_id, _resolve_count(count)
    )


def _resolve_count(count: str) -> int:
    if count in _COUNT_WORDS:
        return _COUNT_WORDS[count]
    try:
        return int(count)
    except ValueError:
        raise AssertionError(f"Cannot interpret count word {count!r}")


def _assert_message_id_search(
    context: Context, folder: str, message_id: str, expected: int
) -> None:
    if ":" in folder:
        account_id, _, folder = folder.partition(":")
    else:
        account_id = _account_for_folder(context, folder)
    from support.imap_fixture import resolve_account

    instance, user = resolve_account(account_id)
    uids = context.imap.search_by_message_id(instance, user, folder, message_id)
    if len(uids) != expected:
        raise AssertionError(
            f"SEARCH on {folder!r} for message-id {message_id!r}: "
            f"expected {expected}, got {len(uids)}: {uids!r}"
        )






@then("the response field {field} is a non-empty string")
def step_response_field_non_empty_string(context: Context, field: str) -> None:
    """For server-assigned opaque values — a transaction id, a draft
    handle — where the scenario cannot name the value but can insist
    that one was handed out."""
    response = _last_response(context)
    found, value = _resolve_dotted_field(response, field)
    if not found:
        raise AssertionError(
            f"Response has no field {field!r}. Available top-level fields: "
            f"{sorted(response.keys())}"
        )
    if not isinstance(value, str) or not value:
        raise AssertionError(f"Field {field!r} is not a non-empty string: {value!r}")


@then('the status response field state equals "{state}"')
def step_status_state_equals(context: Context, state: str) -> None:
    response = _last_response(context)
    actual = response.get("state")
    if actual != state:
        raise AssertionError(
            f"Status state: expected {state!r}, got {actual!r}. Full: {response!r}"
        )


@then("the WAL transactions table has an entry with:")
def step_wal_has_entry(context: Context) -> None:
    from support.wal_reader import WALReader

    reader = WALReader(context.wal_path)
    txs = reader.all_transactions()
    if not txs:
        raise AssertionError("WAL has no transactions")
    # Resolve the expected dict, translating placeholder values.
    expected: dict[str, object] = {}
    tx_id_value = getattr(context, "last_tx_id", None) or (
        context.last_response.get("tx_id") if context.last_response else None
    )
    getattr(context, "message_uids", {})
    # Determine src_account/src_folder up front so we can translate src_uid hints.
    src_account_hint = None
    src_folder_hint = None
    for row in context.table:
        if row["field"] == "src_account":
            src_account_hint = row["value"]
        elif row["field"] == "src_folder":
            src_folder_hint = row["value"]
    for row in context.table:
        field = row["field"]
        raw = row["value"]
        if raw == "the returned tx_id":
            expected[field] = tx_id_value
        elif raw == "(null)":
            expected[field] = None
        elif field == "src_uid" and raw.isdigit():
            hint = int(raw)
            resolved_acc = src_account_hint or getattr(context, "last_call_account", None)
            resolved_fld = src_folder_hint or getattr(context, "last_call_folder", None)
            if not resolved_acc or not resolved_fld:
                resp = getattr(context, "last_response", None) or {}
                resolved_acc = resolved_acc or resp.get("account")
                resolved_fld = resolved_fld or resp.get("folder")
            if resolved_acc and resolved_fld:
                from support.imap_fixture import resolve_uid

                expected[field] = resolve_uid(context, resolved_acc, resolved_fld, hint)
            else:
                raise AssertionError(
                    f"Cannot resolve src_uid hint {hint} in WAL entry assertion: "
                    "source account and folder are not specified in the table or context"
                )
        elif raw.isdigit():
            expected[field] = int(raw)
        else:
            expected[field] = raw
    matches = []
    for tx in txs:
        tx_dict = {
            "tx_id": tx.tx_id,
            "status": tx.status,
            "src_account": tx.src_account,
            "src_folder": tx.src_folder,
            "src_uid": tx.src_uid,
            "dst_account": tx.dst_account,
            "dst_folder": tx.dst_folder,
            "message_id": tx.message_id,
            "retry_count": tx.retry_count,
            "caller_id": tx.caller_id,
            "staged_rfc822": tx.staged_rfc822,
        }
        if all(tx_dict.get(k) == v for k, v in expected.items()):
            matches.append(tx_dict)
    if not matches:
        raise AssertionError(
            f"No WAL transaction matches {expected!r}. "
            f"Present: {[(t.tx_id, t.status) for t in txs]!r}"
        )


# Regex, not parse: a parse `{folder}` would also swallow
# `INBOX/Drafts" on "info-bg` and bind the account-qualified form below
# to the wrong folder name.
use_step_matcher("re")


@then(r'the IMAP folder "(?P<folder>[^"]+)" still contains uid (?P<uid>\d+)')
def step_imap_folder_still_contains_uid(
    context: Context, folder: str, uid: str
) -> None:
    _assert_uid_presence(context, folder, int(uid), should_exist=True)




@then(
    r'a direct IMAP SEARCH on "(?P<qualified>[^"]+)" for header (?P<header>[A-Za-z0-9-]+) '
    r"equal to the returned draft_id returns (?P<count>.+?) results?"
)
def step_direct_header_search_returned_draft_id(
    context: Context, qualified: str, header: str, count: str
) -> None:
    draft_id = getattr(context, "last_draft_id", None)
    if not draft_id:
        raise AssertionError(
            "No draft_id has been returned in this scenario yet; "
            "the step needs a preceding create_draft or create_reply_draft."
        )
    _assert_direct_header_search(context, qualified, header, draft_id, count)




use_step_matcher("parse")


def _count_phrase(phrase: str) -> int:
    """`zero`, `exactly one`, `exactly 2` and the like, as written."""
    words = {"zero": 0, "no": 0, "one": 1, "two": 2}
    text = phrase.strip()
    text = text.removeprefix("exactly ")
    if text in words:
        return words[text]
    if text.isdigit():
        return int(text)
    raise AssertionError(f"Cannot interpret count phrase {phrase!r}")


def _assert_direct_header_search(
    context: Context, qualified: str, header: str, value: str, count: str
) -> None:
    """Second channel (BDD Guidelines §13.2): SEARCH the IMAP server
    itself, under the folder name that server uses, for messages whose
    `header` equals `value`."""
    from support.imap_fixture import resolve_account

    account_id, sep, folder = qualified.partition(":")
    if not sep:
        raise AssertionError(
            f"Expected an \"account:folder\" qualified name, got {qualified!r}"
        )
    expected = _count_phrase(count)
    instance, user = resolve_account(account_id)
    hits = context.imap.search_header(instance, user, folder, header, value)
    if len(hits) != expected:
        raise AssertionError(
            f"SEARCH HEADER {header} {value!r} on {qualified!r}: expected "
            f"{expected} message(s), found {len(hits)} (UIDs {hits!r})"
        )






@then('the IMAP folder "{folder}" does not contain uid {uid:d}')
def step_imap_folder_does_not_contain_uid(
    context: Context, folder: str, uid: int
) -> None:
    _assert_uid_presence(context, folder, uid, should_exist=False)


def _assert_subject_presence(
    context: Context,
    folder: str,
    subject: str,
    *,
    should_exist: bool,
    exact_count: int | None,
) -> None:
    import imaplib as _imaplib

    from support.imap_fixture import TEST_PASSWORD, resolve_account

    account_id = _account_for_folder(context, folder)
    instance, user = resolve_account(account_id)
    host, port = context.imap_instances[instance]
    conn = _imaplib.IMAP4(host, port)
    try:
        conn.login(user, TEST_PASSWORD)
        wire_folder = context.imap.to_wire(instance, user, folder)
        quoted = context.imap._q(wire_folder)
        status, _ = conn.select(quoted)
        if status != "OK":
            raise AssertionError(
                f"Folder {folder!r} (wire: {wire_folder!r}) is not selectable on {account_id!r}"
            )
        from email.header import decode_header, make_header

        status, data = conn.uid("SEARCH", None, "ALL")
        all_uids = data[0].split() if data and data[0] else []
        matching_uids = []
        for u in all_uids:
            msg = context.imap.fetch_message(instance, user, wire_folder, int(u))
            raw_subj = msg.get("Subject") or ""
            decoded = str(make_header(decode_header(raw_subj)))
            if decoded == subject or raw_subj == subject:
                matching_uids.append(u)
        count = len(matching_uids)
    finally:
        try:
            conn.logout()
        except Exception:
            pass
    if exact_count is not None and count != exact_count:
        raise AssertionError(
            f"Folder {folder!r} contains {count} messages with subject "
            f"{subject!r}; expected exactly {exact_count}"
        )
    if should_exist and count == 0:
        raise AssertionError(
            f"Folder {folder!r} does not contain any message with subject {subject!r}"
        )
    if not should_exist and count > 0:
        raise AssertionError(
            f"Folder {folder!r} unexpectedly contains {count} messages with "
            f"subject {subject!r}"
        )


def _assert_uid_presence(
    context: Context,
    folder: str,
    uid: int,
    *,
    should_exist: bool,
    account_id: str | None = None,
) -> None:
    if account_id is None:
        account_id = _account_for_folder(context, folder)
    from support.imap_fixture import resolve_account

    instance, user = resolve_account(account_id)
    from support.imap_fixture import resolve_uid

    actual_uid = resolve_uid(
        context, account_id, folder, uid, allow_unseeded_probe=not should_exist
    )
    uids = context.imap.folder_uids(instance, user, folder)
    present = actual_uid in uids
    if should_exist and not present:
        raise AssertionError(
            f"Folder {folder!r} does not contain uid {actual_uid}; "
            f"present uids: {uids!r}"
        )
    if not should_exist and present:
        raise AssertionError(
            f"Folder {folder!r} unexpectedly still contains uid {actual_uid}"
        )


def _account_for_folder(context: Context, folder: str) -> str:
    """Mirror of policy_steps._find_account_for_folder, kept local so
    the assertion module is self-contained. Scenarios that exercise
    a folder under a specific account should be unambiguous by this
    point in the run."""
    builder = context.policy_builder
    for account in builder.accounts:
        from support.imap_fixture import resolve_account

        instance, user = resolve_account(account.id)
        folders = context.imap.list_folders(instance, user)
        if folder in folders:
            return account.id
    raise AssertionError(
        f"No configured account has folder {folder!r} on IMAP."
    )


def _resolve_uid_hints(context: Context, hints: list[int]) -> list[int]:
    from support.imap_fixture import resolve_uid

    account = getattr(context, "last_call_account", None)
    folder = getattr(context, "last_call_folder", None)
    if not account or not folder:
        resp = getattr(context, "last_response", None) or {}
        account = account or resp.get("account")
        folder = folder or resp.get("folder")
    resolved: list[int] = []
    for hint in hints:
        if not isinstance(hint, int):
            resolved.append(hint)
            continue
        if account and folder:
            resolved.append(resolve_uid(context, account, folder, hint))
        else:
            raise AssertionError(
                f"Cannot resolve UID hint {hint}: last_call_account and last_call_folder are not set"
            )
    return resolved


@then("the server responds with JSON-RPC error code {code:d}")
def step_server_responds_with_rpc_error(context: Context, code: int) -> None:
    error = getattr(context, "last_rpc_error", None)
    if error is None:
        raise AssertionError(
            f"Expected JSON-RPC error code {code}, but no error captured. "
            f"last_response={getattr(context, 'last_response', None)!r}"
        )
    actual_code = error["code"] if isinstance(error, dict) else error.code
    if actual_code != code:
        raise AssertionError(
            f"Expected JSON-RPC code {code}, got {actual_code}: {error!r}"
        )


@then("the response decision is {decision}")
def step_response_decision_is(context: Context, decision: str) -> None:
    response = _last_response(context)
    actual = response.get("decision")
    if actual != decision:
        raise AssertionError(
            f"Response decision: expected {decision!r}, got {actual!r}. "
            f"Full response: {response!r}"
        )






@then('the response does not contain any field named "{field}"')
def step_response_does_not_contain_field(context: Context, field: str) -> None:
    response = _last_response(context)
    if field in response:
        raise AssertionError(
            f"Response unexpectedly contains field {field!r}: {response[field]!r}"
        )








@then("the JSON response does NOT contain the literal strings:")
def step_json_response_no_literal_strings(context: Context) -> None:
    import json as _json

    text = _json.dumps(_last_response(context))
    for row in context.table:
        needle = row[row.headings[0]]
        if needle in text:
            raise AssertionError(
                f"JSON response contains forbidden literal {needle!r}"
            )


@then('the response field {field} matches the regex "{pattern}"')
@then('the response field {field} matches "{pattern}"')
def step_response_field_matches_regex(
    context: Context, field: str, pattern: str
) -> None:
    import re as _re

    response = _last_response(context)
    value = response.get(field)
    if not isinstance(value, str):
        raise AssertionError(
            f"Field {field!r} is not a string: {value!r}"
        )
    if not _re.match(pattern, value):
        raise AssertionError(
            f"Field {field!r} = {value!r} does not match regex {pattern!r}"
        )


def _table_value_matches(expected: str, actual: object) -> bool:
    """Compare a table cell against a response value.

    The cell is plain text; the response value is typed. The coercion
    is driven by the type the response actually carries, so the cell
    never has to spell out JSON.
    """
    if isinstance(actual, bool):
        if expected.lower() in ("true", "false"):
            return actual is (expected.lower() == "true")
        return False
    if expected.isdigit():
        return actual == int(expected)
    return actual == expected


@then("the response field {field} contains exactly one entry with:")
def step_response_field_contains_exactly_one_entry(
    context: Context, field: str
) -> None:
    """The list holds exactly one entry, and it matches every column.

    Two assertions in one sentence, both load-bearing: the list has
    length 1, *and* that single entry carries the described values. A
    scenario that means "one matching entry among several" says so with
    the `has exactly one entry matching:` step below; it must not borrow
    this one, because the length claim is what several existing
    scenarios bought here.
    """
    response = _last_response(context)
    value = response.get(field)
    if not isinstance(value, list):
        raise AssertionError(
            f"Field {field!r} is not a list; got {value!r}"
        )
    if len(value) != 1:
        raise AssertionError(
            f"Field {field!r}: expected exactly one entry, got {len(value)}: {value!r}"
        )
    entry = value[0]
    for row in context.table:
        key = row["field"]
        expected = row["value"]
        actual = entry.get(key) if isinstance(entry, dict) else None
        if not _table_value_matches(expected, actual):
            raise AssertionError(
                f"{field}[0].{key}: expected {expected!r}, got {actual!r}"
            )


@then("the response field {field} has exactly one entry matching:")
def step_response_field_has_exactly_one_entry_matching(
    context: Context, field: str
) -> None:
    """Exactly one entry of a longer list matches every column.

    The complement of the step above: the list length is deliberately
    *not* pinned here, so a scenario using this one owes a separate
    length assertion if the length matters.
    """
    response = _last_response(context)
    value = response.get(field)
    if not isinstance(value, list):
        raise AssertionError(
            f"Field {field!r} is not a list; got {value!r}"
        )
    expectations = [(row["field"], row["value"]) for row in context.table]
    matches = [
        entry
        for entry in value
        if isinstance(entry, dict)
        and all(_table_value_matches(cell, entry.get(key)) for key, cell in expectations)
    ]
    if len(matches) != 1:
        raise AssertionError(
            f"Field {field!r}: expected exactly one entry matching "
            f"{expectations!r}, found {len(matches)}. Entries: {value!r}"
        )


@then("the returned tool names equal exactly:")
def step_returned_tool_names_equal_exactly(context: Context) -> None:
    tools = getattr(context, "last_tools", [])
    actual = sorted(tool.get("name", "") if isinstance(tool, dict) else tool.name for tool in tools)
    expected = sorted(row["tool"] for row in context.table)
    if actual != expected:
        raise AssertionError(
            f"Tool names mismatch.\nExpected: {expected}\nActual: {actual}"
        )


@then("the returned tool names do NOT contain any of:")
def step_returned_tool_names_no_contain(context: Context) -> None:
    tools = getattr(context, "last_tools", [])
    actual = {tool.get("name", "") if isinstance(tool, dict) else tool.name for tool in tools}
    for row in context.table:
        forbidden = row["tool"]
        if forbidden in actual:
            raise AssertionError(
                f"Tool {forbidden!r} is present but should not be"
            )


def _tool_meta(t: dict[str, Any]) -> dict[str, Any]:
    if "_meta" in t and isinstance(t["_meta"], dict):
        return t["_meta"]
    schema = t.get("inputSchema") or {}
    if "x-mcp-imap" in schema and isinstance(schema["x-mcp-imap"], dict):
        return schema["x-mcp-imap"]
    if "_meta" in schema and isinstance(schema["_meta"], dict):
        return schema["_meta"]
    return {}


@then('the tools/list metadata for each read tool has "minimum_visibility" equal to:')
def step_tools_list_read_tool_min_vis(context: Context) -> None:
    tools_list = getattr(context, "last_tools", None) or context.last_response.get("tools", [])
    by_name = {t.get("name") if isinstance(t, dict) else t.name: t for t in tools_list}
    for row in context.table:
        tool_name = row["tool"]
        expected_vis = row["minimum_visibility"]
        if expected_vis == "(none)":
            expected_vis = None
        if tool_name not in by_name:
            raise AssertionError(f"Tool {tool_name!r} not found in tools/list: {list(by_name.keys())}")
        t_dict = by_name[tool_name] if isinstance(by_name[tool_name], dict) else by_name[tool_name].model_dump()
        meta = _tool_meta(t_dict)
        actual_vis = meta.get("minimum_visibility")
        if actual_vis != expected_vis:
            raise AssertionError(
                f"Tool {tool_name!r} minimum_visibility: expected {expected_vis!r}, got {actual_vis!r}"
            )


@then('the tools/list metadata for each write tool has "required_capabilities" and "minimum_visibility" equal to:')
def step_tools_list_write_tool_meta(context: Context) -> None:
    tools_list = getattr(context, "last_tools", None) or context.last_response.get("tools", [])
    by_name = {t.get("name") if isinstance(t, dict) else t.name: t for t in tools_list}
    for row in context.table:
        tool_name = row["tool"]
        expected_caps = json.loads(row["required_capabilities"])
        expected_vis = row["minimum_visibility"]
        if expected_vis == "(none)":
            expected_vis = None
        if tool_name not in by_name:
            raise AssertionError(f"Tool {tool_name!r} not found in tools/list: {list(by_name.keys())}")
        t_dict = by_name[tool_name] if isinstance(by_name[tool_name], dict) else by_name[tool_name].model_dump()
        meta = _tool_meta(t_dict)
        actual_caps = meta.get("required_capabilities")
        actual_vis = meta.get("minimum_visibility")
        if actual_caps != expected_caps:
            raise AssertionError(
                f"Tool {tool_name!r} required_capabilities: expected {expected_caps!r}, got {actual_caps!r}"
            )
        if actual_vis != expected_vis:
            raise AssertionError(
                f"Tool {tool_name!r} minimum_visibility: expected {expected_vis!r}, got {actual_vis!r}"
            )


@then('each of these tools in tools/list has category "meta" and omits "minimum_visibility" and "required_capabilities":')
def step_tools_list_meta_tools(context: Context) -> None:
    tools_list = getattr(context, "last_tools", None) or context.last_response.get("tools", [])
    by_name = {t.get("name") if isinstance(t, dict) else t.name: t for t in tools_list}
    for row in context.table:
        tool_name = row["tool"]
        if tool_name not in by_name:
            raise AssertionError(f"Tool {tool_name!r} not found in tools/list: {list(by_name.keys())}")
        t_dict = by_name[tool_name] if isinstance(by_name[tool_name], dict) else by_name[tool_name].model_dump()
        meta = _tool_meta(t_dict)
        if meta.get("category") != "meta":
            raise AssertionError(f"Tool {tool_name!r} category: expected 'meta', got {meta.get('category')!r}")
        if "minimum_visibility" in meta:
            raise AssertionError(f"Tool {tool_name!r} unexpectedly defines minimum_visibility: {meta['minimum_visibility']!r}")
        if "required_capabilities" in meta:
            raise AssertionError(f"Tool {tool_name!r} unexpectedly defines required_capabilities: {meta['required_capabilities']!r}")
















def _assert_no_string_anywhere(obj: Any, needle: str) -> None:
    import json as _json

    text = _json.dumps(obj)
    if needle in text:
        raise AssertionError(
            f"Structure unexpectedly contains string {needle!r}: {text!r}"
        )


@then("the audit log contains {n:d} records")
def step_audit_log_record_count(context: Context, n: int) -> None:
    from support.audit_reader import AuditReader

    reader = AuditReader(context.audit_dir)
    recs = list(reader.records())
    if len(recs) != n:
        raise AssertionError(
            f"Expected {n} audit records, found {len(recs)}: {[r.record for r in recs]}"
        )


@then('the audit log contains {n:d} records with reason "{reason}"')
def step_audit_log_record_count_reason(context: Context, n: int, reason: str) -> None:
    from support.audit_reader import AuditReader

    reader = AuditReader(context.audit_dir)
    matches = [r for r in reader.records() if r.record.get("reason") == reason]
    if len(matches) != n:
        raise AssertionError(
            f"Expected {n} records with reason {reason!r}, found {len(matches)}: {[r.record for r in matches]}"
        )


@then('the audit log contains an entry with decision "{decision}", reason "{reason}"')
def step_audit_log_contains_entry_decision_reason(
    context: Context, decision: str, reason: str
) -> None:
    from support.audit_reader import AuditReader

    reader = AuditReader(context.audit_dir)
    matches = [
        r
        for r in reader.records()
        if r.record.get("decision") == decision and r.record.get("reason") == reason
    ]
    if not matches:
        all_recs = [r.record for r in reader.records()]
        raise AssertionError(
            f"Expected audit record with decision={decision!r}, reason={reason!r}, found: {all_recs}"
        )


@then('the audit log contains {n:d} records with tool "{tool}"')
def step_audit_log_record_count_tool(context: Context, n: int, tool: str) -> None:
    from support.audit_reader import AuditReader

    reader = AuditReader(context.audit_dir)
    matches = [r for r in reader.records() if r.record.get("tool") == tool]
    if len(matches) != n:
        raise AssertionError(
            f"Expected {n} records with tool {tool!r}, found {len(matches)}: {[r.record for r in matches]}"
        )




@then(
    'the audit log contains an outcome record for tool "{tool}" with '
    'decision "{decision}" and result "{result}"'
)
def step_audit_log_outcome_tool_decision_result(
    context: Context, tool: str, decision: str, result: str
) -> None:
    from support.audit_reader import AuditReader

    reader = AuditReader(context.audit_dir)
    records = reader.find(tool=tool)
    matches = [
        r.record
        for r in records
        if r.record.get("decision") == decision
        and r.record.get("result") == result
    ]
    if not matches:
        present = [r.record for r in records]
        raise AssertionError(
            f"No audit record for tool={tool!r} with decision={decision!r} and result={result!r}. Found: {present}"
        )


@then(
    'the audit log contains an outcome record for tool "{tool}" with '
    'source folder "{src_folder}" and target folder "{dst_folder}"'
)
def step_audit_log_outcome_source_target_folder(
    context: Context, tool: str, src_folder: str, dst_folder: str
) -> None:
    from support.audit_reader import AuditReader

    reader = AuditReader(context.audit_dir)
    records = reader.find(tool=tool)
    for rec in records:
        r = rec.record
        if r.get("phase") != "outcome":
            continue
        args = r.get("args_summary") or {}
        s = (
            r.get("source_folder")
            or (r.get("extra") or {}).get("source_folder")
            or (args.get("source") or {}).get("folder")
        )
        t = (
            r.get("target_folder")
            or (r.get("extra") or {}).get("target_folder")
            or (args.get("target") or {}).get("folder")
        )
        if s == src_folder and t == dst_folder:
            return
    raise AssertionError(
        f"No audit record found for tool {tool!r} with source_folder={src_folder!r} and target_folder={dst_folder!r}. Found: {[r.record for r in records]}"
    )




use_step_matcher("re")

@then(
    r'the audit log contains an entry with tool "(?P<tool>[^"]+)", '
    r'decision "(?P<decision>[^"]+)", result "(?P<result>[^"]+)", reason "(?P<reason>[^"]+)"'
)
def step_audit_entry_tool_decision_result_reason(
    context: Context, tool: str, decision: str, result: str, reason: str
) -> None:
    from support.audit_reader import AuditReader

    reader = AuditReader(context.audit_dir)
    matches = reader.find(tool=tool, decision=decision, result=result, reason=reason)
    if not matches:
        present = [rec.record for rec in reader.records_today()]
        raise AssertionError(
            f"No audit record with tool={tool!r} decision={decision!r} "
            f"result={result!r} reason={reason!r}. Present: {present!r}"
        )
    context.last_matching_audit_record = matches[0].record


@then(
    r'the audit log contains an entry with tool "(?P<tool>[^"]+)", '
    r'result "(?P<result>[^"]+)", reason "(?P<reason>[^"]+)"'
)
def step_audit_entry_tool_result_reason(
    context: Context, tool: str, result: str, reason: str
) -> None:
    from support.audit_reader import AuditReader

    reader = AuditReader(context.audit_dir)
    matches = reader.find(tool=tool, result=result, reason=reason)
    if not matches:
        present = [rec.record for rec in reader.records_today()]
        raise AssertionError(
            f"No audit record with tool={tool!r} result={result!r} "
            f"reason={reason!r}. Present: {present!r}"
        )
    context.last_matching_audit_record = matches[0].record


@then(
    r'the audit log contains an entry with tool "(?P<tool>[^"]+)", '
    r'decision "(?P<decision>[^"]+)", reason "(?P<reason>[^"]+)"'
)
def step_audit_entry_tool_decision_reason(
    context: Context, tool: str, decision: str, reason: str
) -> None:
    from support.audit_reader import AuditReader

    reader = AuditReader(context.audit_dir)
    matches = reader.find(tool=tool, decision=decision, reason=reason)
    if not matches:
        present = [rec.record for rec in reader.records_today()]
        raise AssertionError(
            f"No audit record with tool={tool!r} decision={decision!r} "
            f"reason={reason!r}. Present: {present!r}"
        )
    context.last_matching_audit_record = matches[0].record


@then(r'the audit log contains an entry with tool "(?P<tool>[^"]+)", reason "(?P<reason>[^"]+)"')
def step_audit_entry_tool_reason(
    context: Context, tool: str, reason: str
) -> None:
    from support.audit_reader import AuditReader

    reader = AuditReader(context.audit_dir)
    matches = reader.find(tool=tool, reason=reason)
    if not matches:
        present = [rec.record for rec in reader.records_today()]
        raise AssertionError(
            f"No audit record with tool={tool!r} reason={reason!r}. Present: {present!r}"
        )
    context.last_matching_audit_record = matches[0].record


@then(
    r'the audit log contains an entry with tool "(?P<tool>[^"]+)", '
    r'decision "(?P<decision>[^"]+)", suppressed_by_policy (?P<suppressed>true|false)'
)
def step_audit_entry_tool_decision_suppressed(
    context: Context, tool: str, decision: str, suppressed: str
) -> None:
    from support.audit_reader import AuditReader

    reader = AuditReader(context.audit_dir)
    is_suppressed = suppressed.lower() == "true"
    records = [rec.record for rec in reader.records_today()]
    matches = [
        r for r in records
        if r.get("tool") == tool and r.get("decision") == decision and r.get("suppressed_by_policy") == is_suppressed
    ]
    if not matches:
        raise AssertionError(
            f"No audit record with tool={tool!r} decision={decision!r} "
            f"suppressed_by_policy={is_suppressed}. Present: {records!r}"
        )
    context.last_matching_audit_record = matches[0]


use_step_matcher("parse")




def _field_matches(actual: Any, expected_str: str) -> bool:
    if isinstance(actual, bool):
        if expected_str.lower() == "true":
            return actual is True
        if expected_str.lower() == "false":
            return actual is False
    if actual is None:
        return expected_str.lower() in ("none", "null")
    return str(actual) == expected_str


@then('file "{filename}" contains a JSONL record whose fields equal:')
@then('file "{filename}" contains a JSONL record with:')
@then('file "{filename}" contains a record with:')
def step_file_contains_jsonl_record(context: Context, filename: str) -> None:
    from support.audit_reader import AuditReader

    expected = {row["field"]: row["value"] for row in context.table}
    reader = AuditReader(context.audit_dir)
    records = reader.read_file(filename)
    matches = [
        rec
        for rec in records
        if all(_field_matches(rec.record.get(k), v) for k, v in expected.items())
    ]
    if not matches:
        present = [rec.record for rec in records]
        raise AssertionError(
            f"No audit record in {filename!r} matches {expected!r}. Present: {present!r}"
        )
    context.last_matching_audit_record = matches[0].record


@then('that record has field "{field}" matching RFC 3339 UTC millisecond format')
def step_record_has_field_rfc3339(context: Context, field: str) -> None:
    rec = getattr(context, "last_matching_audit_record", None)
    if not rec:
        raise AssertionError("No audit record remembered from a prior step")
    value = rec.get(field)
    pattern = r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$"
    if not isinstance(value, str) or not re.match(pattern, value):
        raise AssertionError(
            f"Field {field!r} does not match RFC 3339 millisecond format: {value!r}"
        )


@then('that record has integer field "{field}" >= 0')
def step_record_has_integer_field_non_neg(context: Context, field: str) -> None:
    rec = getattr(context, "last_matching_audit_record", None)
    if not rec:
        raise AssertionError("No audit record remembered from a prior step")
    value = rec.get(field)
    if not isinstance(value, int) or value < 0:
        raise AssertionError(f"Field {field!r} is not an integer >= 0: {value!r}")


@then('that record has field "{field}" matching "{pattern}"')
def step_record_has_field_matching(context: Context, field: str, pattern: str) -> None:
    rec = getattr(context, "last_matching_audit_record", None)
    if not rec:
        raise AssertionError("No audit record remembered from a prior step")
    value = str(rec.get(field, ""))
    if not re.search(pattern, value):
        raise AssertionError(f"Field {field!r} ({value!r}) does not match pattern {pattern!r}")


@then('that record has field "{field}" starting with "{prefix}"')
def step_record_has_field_starting_with(context: Context, field: str, prefix: str) -> None:
    rec = getattr(context, "last_matching_audit_record", None)
    if not rec:
        raise AssertionError("No audit record remembered from a prior step")
    value = str(rec.get(field, ""))
    if not value.startswith(prefix):
        raise AssertionError(f"Field {field!r} ({value!r}) does not start with {prefix!r}")




@then('file "{filename}" contains in order for tool "{tool}":')
def step_file_contains_in_order_for_tool(context: Context, filename: str, tool: str) -> None:
    from support.audit_reader import AuditReader

    reader = AuditReader(context.audit_dir)
    records = [rec.record for rec in reader.read_file(filename) if rec.record.get("tool") == tool]
    expected_rows = list(context.table)
    if len(records) < len(expected_rows):
        raise AssertionError(
            f"Expected at least {len(expected_rows)} records for tool {tool!r} in {filename}, found {len(records)}: {records!r}"
        )
    for idx, row in enumerate(expected_rows):
        rec = records[idx]
        for heading in row.headings:
            expected_val = row[heading]
            actual_val = str(rec.get(heading, ""))
            if actual_val != expected_val:
                raise AssertionError(
                    f"Row {idx} field {heading!r}: expected {expected_val!r}, got {actual_val!r} in {rec!r}"
                )
    context.last_matched_tool_records = records[: len(expected_rows)]


@then('both "{tool}" records carry the same non-empty UUIDv4 "request_id"')
def step_both_records_carry_same_uuidv4_req_id(context: Context, tool: str) -> None:
    uuid_regex = r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
    records = getattr(context, "last_matched_tool_records", None)
    if not records or len(records) < 2:
        from support.audit_reader import AuditReader

        reader = AuditReader(context.audit_dir)
        records = [rec.record for rec in reader.records_today() if rec.record.get("tool") == tool]
    if len(records) < 2:
        raise AssertionError(f"Need at least 2 records for tool {tool!r}, got {len(records)}")
    req_ids = [r.get("request_id") for r in records[:2]]
    if not req_ids[0] or req_ids[0] != req_ids[1]:
        raise AssertionError(f"Request IDs do not match or are empty: {req_ids!r}")
    if not re.match(uuid_regex, str(req_ids[0]), re.IGNORECASE):
        raise AssertionError(f"Request ID is not a valid UUIDv4: {req_ids[0]!r}")


@then('file "{filename}" contains {count:d} records with tool "saga_transition" in exact step order:')
def step_file_contains_saga_transition_step_order(
    context: Context, filename: str, count: int
) -> None:
    from support.audit_reader import AuditReader

    reader = AuditReader(context.audit_dir)
    records = [rec.record for rec in reader.read_file(filename) if rec.record.get("tool") == "saga_transition"]
    expected_steps = [row["step"] for row in context.table]
    if len(records) != count:
        raise AssertionError(f"Expected {count} saga_transition records in {filename}, found {len(records)}: {records!r}")
    actual_steps = [r.get("step") for r in records]
    if actual_steps != expected_steps:
        raise AssertionError(f"Expected saga steps {expected_steps!r}, got {actual_steps!r}")
    context.last_saga_records = records


@then('all {count:d} "saga_transition" records carry the exact tx_id returned by move')
def step_saga_records_carry_tx_id_returned_by_move(context: Context, count: int) -> None:
    records = getattr(context, "last_saga_records", None) or []
    if len(records) != count:
        raise AssertionError(f"Expected {count} saga records, got {len(records)}")
    last_resp = getattr(context, "last_response", None) or {}
    expected_tx_id = last_resp.get("tx_id")
    if not expected_tx_id:
        raise AssertionError(f"Last move response did not contain tx_id: {last_resp!r}")
    for r in records:
        if r.get("tx_id") != expected_tx_id:
            raise AssertionError(f"Record tx_id {r.get('tx_id')!r} != expected {expected_tx_id!r}")


















@then("the audit log contains an entry with:")
def step_audit_log_contains_entry(context: Context) -> None:
    """Verify the audit JSONL file has a record matching every cell in the table.

    Cell values ending in `*` are treated as glob prefixes — used by
    `caller_addr` like `stdio:pid=*` where the actual pid is opaque
    to the test."""
    import fnmatch as _fnmatch

    from support.audit_reader import AuditReader

    expected = {row["field"]: row["value"] for row in context.table}

    def _matches(rec: dict, expected: dict) -> bool:
        for k, v in expected.items():
            actual = rec.get(k)
            if isinstance(v, str) and ("*" in v or "?" in v):
                if not isinstance(actual, str) or not _fnmatch.fnmatchcase(actual, v):
                    return False
            elif actual != v:
                return False
        return True

    reader = AuditReader(context.audit_dir)
    matches = [
        rec for rec in reader.records_today() if _matches(rec.record, expected)
    ]
    if not matches:
        present = [rec.record for rec in reader.records_today()]
        raise AssertionError(
            f"Audit log has no record matching {expected!r}. "
            f"Records present: {present!r}"
        )
    context.last_matching_audit_record = matches[0].record














def _assert_field_equals_table(context: Context, field: str) -> None:
    """Single-column table form of the list-equality assertion, for
    values that are too punctuated for the inline JSON form."""
    response = _last_response(context)
    if field not in response:
        raise AssertionError(
            f"Response has no field {field!r}. Available fields: "
            f"{sorted(response.keys())}"
        )
    actual = response[field]
    if not isinstance(actual, list):
        raise AssertionError(
            f"Field {field!r} is not a list; 'contains exactly' requires a list."
        )
    column = context.table.headings[0]
    expected = [row[column] for row in context.table]
    if sorted(actual) != sorted(expected):
        raise AssertionError(
            f"Field {field!r}: expected exactly {expected!r}, got {actual!r}"
        )


@then('the response field {field} contains {expected}')
def step_response_field_contains(
    context: Context, field: str, expected: str
) -> None:
    # `... contains exactly:` with a one-column table is the table form
    # of the list-equality assertion. It cannot carry its own step
    # registration: this pattern already matches that sentence.
    if expected == "exactly:" and context.table is not None:
        _assert_field_equals_table(context, field)
        return
    response = _last_response(context)
    found, actual = _resolve_dotted_field(response, field)
    if not found:
        raise AssertionError(
            f"Response has no field {field!r}. Available fields: "
            f"{sorted(response.keys())}. Full response: {response}"
        )
    expected_value = _parse_expected(expected)
    if isinstance(actual, list):
        # Apply UID-hint translation for the `uids` field.
        to_check = expected_value
        if field == "uids" and isinstance(expected_value, int):
            resolved = _resolve_uid_hints(context, [expected_value])
            to_check = resolved[0]
        searchable = _folder_paths(actual) if field == "folders" else actual
        if to_check not in searchable:
            raise AssertionError(
                f"Field {field!r}: {to_check!r} is not in {searchable!r}"
            )
        return
    if isinstance(actual, str) and isinstance(expected_value, str):
        if expected_value not in actual:
            raise AssertionError(
                f"Field {field!r}: substring {expected_value!r} is not "
                f"in {actual!r}"
            )
        return
    raise AssertionError(
        f"Field {field!r} has type {type(actual).__name__} which cannot "
        "be checked with `contains`."
    )
































@then("the handshake succeeds")
def step_handshake_succeeds(context: Context) -> None:
    if not getattr(context, "last_handshake_succeeded", False):
        err = getattr(context, "last_handshake_error", None)
        raise AssertionError(
            f"Handshake unexpectedly failed: {err!r}"
        )


@then('the handshake fails with error "{expected}"')
def step_handshake_fails(context: Context, expected: str) -> None:
    if getattr(context, "last_handshake_succeeded", True):
        raise AssertionError("Handshake unexpectedly succeeded")
    err = getattr(context, "last_handshake_error", None)
    if err != expected:
        raise AssertionError(
            f"Handshake error message: expected {expected!r}, got {err!r}"
        )






from behave import use_step_matcher as _use_step_matcher_assert


@then('file "{filename}" ends with a record of tool "{tool}" carrying field {field}')
def step_file_ends_with_record(
    context: Context, filename: str, tool: str, field: str
) -> None:
    import json as _json

    path = context.audit_dir / filename
    if not path.exists():
        raise AssertionError(f"File {filename!r} does not exist")
    lines = path.read_bytes().splitlines()
    if not lines:
        raise AssertionError(f"File {filename!r} is empty")
    last = _json.loads(lines[-1])
    if last.get("tool") != tool:
        raise AssertionError(
            f"Last record tool={last.get('tool')!r}, expected {tool!r}"
        )
    if field not in last:
        raise AssertionError(
            f"Last record missing field {field!r}: {last!r}"
        )
    context.last_eof_day_final_hash = last.get(field)


@then('file "{filename}" begins with seq {seq:d} whose prev_hash equals that final_hash')
@then('file "{filename}" begins with a record whose prev_hash equals that final_hash')
def step_file_begins_with_prev_hash(
    context: Context, filename: str, seq: int = 0
) -> None:
    import json as _json

    path = context.audit_dir / filename
    if not path.exists():
        raise AssertionError(f"File {filename!r} does not exist")
    lines = path.read_bytes().splitlines()
    if not lines:
        raise AssertionError(f"File {filename!r} is empty")
    first = _json.loads(lines[0])
    if first.get("seq") != seq:
        raise AssertionError(
            f"First record seq={first.get('seq')!r}, expected {seq}"
        )
    expected = context.last_eof_day_final_hash
    if first.get("prev_hash") != expected:
        raise AssertionError(
            f"First record prev_hash={first.get('prev_hash')!r}, "
            f"expected {expected!r}"
        )




_use_step_matcher_assert("re")




@then(
    r'the audit log contains a record with tool "(?P<tool>[^"]+)", '
    r'result "(?P<result>[^"]+)", reason "(?P<reason>[^"]+)"'
)
def step_audit_record_tool_result_reason(
    context: Context, tool: str, result: str, reason: str
) -> None:
    from support.audit_reader import AuditReader

    reader = AuditReader(context.audit_dir)
    for rec in reader.records_today():
        r = rec.record
        if (
            r.get("tool") == tool
            and r.get("result") == result
            and r.get("reason") == reason
        ):
            context.last_matching_audit_record = r
            return
    present = [
        (r.record.get("tool"), r.record.get("result"), r.record.get("reason"))
        for r in reader.records_today()
    ]
    raise AssertionError(
        f"No audit record with tool={tool!r} result={result!r} reason={reason!r}. "
        f"Present: {present!r}"
    )




_use_step_matcher_assert("parse")






@then('the IMAP command log for "{account_id}" contains in order:')
def step_imap_command_log_in_order(context: Context, account_id: str) -> None:
    """Read the per-account proxy log and assert that each `command`
    cell appears, in the given order, somewhere in the recorded
    client→upstream traffic. Substring match per cell — adjacent
    matches need not be contiguous in the log because aioimaplib
    emits its own bookkeeping (NOOP, LOGOUT, etc.) between the
    business commands.

    mock-gmail accounts have no proxy in front of them; their log is
    the mock's own command log (`connection_steps.imap_command_lines`)."""
    from support.imap_fixture import resolve_account

    log_path = (getattr(context, "imap_proxy_log_paths", None) or {}).get(account_id)
    if log_path is not None and log_path.exists():
        log = log_path.read_text(encoding="utf-8", errors="replace")
    elif resolve_account(account_id)[0] == "mock-gmail":
        from features.steps.connection_steps import imap_command_lines

        log = "\n".join(imap_command_lines(context, account_id))
    else:
        raise AssertionError(
            f"No IMAP proxy log captured for account {account_id!r}; "
            "did a prior step start the MITM proxy?"
        )
    expected_lines = [row["command"] for row in context.table]
    cursor = 0
    upper_log = log.upper()
    for needle in expected_lines:
        # Each cell is a sequence of whitespace-separated tokens that
        # must appear in order on the wire — but the wire form can
        # interleave IMAP framing (`UID `, tag prefix, `+FLAGS (...)`)
        # between them. Match token-by-token so feature cells stay in
        # the user-meaningful "STORE \Deleted" form rather than the
        # wire-literal "UID STORE 1 +FLAGS (\Deleted)".
        tokens = needle.upper().split()
        sub_cursor = cursor
        for token in tokens:
            idx = upper_log.find(token, sub_cursor)
            if idx < 0:
                raise AssertionError(
                    f"IMAP command log missing/out-of-order: {needle!r} "
                    f"(token {token!r} not found) after position "
                    f"{cursor}.\nFull log:\n{log}"
                )
            sub_cursor = idx + len(token)
        cursor = sub_cursor


@then('the IMAP command log for "{account_id}" contains no SEARCH command')
def step_imap_command_log_has_no_search(context: Context, account_id: str) -> None:
    """Absence witness on the proxy in front of a Dovecot-backed account.

    The server may legitimately never connect, so an empty or missing
    log is acceptable only while the proxy that would have recorded the
    SEARCH is still running and wired into the account; anything else
    is a harness error, not a pass.
    """
    log_path = (getattr(context, "imap_proxy_log_paths", None) or {}).get(account_id)
    proxy_proc = (getattr(context, "imap_proxy_procs", None) or {}).get(account_id)
    if log_path is None or proxy_proc is None or proxy_proc.poll() is not None:
        raise AssertionError(
            f"No running IMAP proxy captures the traffic of {account_id!r}; "
            "the absence of SEARCH cannot be witnessed."
        )
    builder = getattr(context, "policy_builder", None)
    proxy_port = (getattr(context, "imap_proxy_ports", None) or {}).get(account_id)
    wired = builder is not None and any(
        account.id == account_id and account.port == proxy_port
        for account in builder.accounts
    )
    if not wired:
        raise AssertionError(
            f"Account {account_id!r} is not configured to reach IMAP through "
            f"the proxy on port {proxy_port!r}."
        )
    log = log_path.read_text(encoding="utf-8", errors="replace") if log_path.exists() else ""
    searches = []
    for entry in log.splitlines():
        # `<timestamp>\t<tag> [UID] <command> ...`
        tokens = entry.partition("\t")[2].split()
        if len(tokens) > 1 and tokens[1].upper() == "UID":
            tokens = tokens[1:]
        if len(tokens) > 1 and tokens[1].upper() == "SEARCH":
            searches.append(entry)
    if searches:
        raise AssertionError(
            f"Unexpected SEARCH in the IMAP command log of {account_id!r}:\n"
            + "\n".join(searches)
        )














def _assert_5tuple_search(
    context: Context, folder: str, sender: str, sent_date: str,
    subject: str | None, expected: int,
) -> None:
    """Independent IMAP SEARCH against the named (account:folder)
    using FROM + SENTON + (optional) SUBJECT. The harness opens its
    own connection so the assertion is a true second channel."""
    from datetime import datetime as _dt

    from support.imap_fixture import resolve_account

    if ":" in folder:
        account_id, _, folder = folder.partition(":")
    else:
        account_id = _account_for_folder(context, folder)
    instance, user = resolve_account(account_id)
    conn = context.imap.connect(instance, user)
    status, _ = conn.select(folder)
    if status != "OK":
        raise AssertionError(f"SELECT {folder!r} failed")
    try:
        d = _dt.fromisoformat(sent_date)
        senton = d.strftime("%d-%b-%Y")
    except ValueError:
        senton = sent_date
    # Always quote scalar values to keep imaplib's tokenizer from
    # splitting on whitespace inside Subject/From etc.
    def _q(s: str) -> str:
        return '"' + s.replace('"', '\\"') + '"'

    args = ["FROM", _q(sender), "SENTON", senton]
    if subject:
        args += ["SUBJECT", _q(subject)]
    status, data = conn.uid("SEARCH", None, *args)
    if status != "OK":
        raise AssertionError(f"SEARCH failed: {data!r}")
    raw = data[0] or b""
    uids = [int(x) for x in raw.split()] if raw else []
    context.last_5tuple_uids = uids
    if len(uids) != expected:
        raise AssertionError(
            f"5-tuple SEARCH on {folder!r}: expected {expected} result(s), "
            f"got {len(uids)}: {uids!r}"
        )




@then(
    'the audit log contains an entry with tool "{tool}", step "{step}", '
    'reason "{reason}"'
)
def step_audit_entry_tool_step_reason(
    context: Context, tool: str, step: str, reason: str
) -> None:
    from support.audit_reader import AuditReader

    reader = AuditReader(context.audit_dir)
    for rec in reader.records_today():
        r = rec.record
        if r.get("tool") == tool and r.get("step") == step and r.get("reason") == reason:
            context.last_matching_audit_record = r
            return
    present = [
        (r.record.get("tool"), r.record.get("step"), r.record.get("reason"))
        for r in reader.records_today()
    ]
    raise AssertionError(
        f"No audit record with tool={tool!r} step={step!r} reason={reason!r}. "
        f"Present: {present!r}"
    )


@then("the WAL contains {n:d} transaction entries")
def step_wal_contains_n_transactions(context: Context, n: int) -> None:
    from support.wal_reader import WALReader
    reader = WALReader(context.wal_path)
    txs = reader.all_transactions()
    if len(txs) != n:
        raise AssertionError(f"Expected {n} WAL transaction entries, found {len(txs)}: {txs!r}")


@then('the audit log contains an entry with tool "{tool}" and step "{step}"')
def step_audit_log_contains_tool_step(
    context: Context, tool: str, step: str
) -> None:
    from support.audit_reader import AuditReader

    reader = AuditReader(context.audit_dir)
    for rec in reader.records_today():
        if rec.record.get("tool") == tool and rec.record.get("step") == step:
            context.last_matching_audit_record = rec.record
            return
    present = [
        (r.record.get("tool"), r.record.get("step"))
        for r in reader.records_today()
    ]
    raise AssertionError(
        f"No audit record with tool={tool!r} step={step!r}. Present: {present!r}"
    )














# ------------------------------------------------------------------
# list_folders message-count assertions
# ------------------------------------------------------------------




# ------------------------------------------------------------------
# Generic list-length assertion
# ------------------------------------------------------------------


@then("the response field {field} has length {length:d}")
def step_response_field_has_length(
    context: Context, field: str, length: int
) -> None:
    response = _last_response(context)
    if field not in response:
        raise AssertionError(
            f"Response has no field {field!r}. "
            f"Available fields: {sorted(response.keys())}"
        )
    actual = response[field]
    if not isinstance(actual, list):
        raise AssertionError(
            f"Field {field!r} is not a list; 'has length' requires a list."
        )
    if len(actual) != length:
        raise AssertionError(
            f"Field {field!r}: expected length {length}, got {len(actual)}"
        )


# ------------------------------------------------------------------
# Audit file permission steps (Given + Then)
# ------------------------------------------------------------------




@then('the audit file "{filename}" has mode {mode}')
def step_audit_file_has_mode(context: Context, filename: str, mode: str) -> None:
    import os as _os
    import stat as _stat

    path = context.audit_dir / filename
    if not path.exists():
        raise AssertionError(f"Audit file {filename!r} does not exist")
    expected = int(mode, 8)
    actual = _stat.S_IMODE(_os.stat(path).st_mode)
    if actual != expected:
        raise AssertionError(
            f"{filename} mode is {oct(actual)}, expected {oct(expected)}"
        )




# ------------------------------------------------------------------
# list_messages assertions
# ------------------------------------------------------------------


@then("the response contains {count:d} messages")
def step_response_contains_n_messages(context: Context, count: int) -> None:
    response = _last_response(context)
    messages = response.get("messages")
    if messages is None:
        raise AssertionError(
            f"Response has no 'messages' field. "
            f"Available: {sorted(response.keys())}"
        )
    if len(messages) != count:
        raise AssertionError(
            f"Expected {count} messages, got {len(messages)}"
        )


@then('message {idx:d} has field "{field}" equal to "{expected}"')
def step_message_field_equals(
    context: Context, idx: int, field: str, expected: str
) -> None:
    response = _last_response(context)
    messages = response.get("messages", [])
    if idx >= len(messages):
        raise AssertionError(
            f"Message index {idx} out of range "
            f"(only {len(messages)} messages)"
        )
    actual = messages[idx].get(field)
    if actual != expected:
        raise AssertionError(
            f"message[{idx}].{field}: expected {expected!r}, "
            f"got {actual!r}"
        )


@then('message {idx:d} has field "{field}" starting with "{prefix}"')
def step_message_field_starting_with(
    context: Context, idx: int, field: str, prefix: str
) -> None:
    response = _last_response(context)
    messages = response.get("messages", [])
    if idx >= len(messages):
        raise AssertionError(
            f"Message index {idx} out of range "
            f"(only {len(messages)} messages)"
        )
    actual = str(messages[idx].get(field) or "")
    if not actual.startswith(prefix):
        raise AssertionError(
            f"message[{idx}].{field}: expected to start with {prefix!r}, "
            f"got {actual!r}"
        )


@then('message {idx:d} has field "{field}" equal to {expected}')
def step_message_field_equals_unquoted(
    context: Context, idx: int, field: str, expected: str
) -> None:
    response = _last_response(context)
    messages = response.get("messages", [])
    if idx >= len(messages):
        raise AssertionError(
            f"Message index {idx} out of range (only {len(messages)} messages)"
        )
    actual = messages[idx].get(field)
    if expected == "null":
        expected_val = None
    elif expected.isdigit():
        expected_val = int(expected)
        if field == "uid":
            acc = response.get("account") or getattr(context, "last_call_account", None)
            fld = response.get("folder") or getattr(context, "last_call_folder", None)
            if acc and fld:
                from support.imap_fixture import resolve_uid

                expected_val = resolve_uid(context, acc, fld, expected_val)
    elif expected in ("true", "false"):
        expected_val = expected == "true"
    elif expected.startswith('the remembered "') and expected.endswith('"'):
        key = expected[len('the remembered "') : -1]
        expected_val = (getattr(context, "remembered_values", {}) or {}).get(key)
    else:
        expected_val = expected.strip('"\'')
    if actual != expected_val:
        raise AssertionError(
            f"message[{idx}].{field}: expected {expected_val!r}, got {actual!r}"
        )


@then('message {idx:d} has integer field "{field}" > {threshold:d}')
def step_message_field_gt(
    context: Context, idx: int, field: str, threshold: int
) -> None:
    response = _last_response(context)
    messages = response.get("messages", [])
    if idx >= len(messages):
        raise AssertionError(
            f"Message index {idx} out of range (only {len(messages)} messages)"
        )
    actual = messages[idx].get(field)
    if not isinstance(actual, (int, float)) or actual <= threshold:
        raise AssertionError(
            f"message[{idx}].{field}: expected > {threshold}, got {actual!r}"
        )


@then('the file at saved_path exists with mode {mode} and exact content byte "{b}" repeated {n:d} times')
def step_file_at_saved_path_exists(
    context: Context, mode: str, b: str, n: int
) -> None:
    import stat
    from pathlib import Path

    response = _last_response(context)
    saved_path = response.get("saved_path")
    if not saved_path:
        raise AssertionError(f"Response has no saved_path: {response}")
    p = Path(saved_path)
    if not p.is_file():
        raise AssertionError(f"File at saved_path does not exist: {saved_path}")

    file_stat = p.stat()
    file_mode = oct(stat.S_IMODE(file_stat.st_mode))
    expected_oct = oct(int(mode, 8))
    if file_mode != expected_oct:
        raise AssertionError(
            f"File mode for {saved_path}: expected {expected_oct}, got {file_mode}"
        )

    expected_bytes = b.encode("utf-8") * n
    actual_bytes = p.read_bytes()
    if actual_bytes != expected_bytes:
        raise AssertionError(
            f"File content length {len(actual_bytes)} != expected {len(expected_bytes)}"
        )


@then('the file at saved_path exists with mode {mode} and exact ASCII content "{expected_ascii}"')
def step_file_at_saved_path_exists_ascii(
    context: Context, mode: str, expected_ascii: str
) -> None:
    import stat
    from pathlib import Path

    response = _last_response(context)
    saved_path = response.get("saved_path")
    if not saved_path:
        raise AssertionError(f"Response has no saved_path: {response}")
    p = Path(saved_path)
    if not p.is_file():
        raise AssertionError(f"File at saved_path does not exist: {saved_path}")

    file_stat = p.stat()
    file_mode = oct(stat.S_IMODE(file_stat.st_mode))
    expected_oct = oct(int(mode, 8))
    if file_mode != expected_oct:
        raise AssertionError(
            f"File mode for {saved_path}: expected {expected_oct}, got {file_mode}"
        )

    expected_bytes = expected_ascii.encode("utf-8").decode("unicode_escape").encode("latin1")
    actual_bytes = p.read_bytes()
    if actual_bytes != expected_bytes:
        raise AssertionError(
            f"File content: expected {expected_bytes!r}, got {actual_bytes!r}"
        )


@then("the leaf filename of saved_to has byte length at most {max_len:d}")
def step_leaf_filename_saved_to_byte_length(context: Context, max_len: int) -> None:
    response = _last_response(context)
    saved_to = response.get("saved_to", "")
    leaf = Path(saved_to).name
    byte_len = len(leaf.encode("utf-8"))
    if byte_len > max_len:
        raise AssertionError(
            f"Leaf filename {leaf!r} byte length {byte_len} exceeds max {max_len}"
        )


@then('the directory "{dir_path}" contains {count:d} files')
def step_directory_contains_n_files(context: Context, dir_path: str, count: int) -> None:
    p = Path(dir_path.replace("$TMPDIR", str(context.scratch_dir)))
    if not p.is_dir():
        raise AssertionError(f"Directory {p} does not exist")
    files = [f for f in p.iterdir() if f.is_file()]
    if len(files) != count:
        raise AssertionError(f"Expected {count} files in {p}, found {len(files)}: {files}")


# ---------------------------------------- blob resource assertions






# ---------------------------------------- attachment discovery assertions


@then("the response field attachments has {count:d} entries")
def step_response_attachments_count(context: Context, count: int) -> None:
    response = _last_response(context)
    attachments = response.get("attachments")
    if attachments is None:
        raise AssertionError(
            f"Response has no 'attachments' field. "
            f"Keys: {sorted(response.keys())!r}"
        )
    if len(attachments) != count:
        raise AssertionError(
            f"Expected {count} attachments, got {len(attachments)}: "
            f"{attachments!r}"
        )


@then('attachment {idx:d} has field "{field}" equal to "{expected}"')
def step_attachment_field_str(
    context: Context, idx: int, field: str, expected: str
) -> None:
    response = _last_response(context)
    attachments = response.get("attachments", [])
    if idx >= len(attachments):
        raise AssertionError(
            f"Attachment index {idx} out of range "
            f"(only {len(attachments)} attachments)"
        )
    actual = attachments[idx].get(field)
    if str(actual) != expected:
        raise AssertionError(
            f"attachment[{idx}].{field}: expected {expected!r}, "
            f"got {actual!r}"
        )


@then("attachment {idx:d} has field \"{field}\" equal to {expected:d}")
def step_attachment_field_int(
    context: Context, idx: int, field: str, expected: int
) -> None:
    response = _last_response(context)
    attachments = response.get("attachments", [])
    if idx >= len(attachments):
        raise AssertionError(
            f"Attachment index {idx} out of range "
            f"(only {len(attachments)} attachments)"
        )
    actual = attachments[idx].get(field)
    if actual != expected:
        raise AssertionError(
            f"attachment[{idx}].{field}: expected {expected!r}, "
            f"got {actual!r}"
        )


# ----------------------------------------------------------------------
# Reply-builder Then-steps (create_reply_draft.feature).
# These read the resulting draft directly via IMAP — the second channel
# required by §13.2 Prüfung 1, independent of the MCP server's response.
# ----------------------------------------------------------------------


def _decoded_header(msg, name: str) -> str:
    """Return the RFC 2047-decoded value of header `name`, or "" if absent."""
    from email.header import decode_header, make_header

    raw = msg[name]
    if raw is None:
        return ""
    return str(make_header(decode_header(raw)))


def _resolve_message_in_folder_by_subject(
    context: Context, folder: str, subject: str
):
    """Return the parsed EmailMessage in `folder` matching `subject`."""
    from support.imap_fixture import resolve_account

    account_id = _account_for_folder(context, folder)
    instance, user = resolve_account(account_id)
    uid = context.imap.find_unique_uid_by_decoded_subject(
        instance, user, folder, subject
    )
    if uid is None:
        raise AssertionError(
            f"No message in folder {folder!r} has decoded subject {subject!r}"
        )
    return context.imap.fetch_message(instance, user, folder, uid)


@then(
    'the message in folder "{folder}" with subject "{subject}" '
    'has {header} header equal to "{value}"'
)
def step_message_in_folder_header_equal(
    context: Context, folder: str, subject: str, header: str, value: str
) -> None:
    msg = _resolve_message_in_folder_by_subject(context, folder, subject)
    actual = _decoded_header(msg, header)
    if actual != value:
        raise AssertionError(
            f"{header} header on message {subject!r} in {folder!r}: "
            f"expected {value!r}, got {actual!r}"
        )






@then('the response text_body equals the following document:')
def step_response_text_body_equals_document(context: Context) -> None:
    """Match a multi-line text_body against a Gherkin DocString.

    Phrased to avoid clashing with the generic
    `the response field {field} equals {expected}` step (which expects
    a JSON literal on a single line, not a DocString)."""
    _assert_text_body_equals_exactly(context)


def _assert_text_body_equals_exactly(context: Context) -> None:
    expected = context.text or ""
    response = _last_response(context)
    if "text_body" not in response:
        raise AssertionError(
            f"Response has no field 'text_body'. Available fields: "
            f"{sorted(response.keys())}"
        )
    actual = response["text_body"]
    # Normalize CRLF → LF (RFC822 uses CRLF, Gherkin DocStrings use LF)
    # and tolerate trailing newline differences.
    actual_n = actual.replace("\r\n", "\n").rstrip("\n")
    expected_n = expected.rstrip("\n")
    if actual_n != expected_n:
        raise AssertionError(
            "text_body mismatch.\n"
            f"--- expected ---\n{expected_n!r}\n"
            f"--- actual   ---\n{actual_n!r}"
        )


# NOTE: the matching step
#   `the response field {field} contains exactly one entry with:`
# is already defined further up (~line 616) as a generic one-row tabular
# match. The reply-builder feature reuses it via field=messages.


# --------------------------------------------------------------------- 2.0 surface


def _server_info_metadata(context: Context) -> dict:
    """Return the serverInfo.metadata block from the live MCP client."""
    client = context.mcp
    if client is None:
        raise AssertionError("MCP client not initialised; start a tool call first")
    info = getattr(client, "server_info", None) or {}
    return info.get("metadata") or {}




@then('the server info version equals "{version}"')
def step_server_info_version_equals(context: Context, version: str) -> None:
    client = context.mcp
    if client is None:
        raise AssertionError("MCP client not initialised")
    info = getattr(client, "server_info", None) or {}
    actual = info.get("version")
    if actual != version:
        raise AssertionError(f"serverInfo.version: expected {version!r}, got {actual!r}")


@then('the server info metadata contains "{key}" equal to "{expected}"')
def step_server_info_metadata_contains(context: Context, key: str, expected: str) -> None:
    metadata = _server_info_metadata(context)
    actual = metadata.get(key)
    if actual != expected:
        raise AssertionError(f"serverInfo metadata[{key!r}]: expected {expected!r}, got {actual!r}")




@then("the response field {field} matches the serverInfo {meta_key}")
def step_response_field_matches_server_info(
    context: Context, field: str, meta_key: str
) -> None:
    metadata = _server_info_metadata(context)
    expected = metadata.get(meta_key)
    response = _last_response(context)
    actual = response.get(field)
    if expected is None:
        raise AssertionError(f"serverInfo.metadata has no key {meta_key!r}")
    if actual != expected:
        raise AssertionError(
            f"Field {field!r}: response {actual!r} vs serverInfo.{meta_key} {expected!r}"
        )






@then("the response field {field} is a non-empty array")
def step_response_field_is_non_empty_array(context: Context, field: str) -> None:
    found, value = _resolve_dotted_field(_last_response(context), field)
    if not found:
        raise AssertionError(f"Response has no field {field!r}")
    if not isinstance(value, list) or len(value) == 0:
        raise AssertionError(f"Field {field!r}: expected non-empty array, got {value!r}")


@then('breaking_changes_since contains an entry with version "{version}" mentioning "{mention}"')
def step_breaking_changes_contains_version_mentioning(
    context: Context, version: str, mention: str
) -> None:
    response = _last_response(context)
    bcs = response.get("breaking_changes_since") or []
    matches = [
        entry
        for entry in bcs
        if isinstance(entry, dict)
        and entry.get("version") == version
        and mention in str(entry)
    ]
    if not matches:
        raise AssertionError(
            f"Expected breaking_changes_since entry with version {version!r} mentioning {mention!r}, found: {bcs}"
        )


@then("the response field {field} is a string of at most {max_len:d} characters")
def step_response_field_string_bounded(
    context: Context, field: str, max_len: int
) -> None:
    found, value = _resolve_dotted_field(_last_response(context), field)
    if not found:
        raise AssertionError(f"Response has no field {field!r}")
    if not isinstance(value, str):
        raise AssertionError(f"Field {field!r}: expected str, got {type(value).__name__}")
    if len(value) > max_len:
        raise AssertionError(
            f"Field {field!r}: length {len(value)} exceeds max {max_len}"
        )


@then('the response field {field} does NOT contain the literal string "{needle}"')
def step_response_field_no_literal(context: Context, field: str, needle: str) -> None:
    found, value = _resolve_dotted_field(_last_response(context), field)
    if not found:
        raise AssertionError(f"Response has no field {field!r}")
    if isinstance(value, str) and needle in value:
        raise AssertionError(
            f"Field {field!r} unexpectedly contains literal {needle!r}: {value!r}"
        )








@then('the response error message contains "{needle}"')
def step_response_error_message_contains(context: Context, needle: str) -> None:
    rpc_error = getattr(context, "last_rpc_error", None)
    if rpc_error is None:
        raise AssertionError(
            "No JSON-RPC error captured. Last response was: "
            f"{getattr(context, 'last_response', None)!r}"
        )
    message = rpc_error.get("message") or ""
    if needle not in message:
        raise AssertionError(
            f"JSON-RPC error message {message!r} does not contain {needle!r}"
        )


@then('the response error message does NOT contain "{needle}"')
def step_response_error_message_does_not_contain(context: Context, needle: str) -> None:
    rpc_error = getattr(context, "last_rpc_error", None)
    if rpc_error is None:
        raise AssertionError(
            "No JSON-RPC error captured. Last response was: "
            f"{getattr(context, 'last_response', None)!r}"
        )
    message = rpc_error.get("message") or ""
    if needle in message:
        raise AssertionError(
            f"JSON-RPC error message {message!r} unexpectedly contains {needle!r}"
        )


@then('both responses report applied_scope equal to "{expected}"')
def step_both_responses_applied_scope(context: Context, expected: str) -> None:
    history = getattr(context, "response_history", None) or []
    if len(history) < 2:
        raise AssertionError(
            f"Need at least two recorded responses, have {len(history)}"
        )
    for i, resp in enumerate(history[-2:], start=1):
        actual = resp.get("applied_scope")
        if actual != expected:
            raise AssertionError(
                f"Response #{i}: applied_scope expected {expected!r}, got {actual!r}"
            )














# --------------------------------------------------------------------- ADR 0028


def _sink_dir(context: Context) -> Path:
    """Return the scenario's sink dir or fail with a helpful message."""
    sink = getattr(context, "attachment_sink_dir", None)
    if sink is None:
        raise AssertionError(
            "No attachment sink directory configured for this scenario. "
            "Use a `Given the server attachment sink directory ...` step."
        )
    from pathlib import Path as _Path

    return _Path(sink)








@then("the response content has no resource block")
def step_response_no_resource_block(context: Context) -> None:
    """The MCP CallToolResult must not carry an EmbeddedResource block
    after ADR 0028; _emit emits text-only content."""
    blocks = getattr(context, "last_content_blocks", None)
    if blocks is None:
        raw = getattr(context, "last_raw_payload", None)
        if raw is not None and isinstance(raw, dict):
            blocks = raw.get("content")
    if blocks is None:
        raise AssertionError("No MCP response content blocks recorded to check for resource block")
    if not isinstance(blocks, list) or len(blocks) == 0:
        raise AssertionError("MCP response content blocks is empty")
    resource_blocks = [
        b
        for b in blocks
        if isinstance(b, dict) and (b.get("type") in ("resource", "embedded_resource") or "resource" in b)
    ]
    if resource_blocks:
        raise AssertionError(
            f"Response unexpectedly carries resource block(s): {resource_blocks!r}"
        )








@then("no file was written outside the sink directory")
def step_no_file_outside_sink(context: Context) -> None:
    """The sanitization contract guarantees no `../` traversal escapes
    the sink. We assert the negative by spot-checking the parent and a
    couple of known-bad targets the scenarios reference."""
    sink = _sink_dir(context)
    parent = sink.parent
    # Anything new in the parent that is not the sink itself fails.
    rogue = [
        p for p in parent.iterdir()
        if p.resolve() != sink.resolve() and p.is_file()
        and p.name.endswith(".pdf")
    ]
    if rogue:
        raise AssertionError(
            f"Files appeared outside the sink in {parent}: {rogue!r}"
        )
    # Specifically check the path the malicious filename in the
    # scenario would have hit: ../../etc/passwd.pdf relative to the
    # sink would land at <sink-parent>/../etc/passwd.pdf.
    for evil in ("etc/passwd.pdf", "passwd.pdf"):
        candidate = (parent / evil).resolve()
        if candidate.exists() and candidate.is_file():
            raise AssertionError(f"Path-traversal artefact appeared: {candidate}")






# ---- tool-description introspection -----------------------------------------


def _tool_description(context: Context, tool_name: str) -> str:
    tools = getattr(context, "last_tools", None)
    if tools is None:
        raise AssertionError(
            "No tool list captured; precede with `calls the MCP list_tools method`."
        )
    for t in tools:
        name = t.get("name") if isinstance(t, dict) else getattr(t, "name", None)
        if name == tool_name:
            desc = t.get("description") if isinstance(t, dict) else getattr(t, "description", "")
            return desc or ""
    raise AssertionError(f"Tool {tool_name!r} not in list_tools output")


@then('the description of tool "{tool}" contains the literal string "{needle}"')
@then('the description of tool "{tool}" contains "{needle}"')
def step_tool_desc_contains(context: Context, tool: str, needle: str) -> None:
    desc = _tool_description(context, tool)
    if needle not in desc:
        raise AssertionError(
            f"Tool {tool!r} description does not contain {needle!r}:\n{desc}"
        )


@then('the description of tool "{tool}" does NOT contain the literal string "{needle}"')
@then('the description of tool "{tool}" does NOT contain "{needle}"')
def step_tool_desc_no_contain(context: Context, tool: str, needle: str) -> None:
    desc = _tool_description(context, tool)
    if needle in desc:
        raise AssertionError(
            f"Tool {tool!r} description unexpectedly contains {needle!r}:\n{desc}"
        )


@then('the description of tool "{tool}" does NOT contain the configured sink path')
def step_tool_desc_no_configured_sink_path(context: Context, tool: str) -> None:
    desc = _tool_description(context, tool)
    sink_dir = getattr(context, "attachment_sink_dir", None)
    if sink_dir is not None and str(sink_dir) in desc:
        raise AssertionError(
            f"Tool {tool!r} description unexpectedly contains configured sink path {str(sink_dir)!r}:\n{desc}"
        )


# ------------------------------------------------------------------
# Draft handle assertions (ADR 0030)
#
# The IMAP-side steps below read the mailbox through the fixture's own
# imaplib connection, independent of the MCP server's view of it (BDD
# Guidelines §13.2 Prüfung 1).
# ------------------------------------------------------------------


def _draft_header_value(message: Any, header: str) -> str | None:
    """Return one header value, RFC 2047-decoded, or None if absent.

    Distinct from `_decoded_header` above, which answers "" for an
    absent header: the draft assertions need to tell "header missing"
    apart from "header present but empty", so they cannot share it.
    """
    from email.header import decode_header, make_header

    raw = message.get(header)
    if raw is None:
        return None
    return str(make_header(decode_header(raw))).strip()


def _folder_messages(context: Context, folder: str) -> list[Any]:
    from support.imap_fixture import resolve_account

    account_id = _account_for_folder(context, folder)
    instance, user = resolve_account(account_id)
    messages = []
    for uid in context.imap.folder_uids(instance, user, folder):
        messages.append(context.imap.fetch_message(instance, user, folder, uid))
    return messages


@then(
    'the IMAP folder "{folder}" contains exactly {count:d} message '
    'with header "{header}" set'
)
@then(
    'the IMAP folder "{folder}" contains exactly {count:d} messages '
    'with header "{header}" set'
)
def step_imap_folder_header_set_count(
    context: Context, folder: str, count: int, header: str
) -> None:
    messages = _folder_messages(context, folder)
    carrying = [m for m in messages if (_draft_header_value(m, header) or "") != ""]
    if len(carrying) != count:
        raise AssertionError(
            f"Folder {folder!r} holds {len(carrying)} message(s) with header "
            f"{header!r} set; expected {count}. Folder holds "
            f"{len(messages)} message(s) in total."
        )


@then('the IMAP folder "{folder}" contains exactly one message with headers:')
def step_imap_folder_one_message_with_headers(context: Context, folder: str) -> None:
    expectations = [(row["header"], row["value"]) for row in context.table]
    messages = _folder_messages(context, folder)
    matches = [
        m
        for m in messages
        if all(_draft_header_value(m, header) == value for header, value in expectations)
    ]
    if len(matches) != 1:
        raise AssertionError(
            f"Folder {folder!r}: expected exactly one message with headers "
            f"{expectations!r}, found {len(matches)} among "
            f"{len(messages)} message(s)."
        )


@then('the IMAP message in folder "{folder}" with subject "{subject}" has flags:')
def step_imap_message_with_subject_has_flags(
    context: Context, folder: str, subject: str
) -> None:
    from support.imap_fixture import resolve_account

    account_id = _account_for_folder(context, folder)
    instance, user = resolve_account(account_id)
    uid = context.imap.find_uid_by_decoded_subject(instance, user, folder, subject)
    if uid is None:
        raise AssertionError(
            f"Folder {folder!r} holds no message with subject {subject!r}."
        )
    actual = context.imap.fetch_flags(instance, user, folder, uid)
    missing = [row["flag"] for row in context.table if row["flag"] not in actual]
    if missing:
        raise AssertionError(
            f"Message {subject!r} in {folder!r} carries flags {actual!r}; "
            f"missing {missing!r}"
        )


def _draft_message_by_subject(context: Context, folder: str, subject: str) -> Any:
    from support.imap_fixture import resolve_account

    account_id = _account_for_folder(context, folder)
    instance, user = resolve_account(account_id)
    uid = context.imap.find_uid_by_decoded_subject(instance, user, folder, subject)
    if uid is None:
        raise AssertionError(
            f"Folder {folder!r} holds no message with subject {subject!r}."
        )
    return context.imap.fetch_message(instance, user, folder, uid)




@then(
    'the header "{header}" of the IMAP message in folder "{folder}" '
    'with subject "{subject}" is remembered as "{label}"'
)
def step_remember_header_value(
    context: Context, header: str, folder: str, subject: str, label: str
) -> None:
    """Park a header value so a later revision can be compared to it."""
    message = _draft_message_by_subject(context, folder, subject)
    value = _draft_header_value(message, header)
    if value is None or value == "":
        raise AssertionError(
            f"Message {subject!r} in {folder!r} carries no {header!r} header, "
            f"so there is nothing to remember under {label!r}."
        )
    if not hasattr(context, "remembered_headers"):
        context.remembered_headers = {}
    context.remembered_headers[label] = value


@then(
    'the header "{header}" of the IMAP message in folder "{folder}" '
    'with subject "{subject}" differs from the remembered "{label}"'
)
def step_header_differs_from_remembered(
    context: Context, header: str, folder: str, subject: str, label: str
) -> None:
    remembered = getattr(context, "remembered_headers", {}).get(label)
    if remembered is None:
        raise AssertionError(
            f"No header value was remembered under {label!r} in this scenario."
        )
    message = _draft_message_by_subject(context, folder, subject)
    value = _draft_header_value(message, header)
    if value is None or value == "":
        raise AssertionError(
            f"Message {subject!r} in {folder!r} carries no {header!r} header at all."
        )
    if value == remembered:
        raise AssertionError(
            f"{header!r} on {subject!r} in {folder!r} is unchanged: "
            f"still {value!r} (remembered as {label!r})."
        )


@then(
    'the header "{header}" of the IMAP message in folder "{folder}" '
    'with subject "{subject}" is not "{value}"'
)
def step_header_is_not(
    context: Context, header: str, folder: str, subject: str, value: str
) -> None:
    message = _draft_message_by_subject(context, folder, subject)
    actual = _draft_header_value(message, header)
    if actual == value:
        raise AssertionError(
            f"{header!r} on {subject!r} in {folder!r} still carries the "
            f"value the caller supplied: {actual!r}"
        )






@then(
    'the header "{header}" of the IMAP message in folder "{folder}" '
    'with subject "{subject}" matches "{pattern}"'
)
def step_header_matches_pattern(
    context: Context, header: str, folder: str, subject: str, pattern: str
) -> None:
    import re
    message = _draft_message_by_subject(context, folder, subject)
    value = _draft_header_value(message, header)
    if value is None or value == "":
        raise AssertionError(
            f"Message {subject!r} in {folder!r} carries no {header!r} header at all."
        )
    if not re.search(pattern, value):
        raise AssertionError(
            f"Header {header!r} on {subject!r} in {folder!r} is {value!r}, which does not match {pattern!r}"
        )


@then('draft entry {idx:d} has field "{field}" equal to {expected}')
def step_draft_entry_field_equals(
    context: Context, idx: int, field: str, expected: str
) -> None:
    response = _last_response(context)
    drafts = response.get("drafts", [])
    if idx >= len(drafts):
        raise AssertionError(f"Draft entry index {idx} out of range (only {len(drafts)} drafts)")
    entry = drafts[idx]
    actual = entry.get(field)
    if expected == "null":
        expected_val = None
    elif expected == "true":
        expected_val = True
    elif expected == "false":
        expected_val = False
    elif expected.isdigit():
        expected_val = int(expected)
    elif expected.startswith('"') and expected.endswith('"'):
        expected_val = expected[1:-1]
    else:
        expected_val = expected

    if actual != expected_val:
        raise AssertionError(
            f"drafts[{idx}].{field}: expected {expected_val!r}, got {actual!r}"
        )


@then('the response field {field} does not equal "{expected}"')
def step_response_field_does_not_equal(
    context: Context, field: str, expected: str
) -> None:
    response = _last_response(context)
    found, value = _resolve_dotted_field(response, field)
    if not found:
        raise AssertionError(
            f"Response has no field {field!r}. Available top-level fields: "
            f"{sorted(response.keys())}"
        )
    if value == expected:
        raise AssertionError(
            f"Field {field!r} equals {expected!r}, which this scenario "
            f"asserts it must not."
        )






@then('the JSON response is remembered as "{key}"')
@when('the JSON response is remembered as "{key}"')
def step_remember_json_response(context: Context, key: str) -> None:
    import copy
    context.remembered_responses = getattr(context, "remembered_responses", {}) or {}
    context.remembered_responses[key] = copy.deepcopy(context.last_response)


@then('the JSON response equals the remembered "{key}"')
def step_json_response_equals_remembered(context: Context, key: str) -> None:
    remembered = (getattr(context, "remembered_responses", None) or {}).get(key)
    if remembered is None:
        raise AssertionError(f"No remembered response found for key {key!r}")
    actual = context.last_response
    if actual != remembered:
        raise AssertionError(f"JSON response changed:\nRemembered: {remembered}\nActual: {actual}")


@then('the JSON response has the exact same keys and values except "{except_key}" as "{key}"')
def step_json_response_same_except(context: Context, except_key: str, key: str) -> None:
    remembered = (getattr(context, "remembered_responses", None) or {}).get(key)
    if remembered is None:
        raise AssertionError(f"No remembered response found for key {key!r}")
    actual = context.last_response
    rem_copy = {k: v for k, v in remembered.items() if k != except_key}
    act_copy = {k: v for k, v in actual.items() if k != except_key}
    if act_copy != rem_copy:
        raise AssertionError(
            f"Responses differ on fields other than {except_key!r}:\nRemembered: {rem_copy}\nActual: {act_copy}"
        )


@then('the caller sink subdirectory "{caller_id}" exists with mode {mode}')
def step_caller_sink_subdir_exists_mode(context: Context, caller_id: str, mode: str) -> None:
    import stat
    sink_dir = getattr(context, "attachment_sink_dir", None)
    if sink_dir is None:
        raise AssertionError("Sink directory is not configured")
    caller_dir = sink_dir / caller_id
    if not caller_dir.is_dir():
        raise AssertionError(f"Caller sink subdirectory {caller_dir} does not exist")
    dir_stat = caller_dir.stat()
    dir_mode = oct(stat.S_IMODE(dir_stat.st_mode))
    expected_oct = oct(int(mode, 8))
    if dir_mode != expected_oct:
        raise AssertionError(f"Directory mode for {caller_dir}: expected {expected_oct}, got {dir_mode}")


@then('the visible folder entry "{folder}" for "{account}" in describe_policy has:')
def step_visible_folder_entry_describe_policy(
    context: Context, folder: str, account: str
) -> None:
    response = _last_response(context)
    accounts = response.get("accounts", [])
    acct = next((a for a in accounts if a.get("id") == account), None)
    if acct is None:
        raise AssertionError(
            f"Account {account!r} not found in describe_policy response: {[a.get('id') for a in accounts]}"
        )
    folders = acct.get("folders_visible", [])
    f_entry = next((f for f in folders if f.get("path") == folder), None)
    if f_entry is None:
        raise AssertionError(
            f"Folder {folder!r} not found in folders_visible for {account}: {[f.get('path') for f in folders]}"
        )

    for row in context.table:
        field = row["field"]
        expected_raw = row["value"]
        if expected_raw == "true":
            expected: Any = True
        elif expected_raw == "false":
            expected = False
        else:
            expected = expected_raw

        actual = f_entry.get(field)
        if actual != expected:
            raise AssertionError(
                f"Field {field!r} on folder {folder}: expected {expected!r}, got {actual!r}"
            )


@then("the response field saved_path ends with the response field saved_to")
def step_saved_path_ends_with_saved_to(context: Context) -> None:
    response = _last_response(context)
    saved_path = response.get("saved_path", "")
    saved_to = response.get("saved_to", "")
    if not saved_path or not saved_to:
        raise AssertionError(f"saved_path ({saved_path!r}) or saved_to ({saved_to!r}) missing in response: {response}")
    if not saved_path.endswith(saved_to):
        raise AssertionError(f"saved_path {saved_path!r} does not end with saved_to {saved_to!r}")


@when('the response field saved_path is remembered as "{key}"')
@then('the response field saved_path is remembered as "{key}"')
def step_remember_saved_path(context: Context, key: str) -> None:
    remembered = getattr(context, "remembered_values", None)
    if remembered is None:
        remembered = {}
        context.remembered_values = remembered
    response = _last_response(context)
    remembered[key] = response.get("saved_path")


@then('the response field saved_path does not equal the remembered "{key}"')
def step_saved_path_not_equal_remembered(context: Context, key: str) -> None:
    remembered = getattr(context, "remembered_values", {}).get(key)
    response = _last_response(context)
    actual = response.get("saved_path")
    if actual == remembered:
        raise AssertionError(f"saved_path unexpectedly equals remembered {key!r}: {actual!r}")


@then('the caller sink subdirectory "{caller_id}" contains {count:d} files')
def step_caller_sink_subdir_contains_n_files(context: Context, caller_id: str, count: int) -> None:
    sink_dir = getattr(context, "attachment_sink_dir", None)
    if sink_dir is None:
        raise AssertionError("Sink directory is not configured")
    caller_dir = sink_dir / caller_id
    if not caller_dir.is_dir():
        if count == 0:
            return
        raise AssertionError(f"Caller sink subdirectory {caller_dir} does not exist")
    files = [f for f in caller_dir.iterdir() if f.is_file()]
    if len(files) != count:
        raise AssertionError(f"Expected {count} files in {caller_dir}, found {len(files)}: {files}")


@then("the sink base directory contains {count:d} files")
def step_sink_base_dir_contains_n_files(context: Context, count: int) -> None:
    sink_dir = getattr(context, "attachment_sink_dir", None)
    if sink_dir is None or not sink_dir.is_dir():
        if count == 0:
            return
        raise AssertionError(f"Sink base directory {sink_dir} does not exist")
    files = [f for f in sink_dir.rglob("*") if f.is_file()]
    if len(files) != count:
        raise AssertionError(f"Expected {count} files in sink base {sink_dir}, found {len(files)}: {files}")


@then('the IMAP folder "{folder}" is empty')
def step_imap_folder_is_empty_assert(context: Context, folder: str) -> None:
    from support.imap_fixture import resolve_account
    account_id = _account_for_folder(context, folder)
    instance, user = resolve_account(account_id)
    if instance == "mock-gmail":
        state = getattr(context, "gmail_state", None)
        msgs = state.messages_in_folder(folder) if state else []
        if msgs:
            raise AssertionError(f"Expected {folder} to be empty, found {len(msgs)} messages")
    else:
        conn = context.imap.connect(instance, user)
        conn.select(context.imap.to_wire(instance, user, folder))
        status, data = conn.uid("SEARCH", None, "ALL")
        if status != "OK":
            raise AssertionError(f"IMAP SEARCH failed on {folder}: {data}")
        uids = data[0].split() if data and data[0] else []
        if uids:
            raise AssertionError(f"Expected {folder} to be empty, found {len(uids)} UIDs: {uids}")


@then('the IMAP folder "{folder}" contains no message with subject "{subject}"')
def step_imap_folder_contains_no_subject(
    context: Context, folder: str, subject: str
) -> None:
    _assert_subject_presence(context, folder, subject, should_exist=False, exact_count=None)


@then(
    'the IMAP folder "{folder}" contains exactly {count:d} message '
    'with subject "{subject}"'
)
@then(
    'the IMAP folder "{folder}" contains exactly {count:d} messages '
    'with subject "{subject}"'
)
def step_imap_folder_contains_n_with_subject(
    context: Context, folder: str, count: int, subject: str
) -> None:
    _assert_subject_presence(
        context, folder, subject, should_exist=count > 0, exact_count=count
    )


@then(
    'the IMAP message in folder "{folder}" with subject "{subject}" '
    'has no header "{header}"'
)
def step_imap_message_with_subject_has_no_header(
    context: Context, folder: str, subject: str, header: str
) -> None:
    from support.imap_fixture import resolve_account

    account_id = _account_for_folder(context, folder)
    instance, user = resolve_account(account_id)
    uid = context.imap.find_uid_by_decoded_subject(instance, user, folder, subject)
    if uid is None:
        raise AssertionError(
            f"Folder {folder!r} holds no message with subject {subject!r}."
        )
    message = context.imap.fetch_message(instance, user, folder, uid)
    headers_to_check = [h.strip('"\'') for h in header.split('" and no header "')]
    for h in headers_to_check:
        present = message.get_all(h)
        if present:
            raise AssertionError(
                f"Message {subject!r} in {folder!r} still carries header "
                f"{h!r}: {present!r}"
            )


@then('the most recent audit entry has field "{field}" starting with "{prefix}"')
def step_audit_entry_field_starts_with(context: Context, field: str, prefix: str) -> None:
    from support.audit_reader import AuditReader

    reader = AuditReader(context.audit_dir)
    recs = list(reader.records())
    if not recs:
        raise AssertionError("No audit records found in audit log directory")
    last_rec = recs[-1].record
    val = str(last_rec.get(field, ""))
    if not val.startswith(prefix):
        raise AssertionError(
            f"Expected most recent audit entry field {field!r} to start with {prefix!r}, got {val!r}"
        )


@then('the most recent audit entry for tool "{tool}" has field "{field}" starting with "{prefix}"')
def step_audit_entry_tool_field_starts_with(context: Context, tool: str, field: str, prefix: str) -> None:
    from support.audit_reader import AuditReader

    reader = AuditReader(context.audit_dir)
    recs = [r.record for r in reader.records() if r.record.get("tool") == tool]
    if not recs:
        raise AssertionError(f"No audit records found for tool {tool!r}")
    last_rec = recs[-1]
    val = str(last_rec.get(field, ""))
    if not val.startswith(prefix):
        raise AssertionError(
            f"Expected most recent audit entry for tool {tool!r} field {field!r} to start with {prefix!r}, got {val!r}"
        )


@then("the audit file does NOT contain any of the literal strings:")
def step_audit_file_does_not_contain_literal_strings(context: Context) -> None:
    if not hasattr(context, "audit_dir") or not context.audit_dir.exists():
        raise AssertionError("Audit directory does not exist or is not configured")
    paths = list(context.audit_dir.glob("*.jsonl"))
    if not paths:
        raise AssertionError("No audit .jsonl files found in audit directory")
    all_text = ""
    for path in paths:
        all_text += path.read_text(encoding="utf-8", errors="replace") + "\n"
    if not all_text.strip():
        raise AssertionError("Audit files exist but contain 0 records / empty text")
    for row in context.table:
        forbidden = row["forbidden_string"]
        if forbidden in all_text:
            raise AssertionError(f"Forbidden string {forbidden!r} leaked in audit file")


@then('the IMAP message in folder "{folder}" on "{account_id}" with subject "{subject}" has header "From" equal to "{expected}"')
def step_imap_message_header_from_equal(
    context: Context, folder: str, account_id: str, subject: str, expected: str
) -> None:
    from support.imap_fixture import resolve_account

    instance, user = resolve_account(account_id)
    uid = context.imap.find_unique_uid_by_decoded_subject(instance, user, folder, subject)
    if uid is None:
        raise AssertionError(f"No message with subject {subject!r} found in {folder!r} on {account_id!r}")
    msg = context.imap.fetch_message(instance, user, folder, uid)
    actual_from = msg.get("From", "")
    if actual_from != expected:
        raise AssertionError(f"Header From: expected {expected!r}, got {actual_from!r}")


_use_step_matcher_assert("re")


@then(r'the startup error indicates "(?P<m1>[^"]+)" and "(?P<m2>[^"]+)"')
def step_startup_error_indicates_two(context: Context, m1: str, m2: str) -> None:
    proc = getattr(context, "startup_proc", None)
    stderr = getattr(context, "bootstrap_stderr", "")
    if proc is not None:
        stderr = proc.stderr
    if not stderr:
        stderr = getattr(context, "http_startup_error", "")
    if not stderr:
        stderr = getattr(context, "startup_error", "")
    if m1 not in stderr or m2 not in stderr:
        raise AssertionError(f"Expected startup error mentioning {m1!r} and {m2!r}, got:\n{stderr}")


@then(r'the startup error indicates "(?P<msg>[^"]+)"')
def step_startup_error_indicates(context: Context, msg: str) -> None:
    proc = getattr(context, "startup_proc", None)
    stderr = getattr(context, "bootstrap_stderr", "")
    if proc is not None:
        stderr = proc.stderr
    if not stderr:
        stderr = getattr(context, "http_startup_error", "")
    if not stderr:
        stderr = getattr(context, "startup_error", "")
    if msg not in stderr:
        raise AssertionError(f"Expected startup error mentioning {msg!r}, got:\n{stderr}")


_use_step_matcher_assert("parse")


def _create_historical_sealed_file(audit_dir: Path, filename: str) -> None:
    path = audit_dir / filename
    date_str = filename[:10]
    rec0 = {
        "seq": 0,
        "ts": f"{date_str}T10:00:00.000Z",
        "tool": "list_accounts",
        "caller_id": "invoice-agent",
        "caller_addr": "stdio",
        "decision": "ALLOW",
        "reason": "authenticated",
        "result": "OK",
        "prev_hash": "sha256:0000000000000000000000000000000000000000000000000000000000000000",
    }
    line0 = json.dumps(rec0, sort_keys=True, separators=(",", ":")).encode("utf-8") + b"\n"
    h0 = hashlib.sha256(line0).hexdigest()
    rec1 = {
        "seq": 1,
        "ts": f"{date_str}T23:59:59.999Z",
        "tool": "eof_day",
        "final_hash": f"sha256:{h0}",
        "prev_hash": f"sha256:{h0}",
    }
    line1 = json.dumps(rec1, sort_keys=True, separators=(",", ":")).encode("utf-8") + b"\n"
    path.write_bytes(line0 + line1)
    path.chmod(0o400)


@given("the audit directory contains historical sealed day files:")
def step_audit_dir_contains_historical_files(context: Context) -> None:
    for row in context.table:
        _create_historical_sealed_file(context.audit_dir, row["filename"])


@given('the audit directory contains historical sealed file "{filename}"')
def step_audit_dir_contains_historical_file(context: Context, filename: str) -> None:
    _create_historical_sealed_file(context.audit_dir, filename)


@given('the audit directory contains historical compressed archive "{filename}"')
def step_audit_dir_contains_historical_archive(context: Context, filename: str) -> None:
    import gzip

    path = context.audit_dir / filename
    date_str = filename[:10]
    rec0 = {
        "seq": 0,
        "ts": f"{date_str}T10:00:00.000Z",
        "tool": "list_accounts",
        "caller_id": "invoice-agent",
        "caller_addr": "stdio",
        "decision": "ALLOW",
        "reason": "authenticated",
        "result": "OK",
        "prev_hash": "sha256:0000000000000000000000000000000000000000000000000000000000000000",
    }
    line0 = json.dumps(rec0, sort_keys=True).encode("utf-8") + b"\n"
    h0 = hashlib.sha256(line0).hexdigest()
    rec1 = {
        "seq": 1,
        "ts": f"{date_str}T23:59:59.999Z",
        "tool": "eof_day",
        "final_hash": f"sha256:{h0}",
        "prev_hash": f"sha256:{h0}",
    }
    line1 = json.dumps(rec1, sort_keys=True).encode("utf-8") + b"\n"
    compressed = gzip.compress(line0 + line1)
    path.write_bytes(compressed)
    path.chmod(0o400)


@given('the SHA-256 digest of plain file "{path_raw}" is remembered')
def step_remember_plain_file_sha256(context: Context, path_raw: str) -> None:
    from features.steps.policy_steps import _scratch_substitute

    resolved = _scratch_substitute(context, path_raw)
    data = Path(resolved).read_bytes()
    context.remembered_sha256 = hashlib.sha256(data).hexdigest()


@then('decompressing "{path_raw}" yields the remembered SHA-256 digest')
def step_decompress_yields_remembered_sha256(context: Context, path_raw: str) -> None:
    import gzip

    from features.steps.policy_steps import _scratch_substitute

    resolved = _scratch_substitute(context, path_raw)
    data = gzip.decompress(Path(resolved).read_bytes())
    actual_sha = hashlib.sha256(data).hexdigest()
    expected_sha = getattr(context, "remembered_sha256", "")
    if actual_sha != expected_sha:
        raise AssertionError(f"Decompressed SHA-256: expected {expected_sha}, got {actual_sha}")


@then('the file "{path_raw}" exists with mode {mode:o}')
def step_file_exists_with_mode(context: Context, path_raw: str, mode: int) -> None:
    from features.steps.policy_steps import _scratch_substitute

    resolved = _scratch_substitute(context, path_raw)
    path = Path(resolved)
    if not path.is_file():
        raise AssertionError(f"File {path} does not exist")
    actual_mode = path.stat().st_mode & 0o777
    if actual_mode != mode:
        raise AssertionError(f"File {path} mode: expected {oct(mode)}, got {oct(actual_mode)}")


@then('the file "{path_raw}" does not exist')
def step_file_does_not_exist(context: Context, path_raw: str) -> None:
    from features.steps.policy_steps import _scratch_substitute

    resolved = _scratch_substitute(context, path_raw)
    path = Path(resolved)
    if path.exists():
        raise AssertionError(f"File {path} exists when it should not")


@then('the file "{path_raw}" exists as plain JSONL')
def step_file_exists_as_plain_jsonl(context: Context, path_raw: str) -> None:
    from features.steps.policy_steps import _scratch_substitute

    resolved = _scratch_substitute(context, path_raw)
    path = Path(resolved)
    if not path.is_file():
        raise AssertionError(f"File {path} does not exist")
    if path.name.endswith(".gz"):
        raise AssertionError(f"File {path} is compressed (.gz)")


@then('the retention state of base date "{base_date}" in "{dir_path_raw}" is "{final_state}"')
def step_retention_state_of_base_date(
    context: Context, base_date: str, dir_path_raw: str, final_state: str
) -> None:
    from features.steps.policy_steps import _scratch_substitute

    resolved = _scratch_substitute(context, dir_path_raw)
    audit_dir = Path(resolved)
    plain = audit_dir / f"{base_date}.jsonl"
    gz = audit_dir / f"{base_date}.jsonl.gz"

    if final_state == "hot_sealed":
        if not plain.is_file():
            raise AssertionError(f"Expected hot_sealed plain file {plain} to exist")
        mode = plain.stat().st_mode & 0o400
        if mode != 0o400:
            raise AssertionError(f"File {plain} mode: expected 0400, got {oct(mode)}")
    elif final_state == "warm":
        if not gz.is_file():
            raise AssertionError(f"Expected warm compressed file {gz} to exist")
        mode = gz.stat().st_mode & 0o400
        if mode != 0o400:
            raise AssertionError(f"File {gz} mode: expected 0400, got {oct(mode)}")
        if plain.exists():
            raise AssertionError(f"Plain file {plain} should not exist in warm state")
    elif final_state == "deleted":
        if plain.exists() or gz.exists():
            raise AssertionError(f"Neither {plain} nor {gz} should exist in deleted state")
    else:
        raise AssertionError(f"Unknown final_state {final_state!r}")


@given('an executable root-hook recorder script exists at "{script_raw}" writing its argv and environment keys to "{out_raw}"')
def step_create_root_hook_recorder(context: Context, script_raw: str, out_raw: str) -> None:
    from features.steps.policy_steps import _scratch_substitute

    script_path = Path(_scratch_substitute(context, script_raw))
    out_path = Path(_scratch_substitute(context, out_raw))
    script_path.parent.mkdir(parents=True, exist_ok=True)
    content = f"""#!/usr/bin/env python3
import sys, os, json
data = {{
    "argv": sys.argv,
    "env_keys": list(os.environ.keys())
}}
with open({str(out_path)!r}, "w", encoding="utf-8") as f:
    json.dump(data, f)
"""
    script_path.write_text(content, encoding="utf-8")
    script_path.chmod(0o755)


@given('the server is configured with audit directory "{dir_raw}" and external_root_hook {hook_argv_raw}')
def step_server_configured_audit_root_hook(context: Context, dir_raw: str, hook_argv_raw: str) -> None:
    import ast

    from features.steps.policy_steps import _ensure_builder, _scratch_substitute

    builder = _ensure_builder(context)
    builder.audit_directory = Path(_scratch_substitute(context, dir_raw))
    raw_list = ast.literal_eval(hook_argv_raw)
    resolved_list = [_scratch_substitute(context, item) for item in raw_list]
    builder.audit_external_root_hook = resolved_list
    builder.write()


@then('"{out_raw}" records {count:d} invocation whose argument equals the final_hash field of the eof_day record in "{day_file}"')
def step_root_hook_recorded_invocation(context: Context, out_raw: str, count: int, day_file: str) -> None:
    from features.steps.policy_steps import _scratch_substitute
    from support.audit_reader import AuditReader

    out_path = Path(_scratch_substitute(context, out_raw))
    if not out_path.is_file():
        raise AssertionError(f"Output file {out_path} does not exist")
    data = json.loads(out_path.read_text(encoding="utf-8"))
    argv = data.get("argv", [])
    reader = AuditReader(context.audit_dir)
    records = reader.read_file(day_file)
    eof_rec = next((r.record for r in records if r.record.get("tool") == "eof_day"), None)
    if not eof_rec:
        raise AssertionError(f"No eof_day record in {day_file}")
    expected_hash = eof_rec.get("final_hash")
    if not any(arg == expected_hash for arg in argv):
        raise AssertionError(f"Expected final_hash {expected_hash!r} in argv {argv!r}")


@then('"{out_raw}" environment keys do NOT include "{leaked_key}"')
def step_hook_out_env_keys_not_include(context: Context, out_raw: str, leaked_key: str) -> None:
    from features.steps.policy_steps import _scratch_substitute

    out_path = Path(_scratch_substitute(context, out_raw))
    data = json.loads(out_path.read_text(encoding="utf-8"))
    env_keys = data.get("env_keys", [])
    if leaked_key in env_keys:
        raise AssertionError(f"Environment key {leaked_key!r} leaked to child hook: {env_keys}")


@then('the server stderr contains a critical log line mentioning "{text}"')
def step_server_stderr_contains_critical(context: Context, text: str) -> None:
    stderr = context.mcp.stderr_text if context.mcp else ""
    if text not in stderr:
        raise AssertionError(f"Expected server stderr mentioning {text!r}, got:\n{stderr}")


@given("{caller_id} calls list_accounts {n:d} times")
@when("{caller_id} calls list_accounts {n:d} times")
def step_caller_calls_list_accounts_n_times(context: Context, caller_id: str, n: int) -> None:
    from features.steps.mcp_steps import _ensure_mcp_client, _store_result

    client = _ensure_mcp_client(context, caller_id)
    for _ in range(n):
        payload = client.call_tool("list_accounts", {})
        _store_result(context, payload)


@when("{caller_id} calls list_accounts, describe_policy, and tool_surface_info")
def step_caller_calls_three_tools(context: Context, caller_id: str) -> None:
    from features.steps.mcp_steps import _ensure_mcp_client, _store_result

    client = _ensure_mcp_client(context, caller_id)
    responses = []
    for tool in ("list_accounts", "describe_policy", "tool_surface_info"):
        payload = client.call_tool(tool, {})
        _store_result(context, payload)
        responses.append(payload)
    context.three_tool_responses = responses


@then('none of the tool responses contains any prev_hash value present in "{filename_raw}"')
def step_none_contains_prev_hash(context: Context, filename_raw: str) -> None:
    from features.steps.policy_steps import _scratch_substitute

    resolved = _scratch_substitute(context, filename_raw)
    path = Path(resolved)
    if not path.is_file():
        raise AssertionError(f"File {path} does not exist")
    prev_hashes = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rec = json.loads(line)
            if rec.get("prev_hash"):
                prev_hashes.add(rec["prev_hash"])

    responses = getattr(context, "three_tool_responses", [])
    for resp in responses:
        resp_str = json.dumps(resp)
        for ph in prev_hashes:
            if ph in resp_str:
                raise AssertionError(f"prev_hash {ph!r} leaked in tool response: {resp_str}")


@then('file "{filename}" contains an outcome record for tool "{tool}" carrying non-empty fields "{f1}" and "{f2}"')
def step_file_contains_outcome_record_carrying(
    context: Context, filename: str, tool: str, f1: str, f2: str
) -> None:
    from support.audit_reader import AuditReader

    reader = AuditReader(context.audit_dir)
    records = reader.read_file(filename)
    matches = [
        r.record
        for r in records
        if r.record.get("tool") == tool and r.record.get("phase") == "outcome"
    ]
    if not matches:
        raise AssertionError(f"No outcome record for tool {tool!r} in {filename}")
    found = False
    for rec in matches:
        if rec.get(f1) and rec.get(f2):
            found = True
            break
    if not found:
        raise AssertionError(f"No outcome record carries non-empty {f1} and {f2} in {matches!r}")


@then('the directory "{dir_path_raw}" has mode {mode:o}')
def step_directory_has_mode(context: Context, dir_path_raw: str, mode: int) -> None:
    from features.steps.policy_steps import _scratch_substitute

    resolved = _scratch_substitute(context, dir_path_raw)
    path = Path(resolved)
    if not path.is_dir():
        raise AssertionError(f"Directory {path} does not exist")
    actual_mode = path.stat().st_mode & 0o777
    if actual_mode != mode:
        raise AssertionError(f"Directory {path} mode: expected {oct(mode)}, got {oct(actual_mode)}")


@given('the server is armed with IMAP_MCP_TEST_INJECT_INTERNAL_ERROR="{inject_val}"')
def step_server_armed_with_internal_error(context: Context, inject_val: str) -> None:
    extra = getattr(context, "mcp_extra_env", None) or {}
    extra["IMAP_MCP_TEST_INJECT_INTERNAL_ERROR"] = inject_val
    context.mcp_extra_env = extra


@when("the server process is killed with SIGKILL")
def step_server_killed_sigkill(context: Context) -> None:
    if context.mcp is not None:
        context.mcp._proc.kill()
        context.mcp._proc.wait()
    if getattr(context, "mcp_clients", None):
        context.mcp_clients.clear()
    context.mcp = None


@when('the server is restarted and {caller_id} calls list_accounts')
def step_server_restarted_and_caller_calls(context: Context, caller_id: str) -> None:
    if getattr(context, "mcp_clients", None):
        for c in context.mcp_clients.values():
            try:
                c.close()
            except Exception:
                pass
        context.mcp_clients.clear()
    if context.mcp is not None:
        try:
            context.mcp.close()
        except Exception:
            pass
        context.mcp = None
    from features.steps.mcp_steps import _ensure_mcp_client, _store_result

    client = _ensure_mcp_client(context, caller_id)
    payload = client.call_tool("list_accounts", {})
    _store_result(context, payload)


@when('the server process is started without IMAP_MCP_TEST_MODE and with environment {hook_var}="{hook_val}"')
def step_server_started_without_test_mode(context: Context, hook_var: str, hook_val: str) -> None:
    from features.steps.mcp_steps import _server_binary
    from support.server_bootstrap import try_bootstrap_server

    builder = getattr(context, "policy_builder", None)
    if builder is not None:
        builder.write()
    env = dict(os.environ)
    env.pop("IMAP_MCP_TEST_MODE", None)
    env[hook_var] = hook_val
    caller = next(iter(context.policy_builder.callers), None) if getattr(context, "policy_builder", None) else None
    caller_id = caller.id if caller else "bootstrap-probe"
    res = try_bootstrap_server(
        _server_binary(context),
        context.config_dir,
        caller_id=caller_id,
        extra_env=env,
        timeout=3.0,
    )
    if res.timed_out:
        raise AssertionError(f"Server process hung when started with {hook_var}={hook_val}")
    context.startup_proc = res


@when('an external writer alters the "{field}" value of record index {index:d} in "{filename}"')
def step_external_writer_alters_record(context: Context, field: str, index: int, filename: str) -> None:
    path = context.audit_dir / filename
    lines = [l for l in path.read_bytes().splitlines(keepends=True) if l.strip()]
    rec = json.loads(lines[index])
    rec[field] = "tampered_value"
    lines[index] = json.dumps(rec).encode("utf-8") + b"\n"
    path.write_bytes(b"".join(lines))


@then('the offline chain verifier reports records {idx1:d} and {idx2:d} as valid and record {idx3:d} as broken prev_hash')
def step_offline_chain_verifier_reports(
    context: Context, idx1: int, idx2: int, idx3: int
) -> None:
    files = sorted(context.audit_dir.glob("*.jsonl"))
    if not files:
        raise AssertionError("No audit files in audit directory")
    path = files[-1]
    lines = [l for l in path.read_bytes().splitlines(keepends=True) if l.strip()]
    valid_indices = []
    broken_indices = []
    prev_line = None
    for seq, line in enumerate(lines):
        rec = json.loads(line)
        if seq == 0:
            valid_indices.append(seq)
        else:
            expected_prev = "sha256:" + hashlib.sha256(prev_line).hexdigest()
            if rec.get("prev_hash") == expected_prev:
                valid_indices.append(seq)
            else:
                broken_indices.append(seq)
        prev_line = line
    if idx1 not in valid_indices or idx2 not in valid_indices:
        raise AssertionError(f"Expected records {idx1} and {idx2} to be valid, got valid: {valid_indices}")
    if idx3 not in broken_indices:
        raise AssertionError(f"Expected record {idx3} to be broken, got broken: {broken_indices}")


def _parse_folder_spec_local(context: Context, folder_spec: str) -> tuple[str, str, str, str]:
    from support.imap_fixture import resolve_account

    if ":" in folder_spec:
        account_id, _, folder = folder_spec.partition(":")
    else:
        account_id = _account_for_folder(context, folder_spec)
        folder = folder_spec
    instance, user = resolve_account(account_id)
    return account_id, instance, user, folder


def _get_body_text_local(msg: Any) -> str:
    if msg.is_multipart():
        parts = []
        for part in msg.walk():
            if part.get_content_type() == "text/plain":
                payload = part.get_payload(decode=True)
                if payload:
                    parts.append(payload.decode("utf-8", errors="replace"))
        return "".join(parts)
    payload = msg.get_payload(decode=True)
    if payload:
        return payload.decode("utf-8", errors="replace")
    return ""


@then('within {seconds:d} seconds the WAL entry for the crashed move has status "{status}"')
@then('within {seconds:d} seconds the WAL entry for the crashed draft revision has status "{status}"')
def step_wal_entry_crashed_status_within(context: Context, seconds: int, status: str) -> None:
    import time

    from support.wal_reader import WALReader

    tx_id = getattr(context, "crashed_tx_id", None) or getattr(context, "last_tx_id", None)
    if not tx_id:
        raise AssertionError("No crashed tx_id recorded in context")
    reader = WALReader(context.wal_path)
    deadline = time.time() + seconds
    last_status = None
    while time.time() < deadline:
        tx = reader.transaction(tx_id)
        if tx is not None:
            last_status = tx.status
            if last_status == status:
                return
        time.sleep(0.2)
    raise AssertionError(
        f"WAL tx {tx_id!r} did not reach status {status!r} within {seconds}s (last: {last_status!r})"
    )


@then('the WAL entry for the crashed move has status "{status}"')
def step_wal_entry_crashed_status(context: Context, status: str) -> None:
    from support.wal_reader import WALReader

    tx_id = getattr(context, "crashed_tx_id", None) or getattr(context, "last_tx_id", None)
    if not tx_id:
        raise AssertionError("No crashed tx_id recorded in context")
    reader = WALReader(context.wal_path)
    tx = reader.transaction(tx_id)
    if tx is None:
        raise AssertionError(f"No WAL entry found for tx_id {tx_id!r}")
    if tx.status != status:
        raise AssertionError(f"Expected WAL tx {tx_id!r} status {status!r}, got {tx.status!r}")


@then('within {seconds:d} seconds the WAL entry for the crashed move has retry_count >= {min_count:d}')
def step_wal_entry_crashed_retry_count_within_gte(context: Context, seconds: int, min_count: int) -> None:
    import time

    from support.wal_reader import WALReader

    tx_id = getattr(context, "crashed_tx_id", None) or getattr(context, "last_tx_id", None)
    if not tx_id:
        raise AssertionError("No crashed tx_id recorded in context")
    reader = WALReader(context.wal_path)
    deadline = time.time() + seconds
    last_count = None
    while time.time() < deadline:
        tx = reader.transaction(tx_id)
        if tx is not None:
            last_count = tx.retry_count
            if last_count >= min_count:
                return
        time.sleep(0.2)
    raise AssertionError(
        f"WAL tx {tx_id!r} did not reach retry_count >= {min_count} within {seconds}s (last: {last_count!r})"
    )


@then('the WAL entry for the crashed move has retry_count >= {min_count:d}')
def step_wal_entry_crashed_retry_count_gte(context: Context, min_count: int) -> None:
    from support.wal_reader import WALReader

    tx_id = getattr(context, "crashed_tx_id", None) or getattr(context, "last_tx_id", None)
    if not tx_id:
        raise AssertionError("No crashed tx_id recorded in context")
    reader = WALReader(context.wal_path)
    tx = reader.transaction(tx_id)
    if tx is None:
        raise AssertionError(f"No WAL entry found for tx_id {tx_id!r}")
    if tx.retry_count < min_count:
        raise AssertionError(f"Expected WAL tx {tx_id!r} retry_count >= {min_count}, got {tx.retry_count}")


@then('the WAL entry for the crashed move has retry_count {expected_count:d}')
def step_wal_entry_crashed_retry_count(context: Context, expected_count: int) -> None:
    from support.wal_reader import WALReader

    tx_id = getattr(context, "crashed_tx_id", None) or getattr(context, "last_tx_id", None)
    if not tx_id:
        raise AssertionError("No crashed tx_id recorded in context")
    reader = WALReader(context.wal_path)
    tx = reader.transaction(tx_id)
    if tx is None:
        raise AssertionError(f"No WAL entry found for tx_id {tx_id!r}")
    if tx.retry_count != expected_count:
        raise AssertionError(f"Expected WAL tx {tx_id!r} retry_count {expected_count}, got {tx.retry_count}")


@when('the WAL retry_count for the crashed move is set to {count:d}')
def step_wal_retry_count_set(context: Context, count: int) -> None:
    import sqlite3

    tx_id = getattr(context, "crashed_tx_id", None) or getattr(context, "last_tx_id", None)
    if not tx_id:
        raise AssertionError("No crashed tx_id recorded in context")
    with sqlite3.connect(context.wal_path) as conn:
        conn.execute("UPDATE transactions SET retry_count = ? WHERE tx_id = ?", (count, tx_id))


@then('the WAL entry for the crashed move has staged_rfc822 equal to null')
def step_wal_entry_crashed_staged_rfc822_null(context: Context) -> None:
    from support.wal_reader import WALReader

    tx_id = getattr(context, "crashed_tx_id", None) or getattr(context, "last_tx_id", None)
    if not tx_id:
        raise AssertionError("No crashed tx_id recorded in context")
    reader = WALReader(context.wal_path)
    tx = reader.transaction(tx_id)
    if tx is None:
        raise AssertionError(f"No WAL entry found for tx_id {tx_id!r}")
    if tx.staged_rfc822 is not None:
        raise AssertionError(f"Expected staged_rfc822 to be NULL for tx_id {tx_id!r}, got {len(tx.staged_rfc822)} bytes")


@then('the WAL database file exists with mode {mode} and {count:d} transaction entries')
def step_wal_db_mode_and_count(context: Context, mode: str, count: int) -> None:
    import stat

    if not context.wal_path.exists():
        raise AssertionError(f"WAL database file {context.wal_path} does not exist")
    st = context.wal_path.stat()
    actual_mode = oct(stat.S_IMODE(st.st_mode))
    expected_mode = oct(int(mode, 8))
    if actual_mode != expected_mode:
        raise AssertionError(f"WAL file mode expected {expected_mode}, got {actual_mode}")
    from support.wal_reader import WALReader

    reader = WALReader(context.wal_path)
    txs = reader.all_transactions()
    if len(txs) != count:
        raise AssertionError(f"WAL transaction entries expected {count}, got {len(txs)}")


@given('an identical copy of uid {uid:d} from "{src_box}" already exists in "{dst_box}"')
def step_identical_copy_already_exists(
    context: Context, uid: int, src_box: str, dst_box: str
) -> None:
    from features.steps.policy_steps import flush_staged_messages

    flush_staged_messages(context)
    src_acc, _, src_folder = src_box.partition(":")
    dst_acc, _, dst_folder = dst_box.partition(":")
    from support.imap_fixture import resolve_account

    src_inst, src_user = resolve_account(src_acc)
    dst_inst, dst_user = resolve_account(dst_acc)
    from support.imap_fixture import resolve_uid

    actual_uid = resolve_uid(context, src_acc, src_folder, uid)
    raw = context.imap.fetch_raw(src_inst, src_user, src_folder, actual_uid)
    context.imap.append_verbatim(dst_inst, dst_user, dst_folder, raw)


@given('two identical copies of uid {uid:d} from "{src_box}" already exist in "{dst_box}"')
def step_two_identical_copies_already_exist(
    context: Context, uid: int, src_box: str, dst_box: str
) -> None:
    from features.steps.policy_steps import flush_staged_messages

    flush_staged_messages(context)
    src_acc, _, src_folder = src_box.partition(":")
    dst_acc, _, dst_folder = dst_box.partition(":")
    from support.imap_fixture import resolve_account, resolve_uid

    src_inst, src_user = resolve_account(src_acc)
    dst_inst, dst_user = resolve_account(dst_acc)
    actual_uid = resolve_uid(context, src_acc, src_folder, uid)
    raw = context.imap.fetch_raw(src_inst, src_user, src_folder, actual_uid)
    context.imap.append_verbatim(dst_inst, dst_user, dst_folder, raw)
    context.imap.append_verbatim(dst_inst, dst_user, dst_folder, raw)


@given(
    'the folder "{folder}" holds a message at uid {uid:d} with message-id "{message_id}", '
    'from "{from_addr}", to "{to_addr}", subject "{subject}", date "{date_str}", '
    'and body prefix "{prefix}" repeated {count:d} times followed by "{tail}"'
)
def step_folder_holds_message_with_prefix_tail_uid(
    context: Context,
    folder: str,
    uid: int,
    message_id: str,
    from_addr: str,
    to_addr: str,
    subject: str,
    date_str: str,
    prefix: str,
    count: int,
    tail: str,
) -> None:
    from email.utils import format_datetime

    _, instance, user, fld = _parse_folder_spec_local(context, folder)
    body = prefix * count + tail
    parsed_dt = datetime.fromisoformat(date_str)
    if parsed_dt.tzinfo is None:
        parsed_dt = parsed_dt.replace(tzinfo=UTC)
    formatted_date = format_datetime(parsed_dt)
    msg_bytes = (
        f"From: {from_addr}\r\n"
        f"To: {to_addr}\r\n"
        f"Subject: {subject}\r\n"
        f"Message-ID: {message_id}\r\n"
        f"Date: {formatted_date}\r\n"
        f"Content-Type: text/plain; charset=utf-8\r\n"
        f"Content-Transfer-Encoding: 7bit\r\n"
        f"MIME-Version: 1.0\r\n\r\n"
        f"{body}\r\n"
    ).encode()
    context.imap.append_verbatim(instance, user, fld, msg_bytes)
    all_uids = context.imap.folder_uids(instance, user, fld)
    u = all_uids[-1] if all_uids else uid
    lookup = getattr(context, "message_uids", None)
    if lookup is None:
        lookup = {}
        context.message_uids = lookup
    acc_id, _, _, _ = _parse_folder_spec_local(context, folder)
    lookup[(acc_id, fld, uid)] = u


@given(
    'the folder "{folder}" holds a message with message-id "{message_id}", '
    'from "{from_addr}", to "{to_addr}", subject "{subject}", date "{date_str}", '
    'and body prefix "{prefix}" repeated {count:d} times followed by "{tail}"'
)
def step_folder_holds_message_with_prefix_tail(
    context: Context,
    folder: str,
    message_id: str,
    from_addr: str,
    to_addr: str,
    subject: str,
    date_str: str,
    prefix: str,
    count: int,
    tail: str,
) -> None:
    from email.utils import format_datetime

    _, instance, user, fld = _parse_folder_spec_local(context, folder)
    body = prefix * count + tail
    parsed_dt = datetime.fromisoformat(date_str)
    if parsed_dt.tzinfo is None:
        parsed_dt = parsed_dt.replace(tzinfo=UTC)
    formatted_date = format_datetime(parsed_dt)
    msg_bytes = (
        f"From: {from_addr}\r\n"
        f"To: {to_addr}\r\n"
        f"Subject: {subject}\r\n"
        f"Message-ID: {message_id}\r\n"
        f"Date: {formatted_date}\r\n"
        f"Content-Type: text/plain; charset=utf-8\r\n"
        f"Content-Transfer-Encoding: 7bit\r\n"
        f"MIME-Version: 1.0\r\n\r\n"
        f"{body}\r\n"
    ).encode()
    context.imap.append_verbatim(instance, user, fld, msg_bytes)


@given('the folder "{folder}" holds a message without Message-ID:')
def step_folder_holds_message_without_message_id(context: Context, folder: str) -> None:
    _, instance, user, fld = _parse_folder_spec_local(context, folder)
    for row in context.table:
        uid = int(row["uid"])
        from_addr = row["from_address"]
        to_addr = row["to_address"]
        subject = row["subject"]
        date_str = row["date"]
        exact_size = int(row["exact_size_bytes"])

        parsed_dt = datetime.fromisoformat(date_str)
        if parsed_dt.tzinfo is None:
            parsed_dt = parsed_dt.replace(tzinfo=UTC)
        formatted_date = email.utils.format_datetime(parsed_dt)

        header_str = (
            f"From: {from_addr}\r\n"
            f"To: {to_addr}\r\n"
            f"Subject: {subject}\r\n"
            f"Date: {formatted_date}\r\n"
            f"Content-Type: text/plain; charset=utf-8\r\n"
            f"Content-Transfer-Encoding: 7bit\r\n"
            f"MIME-Version: 1.0\r\n\r\n"
        )
        headers_len = len(header_str.encode("utf-8"))
        padding_needed = exact_size - headers_len
        if padding_needed < 0:
            raise AssertionError(f"Headers length {headers_len} exceeds exact_size {exact_size}")
        body = "X" * padding_needed
        msg_bytes = header_str.encode("utf-8") + body.encode("utf-8")
        if len(msg_bytes) != exact_size:
            raise AssertionError(f"Constructed bytes length {len(msg_bytes)} != {exact_size}")

        context.imap.append_verbatim(instance, user, fld, msg_bytes)
        all_uids = context.imap.folder_uids(instance, user, fld)
        u = all_uids[-1] if all_uids else uid
        lookup = getattr(context, "message_uids", None)
        if lookup is None:
            lookup = {}
            context.message_uids = lookup
        acc_id, _, _, _ = _parse_folder_spec_local(context, folder)
        lookup[(acc_id, fld, uid)] = u


@then('the folder "{folder}" contains {count:d} message with message-id "{message_id}"')
@then('the folder "{folder}" contains {count:d} messages with message-id "{message_id}"')
def step_folder_contains_messages_with_message_id(
    context: Context, folder: str, count: int, message_id: str
) -> None:
    _, instance, user, fld = _parse_folder_spec_local(context, folder)
    uids = context.imap.search_by_message_id(instance, user, fld, message_id)
    if len(uids) != count:
        raise AssertionError(
            f"Folder {folder!r} contains {len(uids)} message(s) with Message-ID {message_id!r}, "
            f"expected {count}"
        )


@then(
    '{count1:d} message in "{folder}" has body ending with "{tail1}" '
    'and {count2:d} has body ending with "{tail2}"'
)
def step_messages_have_body_ending_with(
    context: Context, count1: int, folder: str, tail1: str, count2: int, tail2: str
) -> None:
    _, instance, user, fld = _parse_folder_spec_local(context, folder)
    uids = context.imap.folder_uids(instance, user, fld)
    c1 = 0
    c2 = 0
    for u in uids:
        msg = context.imap.fetch_message(instance, user, fld, u)
        body = _get_body_text_local(msg).rstrip("\r\n")
        if body.endswith(tail1):
            c1 += 1
        elif body.endswith(tail2):
            c2 += 1
    if c1 != count1 or c2 != count2:
        raise AssertionError(
            f"Expected {count1} ending with {tail1!r} and {count2} ending with {tail2!r}, "
            f"got {c1} and {c2} among {len(uids)} messages in {folder}"
        )


@then(
    'the folder "{folder}" contains {count:d} message from "{from_addr}" '
    'with subject "{subject}" and exact RFC822 size {size:d} bytes'
)
def step_folder_contains_message_exact_size(
    context: Context, folder: str, count: int, from_addr: str, subject: str, size: int
) -> None:
    _, instance, user, fld = _parse_folder_spec_local(context, folder)
    uids = context.imap.folder_uids(instance, user, fld)
    matches = 0
    for u in uids:
        raw = context.imap.fetch_raw(instance, user, fld, u)
        msg = context.imap.fetch_message(instance, user, fld, u)
        msg_from = msg.get("From") or ""
        msg_subj = msg.get("Subject") or ""
        if from_addr in msg_from and msg_subj == subject and len(raw) == size:
            matches += 1
    if matches != count:
        raise AssertionError(
            f"Expected {count} message(s) from {from_addr!r} with subject {subject!r} "
            f"and size {size}, got {matches} in {folder}"
        )


@then('the folder "{folder}" contains {count:d} message with subject "{subject}"')
@then('the folder "{folder}" contains {count:d} messages with subject "{subject}"')
@then('the IMAP folder "{folder}" contains {count:d} message with subject "{subject}"')
@then('the IMAP folder "{folder}" contains {count:d} messages with subject "{subject}"')
def step_folder_contains_subject_count(
    context: Context, folder: str, count: int, subject: str
) -> None:
    _, instance, user, fld = _parse_folder_spec_local(context, folder)
    uids = context.imap.folder_uids(instance, user, fld)
    matches = 0
    for u in uids:
        msg = context.imap.fetch_message(instance, user, fld, u)
        if (msg.get("Subject") or "") == subject:
            matches += 1
    if matches != count:
        raise AssertionError(
            f"Folder {folder!r} contains {matches} messages with subject {subject!r}, expected {count}"
        )


@then('the IMAP folder "{folder}" contains {count:d} message with header "{header}" set')
@then('the IMAP folder "{folder}" contains {count:d} messages with header "{header}" set')
def step_folder_contains_messages_with_header_set(
    context: Context, folder: str, count: int, header: str
) -> None:
    _, instance, user, fld = _parse_folder_spec_local(context, folder)
    uids = context.imap.folder_uids(instance, user, fld)
    matches = 0
    for u in uids:
        msg = context.imap.fetch_message(instance, user, fld, u)
        if msg.get(header) is not None:
            matches += 1
    if matches != count:
        raise AssertionError(
            f"Folder {folder!r} contains {matches} messages with header {header!r} set, expected {count}"
        )


@then('{n:d} concurrent update_draft response has result "{res1}" with revision {rev:d} and {m:d} has result "{res2}" with error.type "{err_type}"')
def step_concurrent_update_draft_assertion(
    context: Context,
    n: int,
    res1: str,
    rev: int,
    m: int,
    res2: str,
    err_type: str,
) -> None:
    responses = getattr(context, "concurrent_update_responses", [])
    successes = [
        r for r in responses
        if r.get("result") == res1 and r.get("revision") == rev
    ]
    failures = [
        r for r in responses
        if r.get("result") == res2 and (r.get("error") or {}).get("type") == err_type
    ]
    if len(successes) != n or len(failures) != m:
        raise AssertionError(
            f"Expected {n} response(s) with result={res1!r}, rev={rev} and {m} with result={res2!r}, error.type={err_type!r}. Responses: {responses}"
        )


@then('the IMAP folder "{folder}" contains {count:d} message carrying header "{header}" equal to "{val}"')
@then('the IMAP folder "{folder}" contains {count:d} messages carrying header "{header}" equal to "{val}"')
def step_folder_contains_header_equal(
    context: Context, folder: str, count: int, header: str, val: str
) -> None:
    _, instance, user, fld = _parse_folder_spec_local(context, folder)
    uids = context.imap.folder_uids(instance, user, fld)
    matches = 0
    for u in uids:
        msg = context.imap.fetch_message(instance, user, fld, u)
        if (msg.get(header) or "") == val:
            matches += 1
    if matches != count:
        raise AssertionError(
            f"Folder {folder!r} contains {matches} messages with header {header!r} equal to {val!r}, expected {count}"
        )


@then('the response field {field} does NOT contain "{expected}"')
def step_response_field_does_not_contain(context: Context, field: str, expected: str) -> None:
    response = _last_response(context)
    found, actual = _resolve_dotted_field(response, field)
    if not found:
        raise AssertionError(f"Field {field!r} not found in response: {sorted(response.keys())}")
    if not isinstance(actual, (str, list, dict, set, tuple)):
        raise AssertionError(f"Field {field!r} is not a string or container (type: {type(actual).__name__})")
    if actual is not None and expected in str(actual):
        raise AssertionError(f"Field {field!r} contains {expected!r}: {actual!r}")


@then('the IMAP folder "{folder}" contains {count:d} message with keyword "{keyword}"')
@then('the IMAP folder "{folder}" contains {count:d} messages with keyword "{keyword}"')
def step_imap_folder_contains_keyword_count(
    context: Context, folder: str, count: int, keyword: str
) -> None:
    _, instance, user, fld = _parse_folder_spec_local(context, folder)
    uids = context.imap.folder_uids(instance, user, fld)
    if count == 0 and not uids:
        raise AssertionError(
            f"Positive control failed: folder {folder!r} holds 0 messages total; "
            f"cannot witness absence of keyword {keyword!r}."
        )
    matches = 0
    conn = context.imap.connect(instance, user)
    conn.select(context.imap._q(context.imap.to_wire(instance, user, fld)))
    for u in uids:
        status, data = conn.uid("FETCH", str(u), "(FLAGS)")
        if status == "OK" and data and data[0]:
            flags_str = str(data[0])
            if keyword in flags_str:
                matches += 1
    if matches != count:
        raise AssertionError(
            f"Expected {count} messages with keyword {keyword!r} in {folder}, got {matches}"
        )


@then('the IMAP folder "{folder}" contains {count:d} message with flag "{flag}"')
@then('the IMAP folder "{folder}" contains {count:d} messages with flag "{flag}"')
def step_imap_folder_contains_flag_count(
    context: Context, folder: str, count: int, flag: str
) -> None:
    _, instance, user, fld = _parse_folder_spec_local(context, folder)
    uids = context.imap.folder_uids(instance, user, fld)
    if count == 0 and not uids:
        raise AssertionError(
            f"Positive control failed: folder {folder!r} holds 0 messages total; "
            f"cannot witness absence of flag {flag!r}."
        )
    matches = 0
    conn = context.imap.connect(instance, user)
    conn.select(context.imap._q(context.imap.to_wire(instance, user, fld)))
    for u in uids:
        status, data = conn.uid("FETCH", str(u), "(FLAGS)")
        if status == "OK" and data and data[0]:
            flags_str = str(data[0])
            if flag in flags_str:
                matches += 1
    if matches != count:
        raise AssertionError(
            f"Expected {count} messages with flag {flag!r} in {folder}, got {matches}"
        )


@then('the response field error.detail is a non-empty string of at most 256 characters')
def step_response_field_error_detail_len_256(context: Context) -> None:
    response = _last_response(context)
    found, actual = _resolve_dotted_field(response, "error.detail")
    if not found or not isinstance(actual, str) or not actual or len(actual) > 256:
        raise AssertionError(f"Expected error.detail to be non-empty string <= 256 chars, got: {actual!r}")


def _matches_subject(msg: Any, subject: str) -> bool:
    from email.header import decode_header, make_header

    raw_subj = msg.get("Subject") or ""
    decoded = str(make_header(decode_header(raw_subj)))
    return decoded == subject or raw_subj == subject


@then('the IMAP message in folder "{folder}" with subject "{subject}" has header "{header}" equal to "{expected}"')
def step_imap_message_with_subject_has_header(
    context: Context, folder: str, subject: str, header: str, expected: str
) -> None:
    _, instance, user, fld = _parse_folder_spec_local(context, folder)
    uids = context.imap.folder_uids(instance, user, fld)
    for u in uids:
        msg = context.imap.fetch_message(instance, user, fld, u)
        if _matches_subject(msg, subject):
            actual = msg.get(header) or ""
            if actual != expected:
                raise AssertionError(f"Header {header!r} on message with subject {subject!r}: expected {expected!r}, got {actual!r}")
            return
    raise AssertionError(f"No message with subject {subject!r} found in {folder}")


@then('the IMAP message in folder "{folder}" with subject "{subject}" has Cc header containing "{a1}" and "{a2}"')
def step_imap_message_cc_containing_two(
    context: Context, folder: str, subject: str, a1: str, a2: str
) -> None:
    _, instance, user, fld = _parse_folder_spec_local(context, folder)
    uids = context.imap.folder_uids(instance, user, fld)
    for u in uids:
        msg = context.imap.fetch_message(instance, user, fld, u)
        if _matches_subject(msg, subject):
            cc = msg.get("Cc") or ""
            if a1 not in cc or a2 not in cc:
                raise AssertionError(f"Cc header {cc!r} does not contain both {a1!r} and {a2!r}")
            return
    raise AssertionError(f"No message with subject {subject!r} found in {folder}")


@then('the IMAP message in folder "{folder}" with subject "{subject}" has {header_name} header NOT containing "{val}"')
def step_imap_message_header_not_containing(
    context: Context, folder: str, subject: str, header_name: str, val: str
) -> None:
    _, instance, user, fld = _parse_folder_spec_local(context, folder)
    uids = context.imap.folder_uids(instance, user, fld)
    for u in uids:
        msg = context.imap.fetch_message(instance, user, fld, u)
        if _matches_subject(msg, subject):
            hdr_val = msg.get(header_name) or ""
            if val in hdr_val:
                raise AssertionError(f"{header_name} header {hdr_val!r} unexpectedly contains {val!r}")
            return
    raise AssertionError(f"No message with subject {subject!r} found in {folder}")


@then('the IMAP message in folder "{folder}" with subject "{subject}" carries 1 header "X-SC-Draft-Id", 1 header "X-SC-Draft-Rev", and 1 header "X-SC-Provenance"')
def step_imap_message_carries_draft_headers(
    context: Context, folder: str, subject: str
) -> None:
    _, instance, user, fld = _parse_folder_spec_local(context, folder)
    uids = context.imap.folder_uids(instance, user, fld)
    for u in uids:
        msg = context.imap.fetch_message(instance, user, fld, u)
        if _matches_subject(msg, subject):
            d_id = msg.get("X-SC-Draft-Id")
            d_rev = msg.get("X-SC-Draft-Rev")
            d_prov = msg.get("X-SC-Provenance")
            if not d_id or not d_rev or not d_prov:
                raise AssertionError(
                    f"Message missing required draft headers: X-SC-Draft-Id={d_id!r}, "
                    f"X-SC-Draft-Rev={d_rev!r}, X-SC-Provenance={d_prov!r}"
                )
            return
    raise AssertionError(f"No message with subject {subject!r} found in {folder}")


@then('the IMAP message in folder "{folder}" with header "In-Reply-To" equal to "{irt}" has decoded subject "{expected_subj}"')
def step_imap_message_with_irt_has_decoded_subject(
    context: Context, folder: str, irt: str, expected_subj: str
) -> None:
    from email.header import decode_header, make_header

    _, instance, user, fld = _parse_folder_spec_local(context, folder)
    uids = context.imap.folder_uids(instance, user, fld)
    for u in uids:
        msg = context.imap.fetch_message(instance, user, fld, u)
        if (msg.get("In-Reply-To") or "").strip() == irt.strip():
            raw_subj = msg.get("Subject") or ""
            decoded = str(make_header(decode_header(raw_subj)))
            if decoded != expected_subj:
                raise AssertionError(f"Decoded subject expected {expected_subj!r}, got {decoded!r}")
            return
    raise AssertionError(f"No message with In-Reply-To={irt!r} found in {folder}")


@then('a direct IMAP FETCH of the message with subject "{subject}" in "{box}" shows decoded plain text containing "{text}"')
def step_direct_imap_fetch_text_containing(
    context: Context, subject: str, box: str, text: str
) -> None:
    _, instance, user, fld = _parse_folder_spec_local(context, box)
    uids = context.imap.folder_uids(instance, user, fld)
    for u in uids:
        msg = context.imap.fetch_message(instance, user, fld, u)
        if _matches_subject(msg, subject):
            body = _get_body_text_local(msg)
            if text not in body:
                raise AssertionError(f"Expected text {text!r} in body, got: {body!r}")
            return
    raise AssertionError(f"No message with subject {subject!r} found in {box}")


@then('a direct IMAP FETCH of the message with subject "{subject}" in "{box}" shows decoded plain text NOT containing "{text}"')
def step_direct_imap_fetch_text_not_containing(
    context: Context, subject: str, box: str, text: str
) -> None:
    _, instance, user, fld = _parse_folder_spec_local(context, box)
    uids = context.imap.folder_uids(instance, user, fld)
    for u in uids:
        msg = context.imap.fetch_message(instance, user, fld, u)
        if _matches_subject(msg, subject):
            body = _get_body_text_local(msg)
            if text in body:
                raise AssertionError(f"Forbidden text {text!r} found in body: {body!r}")
            return
    raise AssertionError(f"No message with subject {subject!r} found in {box}")


def _registry_path(context: Context) -> Path:
    bdd_root = getattr(context, "bdd_root", None)
    if bdd_root is not None:
        return Path(bdd_root).parent / "docs" / "contract" / "registry.yaml"
    return Path(__file__).resolve().parents[3] / "docs" / "contract" / "registry.yaml"


@then("docs/contract/registry.yaml declares caller_allow_reasons equal to:")
def step_registry_declares_caller_allow_reasons(context: Context) -> None:
    import yaml

    expected = [row[0] for row in context.table]
    registry_path = _registry_path(context)
    data = yaml.safe_load(registry_path.read_text(encoding="utf-8"))
    actual = data.get("caller_allow_reasons", [])
    if actual != expected:
        raise AssertionError(f"Expected caller_allow_reasons {expected}, got {actual}")


@then("docs/contract/registry.yaml declares caller_deny_reasons equal to:")
def step_registry_declares_caller_deny_reasons(context: Context) -> None:
    import yaml

    expected = [row[0] for row in context.table]
    registry_path = _registry_path(context)
    data = yaml.safe_load(registry_path.read_text(encoding="utf-8"))
    actual = data.get("caller_deny_reasons", [])
    if actual != expected:
        raise AssertionError(f"Expected caller_deny_reasons {expected}, got {actual}")


@then("docs/contract/registry.yaml declares caller_error_types equal to:")
def step_registry_declares_caller_error_types(context: Context) -> None:
    import yaml

    expected = [row[0] for row in context.table]
    registry_path = _registry_path(context)
    data = yaml.safe_load(registry_path.read_text(encoding="utf-8"))
    actual = data.get("caller_error_types", [])
    if actual != expected:
        raise AssertionError(f"Expected caller_error_types {expected}, got {actual}")


@then(
    'none of the captured responses or audit entries in this scenario carry any of {forbidden_list_raw}'
)
def step_none_carry_forbidden_values(context: Context, forbidden_list_raw: str) -> None:
    import ast

    forbidden = ast.literal_eval(forbidden_list_raw)
    history = getattr(context, "response_history", [])
    if getattr(context, "last_response", None):
        history = history + [context.last_response]
    for resp in history:
        resp_str = json.dumps(resp)
        for f in forbidden:
            if f in resp_str:
                raise AssertionError(f"Forbidden term {f!r} found in response: {resp_str}")
    if getattr(context, "audit_dir", None) and context.audit_dir.exists():
        for path in context.audit_dir.glob("*.jsonl"):
            content = path.read_text(encoding="utf-8", errors="replace")
            for f in forbidden:
                if f in content:
                    raise AssertionError(f"Forbidden term {f!r} found in audit log {path}: {content}")


@then(
    'file "{filename}" contains at least {count:d} records and every record\'s reason and error_type belong to docs/contract/registry.yaml'
)
def step_file_contains_valid_registry_records(context: Context, filename: str, count: int) -> None:
    import yaml

    from support.audit_reader import AuditReader

    registry_path = _registry_path(context)
    data = yaml.safe_load(registry_path.read_text(encoding="utf-8"))
    allowed_reasons = set(data.get("caller_allow_reasons", []) + data.get("caller_deny_reasons", []))
    allowed_errors = set(data.get("caller_error_types", []))

    reader = AuditReader(context.audit_dir)
    records = reader.read_file(filename)
    if len(records) < count:
        raise AssertionError(f"Expected at least {count} records in {filename}, found {len(records)}")
    for r in records:
        rec = r.record
        reason = rec.get("reason")
        if reason and reason not in allowed_reasons:
            raise AssertionError(f"Unknown reason {reason!r} in audit record: {rec}")
        error_type = rec.get("error_type") or (rec.get("error") or {}).get("type")
        if error_type and error_type not in allowed_errors:
            raise AssertionError(f"Unknown error_type {error_type!r} in audit record: {rec}")
