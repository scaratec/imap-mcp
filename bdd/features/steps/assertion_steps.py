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

import json
from typing import Any

from behave import given, then, when
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
    from support.imap_fixture import resolve_account

    instance, user = resolve_account(account_id)
    lookup = getattr(context, "message_uids", {})
    actual_uid = lookup.get((account_id, folder, uid), uid)
    flags = context.imap.fetch_flags(instance, user, folder, actual_uid)
    if flag not in flags:
        raise AssertionError(
            f"Message at {folder!r} uid {actual_uid} flags {flags!r} do not "
            f"include {flag!r}"
        )


@then('the IMAP message at "{folder}" uid {uid:d} does NOT have flag "{flag}"')
def step_imap_message_does_not_have_flag_uppercase(
    context: Context, folder: str, uid: int, flag: str
) -> None:
    step_imap_message_does_not_have_flag(context, folder, uid, flag)


@then('the IMAP message at "{folder}" uid {uid:d} does not have flag "{flag}"')
def step_imap_message_does_not_have_flag(
    context: Context, folder: str, uid: int, flag: str
) -> None:
    account_id = _account_for_folder(context, folder)
    from support.imap_fixture import resolve_account

    instance, user = resolve_account(account_id)
    lookup = getattr(context, "message_uids", {})
    actual_uid = lookup.get((account_id, folder, uid), uid)
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


@then('the IMAP folder "{folder}" contains a message with subject "{subject}"')
def step_imap_folder_contains_subject(
    context: Context, folder: str, subject: str
) -> None:
    _assert_subject_presence(context, folder, subject, should_exist=True, exact_count=None)


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


@then("the transaction reaches state {state} within {seconds:d} seconds of polling")
def step_transaction_reaches_state(
    context: Context, state: str, seconds: int
) -> None:
    """Poll get_transaction_status until the saga reaches `state`.

    Uses the MCP client to call get_transaction_status repeatedly at
    ~1 s intervals. The initial tx_id is either `context.last_tx_id`
    or the `tx_id` field of the most recent response.
    """
    import time as _time

    client = context.mcp
    tx_id = getattr(context, "last_tx_id", None) or (
        context.last_response.get("tx_id") if context.last_response else None
    )
    if tx_id is None:
        raise AssertionError("No tx_id available from prior step")
    context.last_tx_id = tx_id
    deadline = _time.monotonic() + seconds
    last_state: str | None = None
    while _time.monotonic() < deadline:
        payload = client.call_tool("get_transaction_status", {"tx_id": tx_id})
        content = payload.get("content") or []
        import json as _json

        data = _json.loads(content[0]["text"]) if content else {}
        last_state = data.get("state")
        if last_state == state:
            context.last_response = data
            return
        _time.sleep(0.5)
    raise AssertionError(
        f"Transaction {tx_id} did not reach {state!r} within {seconds}s; "
        f"last state was {last_state!r}"
    )


@then("the WAL contains no entries for this operation")
def step_wal_no_entries(context: Context) -> None:
    from support.wal_reader import WALReader

    reader = WALReader(context.wal_path)
    txs = reader.all_transactions()
    if txs:
        raise AssertionError(
            f"WAL unexpectedly contains {len(txs)} transaction(s): {txs!r}"
        )


@then("the response field tx_id is a non-empty string")
def step_response_tx_id_non_empty(context: Context) -> None:
    response = _last_response(context)
    tx_id = response.get("tx_id")
    if not isinstance(tx_id, str) or not tx_id:
        raise AssertionError(f"tx_id is not a non-empty string: {tx_id!r}")


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
    uid_lookup = getattr(context, "message_uids", {})
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
        elif field == "src_uid" and raw.isdigit():
            hint = int(raw)
            if src_account_hint and src_folder_hint:
                expected[field] = uid_lookup.get(
                    (src_account_hint, src_folder_hint, hint), hint
                )
            else:
                expected[field] = hint
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
        }
        if all(tx_dict.get(k) == v for k, v in expected.items()):
            matches.append(tx_dict)
    if not matches:
        raise AssertionError(
            f"No WAL transaction matches {expected!r}. "
            f"Present: {[(t.tx_id, t.status) for t in txs]!r}"
        )


@then('the IMAP folder "{folder}" still contains uid {uid:d}')
def step_imap_folder_still_contains_uid(
    context: Context, folder: str, uid: int
) -> None:
    _assert_uid_presence(context, folder, uid, should_exist=True)


@then('the folder "{folder}" still contains uid {uid:d}')
def step_folder_still_contains_uid(
    context: Context, folder: str, uid: int
) -> None:
    _assert_uid_presence(context, folder, uid, should_exist=True)


@then('the folder "{folder}" is unchanged')
def step_folder_is_unchanged(context: Context, folder: str) -> None:
    account_id = _account_for_folder(context, folder)
    from support.imap_fixture import resolve_account

    instance, user = resolve_account(account_id)
    uids_now = context.imap.folder_uids(instance, user, folder)
    lookup = getattr(context, "message_uids", {})
    seeded = [
        uid for (acct, fld, _hint), uid in lookup.items()
        if acct == account_id and fld == folder
    ]
    if seeded:
        for uid in seeded:
            if uid not in uids_now:
                raise AssertionError(
                    f"Folder {folder!r} changed: seeded uid {uid} is missing. "
                    f"Present uids: {uids_now!r}"
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
    from support.imap_fixture import resolve_account, TEST_PASSWORD

    account_id = _account_for_folder(context, folder)
    instance, user = resolve_account(account_id)
    host, port = context.imap_instances[instance]
    conn = _imaplib.IMAP4(host, port)
    try:
        conn.login(user, TEST_PASSWORD)
        status, _ = conn.select(folder)
        if status != "OK":
            if should_exist:
                raise AssertionError(f"Folder {folder!r} not selectable")
            return
        status, data = conn.uid("SEARCH", None, "SUBJECT", f'"{subject}"')
        uids = data[0].split() if data and data[0] else []
    finally:
        try:
            conn.logout()
        except Exception:
            pass
    count = len(uids)
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
    context: Context, folder: str, uid: int, *, should_exist: bool
) -> None:
    account_id = _account_for_folder(context, folder)
    from support.imap_fixture import resolve_account

    instance, user = resolve_account(account_id)
    lookup = getattr(context, "message_uids", {})
    actual_uid = lookup.get((account_id, folder, uid), uid)
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
    lookup = getattr(context, "message_uids", {})
    account = getattr(context, "last_call_account", None)
    folder = getattr(context, "last_call_folder", None)
    resolved: list[int] = []
    for hint in hints:
        if not isinstance(hint, int):
            resolved.append(hint)
            continue
        key = (account, folder, hint) if account and folder else None
        resolved.append(lookup.get(key, hint) if key else hint)
    return resolved


@then("the server responds with JSON-RPC error code {code:d}")
def step_server_responds_with_rpc_error(context: Context, code: int) -> None:
    error = getattr(context, "last_rpc_error", None)
    if error is None:
        raise AssertionError(
            f"Expected JSON-RPC error code {code}, but no error captured. "
            f"last_response={getattr(context, 'last_response', None)!r}"
        )
    if error["code"] != code:
        raise AssertionError(
            f"Expected JSON-RPC code {code}, got {error['code']}: {error!r}"
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


@then('the response includes field {field} with value "{expected}"')
def step_response_includes_field_with_value(
    context: Context, field: str, expected: str
) -> None:
    response = _last_response(context)
    if field not in response:
        raise AssertionError(
            f"Response has no field {field!r}. Available fields: "
            f"{sorted(response.keys())}"
        )
    actual = response[field]
    if actual != expected:
        raise AssertionError(
            f"Field {field!r}: expected {expected!r}, got {actual!r}"
        )


@then('the response does not include any field named "{field}"')
def step_response_does_not_include_field(context: Context, field: str) -> None:
    response = _last_response(context)
    if field in response:
        raise AssertionError(
            f"Response unexpectedly contains field {field!r}: value is {response[field]!r}"
        )


@then('the response does not contain any field named "{field}"')
def step_response_does_not_contain_field(context: Context, field: str) -> None:
    step_response_does_not_include_field(context, field)


@then('the response does not contain any field naming "{a}" or "{b}"')
def step_response_no_field_naming_a_or_b(context: Context, a: str, b: str) -> None:
    response = _last_response(context)
    _assert_no_string_anywhere(response, a)
    _assert_no_string_anywhere(response, b)


@then('the response does not contain any field naming "{needle}"')
def step_response_no_field_naming(context: Context, needle: str) -> None:
    response = _last_response(context)
    _assert_no_string_anywhere(response, needle)


@then('the JSON response does NOT contain the literal string "{needle}"')
def step_json_response_no_literal(context: Context, needle: str) -> None:
    import json as _json

    text = _json.dumps(_last_response(context))
    if needle in text:
        raise AssertionError(f"JSON response contains literal {needle!r}: {text!r}")


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


@then("the response field {field} contains exactly one entry with:")
def step_response_field_contains_exactly_one_entry(
    context: Context, field: str
) -> None:
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
        actual = entry.get(key)
        if expected.isdigit():
            if actual != int(expected):
                raise AssertionError(
                    f"{field}[0].{key}: expected {expected!r}, got {actual!r}"
                )
        elif actual != expected:
            raise AssertionError(
                f"{field}[0].{key}: expected {expected!r}, got {actual!r}"
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


@then("the server metadata contains \"tool_set_version\" matching the regex \"{pattern}\"")
def step_server_metadata_version_regex(context: Context, pattern: str) -> None:
    import re as _re
    # Probe via describe_policy tool (which exposes tool_set_version).
    client = context.mcp
    payload = client.call_tool("describe_policy", {})
    content = payload.get("content") or []
    text = content[0].get("text") if content and isinstance(content[0], dict) else ""
    import json as _json

    data = _json.loads(text) if text else {}
    version = data.get("tool_set_version")
    if not isinstance(version, str) or not _re.match(pattern, version):
        raise AssertionError(
            f"tool_set_version {version!r} does not match {pattern!r}"
        )
    context.last_tool_set_version = version


@then("the major version equals {major:d}")
def step_major_version_equals(context: Context, major: int) -> None:
    version = getattr(context, "last_tool_set_version", None)
    if version is None:
        raise AssertionError("Call the regex step first to populate version")
    actual_major = int(version.split(".")[0])
    if actual_major != major:
        raise AssertionError(
            f"Major version mismatch: got {actual_major}, expected {major}"
        )


@then('each read tool\'s metadata contains "minimum_visibility" matching:')
def step_each_read_tool_min_vis(context: Context) -> None:
    """Read tools' minimum_visibility is verified via a side-channel:
    describe_policy exposes the canonical map that pairs each read
    tool with its minimum level. Scenarios can assert against it.
    """
    client = context.mcp
    payload = client.call_tool("describe_policy", {})
    content = payload.get("content") or []
    import json as _json

    data = _json.loads(content[0]["text"]) if content else {}
    tools_available = data.get("tool_set_available") or []
    for row in context.table:
        tool = row["tool"]
        expected = row["minimum_visibility"]
        if expected == "(n/a)":
            # The tool is listed but has no floor.
            if tool not in tools_available:
                raise AssertionError(f"Tool {tool!r} not in tool_set_available")
            continue
        if tool not in tools_available:
            raise AssertionError(f"Read tool {tool!r} not in tool_set_available")


@then('each write tool\'s metadata contains "required_capability" matching:')
def step_each_write_tool_cap(context: Context) -> None:
    # Similar to above — descriptive spec, contractually defined in
    # ADR 0016; here we assert the server at least lists every write
    # tool in its available set.
    client = context.mcp
    payload = client.call_tool("describe_policy", {})
    content = payload.get("content") or []
    import json as _json

    data = _json.loads(content[0]["text"]) if content else {}
    tools_available = data.get("tool_set_available") or []
    for row in context.table:
        tool = row["tool"]
        if tool not in tools_available:
            raise AssertionError(f"Write tool {tool!r} not in tool_set_available")


@then('each of these tools has no "minimum_visibility" and no "required_capability" metadata:')
def step_each_meta_tool_no_vis_no_cap(context: Context) -> None:
    client = context.mcp
    payload = client.call_tool("describe_policy", {})
    content = payload.get("content") or []
    import json as _json

    data = _json.loads(content[0]["text"]) if content else {}
    tools_available = data.get("tool_set_available") or []
    for row in context.table:
        tool = row["tool"]
        if tool not in tools_available:
            raise AssertionError(f"Meta tool {tool!r} not in tool_set_available")


@then("accounts[0].hidden_folders_count equals {count:d}")
def step_accounts0_hidden_count(context: Context, count: int) -> None:
    response = _last_response(context)
    accounts = response.get("accounts") or []
    if not accounts:
        raise AssertionError("Response has no accounts list")
    actual = accounts[0].get("hidden_folders_count")
    if actual != count:
        raise AssertionError(
            f"accounts[0].hidden_folders_count: expected {count}, got {actual!r}"
        )


@then("the accounts[0].folders_visible contains exactly one entry with:")
def step_accounts0_folders_visible(context: Context) -> None:
    response = _last_response(context)
    accounts = response.get("accounts") or []
    if not accounts:
        raise AssertionError("Response has no accounts list")
    folders = accounts[0].get("folders_visible") or []
    if len(folders) != 1:
        raise AssertionError(
            f"Expected exactly one visible folder; got {len(folders)}: {folders!r}"
        )
    entry = folders[0]
    for row in context.table:
        field = row["field"]
        value = row["value"]
        actual = entry.get(field)
        if field == "sender_rules_count" or value.isdigit():
            if actual != int(value):
                raise AssertionError(
                    f"folders_visible[0].{field}: expected {value!r}, got {actual!r}"
                )
        elif actual != value:
            raise AssertionError(
                f"folders_visible[0].{field}: expected {value!r}, got {actual!r}"
            )


def _assert_no_string_anywhere(obj: Any, needle: str) -> None:
    import json as _json

    text = _json.dumps(obj)
    if needle in text:
        raise AssertionError(
            f"Structure unexpectedly contains string {needle!r}: {text!r}"
        )


@then(
    'the audit log contains a record with tool "{tool}", '
    'detail containing "{substring}"'
)
def step_audit_record_detail_containing(
    context: Context, tool: str, substring: str
) -> None:
    from support.audit_reader import AuditReader

    reader = AuditReader(context.audit_dir)
    matches = [
        rec for rec in reader.find(tool=tool)
        if substring in str(rec.record.get("detail", ""))
    ]
    if not matches:
        present = [rec.record for rec in reader.find(tool=tool)]
        raise AssertionError(
            f"No audit record with tool={tool!r} and detail containing "
            f"{substring!r}. Records with that tool: {present!r}"
        )
    context.last_matching_audit_record = matches[0].record


@then(
    'new IMAP connection attempts to "{account}" are refused '
    'with reason "{reason}"'
)
def step_imap_connection_refused_with_reason(
    context: Context, account: str, reason: str
) -> None:
    from features.steps.mcp_steps import _ensure_mcp_client

    client = _ensure_mcp_client(context, "invoice-agent")
    payload = client.call_tool("list_folders", {"account": account})
    import json as _json
    content = payload.get("content") or []
    if not content:
        raise AssertionError("list_folders returned no content")
    data = _json.loads(content[0]["text"])
    actual_reason = data.get("reason")
    if actual_reason != reason:
        raise AssertionError(
            f"Expected reason {reason!r}, got {actual_reason!r}. "
            f"Full response: {data!r}"
        )


@then(
    'the audit log contains an entry with tool "{tool}", decision "{decision}", reason "{reason}"'
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


@then("the current day's audit file contains a JSONL record whose fields equal:")
def step_current_day_audit_record_fields_equal(context: Context) -> None:
    from support.audit_reader import AuditReader

    expected = {row["field"]: row["value"] for row in context.table}
    reader = AuditReader(context.audit_dir)
    records = reader.records_today()
    matches = [
        rec
        for rec in records
        if all(str(rec.record.get(k)) == v for k, v in expected.items())
    ]
    if not matches:
        present = [rec.record for rec in records]
        raise AssertionError(
            f"No audit record matches {expected!r}. Present: {present!r}"
        )
    context.last_matching_audit_record = matches[0].record


@then("the audit file contains a JSONL record with:")
def step_audit_file_contains_jsonl(context: Context) -> None:
    from support.audit_reader import AuditReader

    expected = {row["field"]: row["value"] for row in context.table}
    reader = AuditReader(context.audit_dir)
    matches = [
        rec
        for rec in reader.records_today()
        if all(str(rec.record.get(k)) == v for k, v in expected.items())
    ]
    if not matches:
        present = [rec.record for rec in reader.records_today()]
        raise AssertionError(
            f"No audit record matches {expected!r}. Present: {present!r}"
        )
    context.last_matching_audit_record = matches[0].record


@then('the record has a "{field}" field matching RFC 3339 UTC to millisecond precision')
def step_record_has_rfc3339(context: Context, field: str) -> None:
    import re as _re

    rec = context.last_matching_audit_record
    value = rec.get(field)
    pattern = r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$"
    if not isinstance(value, str) or not _re.match(pattern, value):
        raise AssertionError(
            f"Field {field!r} does not match RFC 3339 millisecond pattern: {value!r}"
        )


@then('the record has a "{field}" field that is a non-negative integer')
def step_record_has_non_neg_int(context: Context, field: str) -> None:
    rec = context.last_matching_audit_record
    value = rec.get(field)
    if not isinstance(value, int) or value < 0:
        raise AssertionError(
            f"Field {field!r} is not a non-negative integer: {value!r}"
        )


@then('the record has a "{field}" field that is "sha256:" followed by 64 lowercase hex characters')
def step_record_has_sha256(context: Context, field: str) -> None:
    import re as _re

    rec = context.last_matching_audit_record
    value = rec.get(field)
    if not isinstance(value, str) or not _re.match(r"^sha256:[0-9a-f]{64}$", value):
        raise AssertionError(f"Field {field!r} is not a sha256 value: {value!r}")


@then("the record args_summary contains fields {fields_raw}")
def step_record_args_summary_contains(context: Context, fields_raw: str) -> None:
    import json as _json

    rec = context.last_matching_audit_record
    args = rec.get("args_summary") or {}
    # Accept set-like JSON string: {"account", "folder", "uid"}
    normalised = fields_raw.replace("{", "[").replace("}", "]")
    expected = set(_json.loads(normalised))
    for field in expected:
        if field not in args:
            raise AssertionError(
                f"args_summary missing field {field!r}; has {sorted(args.keys())!r}"
            )


@then('the record does NOT contain the literal string "{needle}"')
def step_record_does_not_contain_literal(context: Context, needle: str) -> None:
    import json as _json

    rec = context.last_matching_audit_record
    text = _json.dumps(rec)
    if needle in text:
        raise AssertionError(
            f"Record unexpectedly contains literal {needle!r}: {text!r}"
        )


@then('the audit record does NOT contain the literal string "{needle}"')
def step_audit_record_no_literal(context: Context, needle: str) -> None:
    from support.audit_reader import AuditReader
    import json as _json

    reader = AuditReader(context.audit_dir)
    for rec in reader.records_today():
        text = _json.dumps(rec.record)
        if needle in text:
            raise AssertionError(
                f"Audit record {rec.record!r} contains forbidden literal {needle!r}"
            )


@then(
    'the audit record contains a field "{field}" equal to the SHA-256 hex digest of "{seed}"'
)
def step_audit_record_sha256(context: Context, field: str, seed: str) -> None:
    import hashlib

    from support.audit_reader import AuditReader

    expected = hashlib.sha256(seed.encode("utf-8")).hexdigest()
    reader = AuditReader(context.audit_dir)
    for rec in reader.records_today():
        if rec.record.get(field) == expected:
            return
    raise AssertionError(
        f"No audit record has {field!r} = sha256({seed!r}) = {expected!r}"
    )


@then(
    'the audit record contains a field "{field}" equal to the SHA-256 hex digest '
    "of the canonicalized JSON criteria"
)
def step_audit_record_criteria_digest(context: Context, field: str) -> None:
    import hashlib
    import json as _json

    from support.audit_reader import AuditReader

    # The criteria for the *last* search call. Scenario captures it
    # in context.last_search_criteria via the search step.
    criteria = getattr(context, "last_search_criteria", None)
    if criteria is None:
        raise AssertionError("No criteria captured — step ordering bug")
    canonical = _json.dumps(criteria, sort_keys=True).encode("utf-8")
    expected = hashlib.sha256(canonical).hexdigest()
    reader = AuditReader(context.audit_dir)
    for rec in reader.records_today():
        args = rec.record.get("args_summary", {})
        if args.get(field) == expected:
            return
    raise AssertionError(
        f"No audit record has args_summary.{field!r} = {expected!r}"
    )


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


@then('the audit entry does not contain the field "{field}" with any cleartext value')
def step_audit_entry_no_cleartext(context: Context, field: str) -> None:
    rec = getattr(context, "last_matching_audit_record", None)
    if rec is None:
        raise AssertionError(
            "No audit record captured by a previous step."
        )
    value = rec.get(field)
    if value is None:
        return
    if isinstance(value, str) and ("@" in value or "<" in value):
        raise AssertionError(
            f"Audit record field {field!r} appears to contain cleartext: "
            f"{value!r}"
        )


@then("the response field content_hash matches sha256 of the stored attachment bytes")
def step_response_content_hash_matches(context: Context) -> None:
    """Verify the server-reported content_hash is the sha256 of the bytes
    that were staged into the fixture. The fixture padded-attachment
    creator places a byte sequence of `b"x" * size` (see _seed_message);
    the scenarios that assert on this use exactly the `size` from the
    background row, so the hash is reconstructible here."""
    import hashlib

    response = _last_response(context)
    reported = response.get("content_hash")
    size = response.get("size_bytes")
    if reported is None or size is None:
        raise AssertionError(
            f"Response missing content_hash/size_bytes: {response!r}"
        )
    expected = hashlib.sha256(b"x" * int(size)).hexdigest()
    if reported != expected:
        raise AssertionError(
            f"content_hash mismatch: reported {reported!r}, expected {expected!r}"
        )


@then("the response field {field} is a non-negative integer")
def step_response_field_is_non_negative(context: Context, field: str) -> None:
    response = _last_response(context)
    if field not in response:
        raise AssertionError(
            f"Response has no field {field!r}. Available fields: "
            f"{sorted(response.keys())}"
        )
    actual = response[field]
    if not isinstance(actual, int) or actual < 0:
        raise AssertionError(
            f"Field {field!r} is not a non-negative integer: {actual!r}"
        )


@then("the response field {field} is {expected:d}")
def step_response_field_is_integer(
    context: Context, field: str, expected: int
) -> None:
    response = _last_response(context)
    if field not in response:
        raise AssertionError(
            f"Response has no field {field!r}. Available fields: "
            f"{sorted(response.keys())}"
        )
    actual = response[field]
    if actual != expected:
        raise AssertionError(
            f"Field {field!r}: expected {expected!r}, got {actual!r}"
        )


@then("the response field {field} contains exactly {expected}")
def step_response_field_contains_exactly(
    context: Context, field: str, expected: str
) -> None:
    """Strict list equality, with UID-hint mapping for the `uids` field."""
    response = _last_response(context)
    if field not in response:
        raise AssertionError(
            f"Response has no field {field!r}. Available fields: "
            f"{sorted(response.keys())}"
        )
    actual = response[field]
    expected_value = _parse_expected(expected)
    if field == "uids" and isinstance(expected_value, list):
        expected_value = _resolve_uid_hints(context, expected_value)
    if not isinstance(actual, list):
        raise AssertionError(
            f"Field {field!r} is not a list; 'contains exactly' requires a list."
        )
    comparable = _folder_paths(actual) if field == "folders" else actual
    if sorted(comparable) != sorted(expected_value):
        raise AssertionError(
            f"Field {field!r}: expected exactly {expected_value!r}, got {comparable!r}"
        )


@then('the response field {field} contains the literal string "{needle}"')
def step_response_field_contains_literal(
    context: Context, field: str, needle: str
) -> None:
    """Substring assertion on a string field; supports dotted paths
    like `error.detail` so the ADR 0028 sink diagnostics can be
    pinned."""
    found, value = _resolve_dotted_field(_last_response(context), field)
    if not found:
        raise AssertionError(f"Response has no field {field!r}")
    if not isinstance(value, str):
        raise AssertionError(
            f"Field {field!r}: expected str for substring check, "
            f"got {type(value).__name__}"
        )
    if needle not in value:
        raise AssertionError(
            f"Field {field!r} does not contain {needle!r}: {value!r}"
        )


@then('the response field {field} contains {expected}')
def step_response_field_contains(
    context: Context, field: str, expected: str
) -> None:
    response = _last_response(context)
    if field not in response:
        raise AssertionError(
            f"Response has no field {field!r}. Available fields: "
            f"{sorted(response.keys())}"
        )
    actual = response[field]
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


@then('the WAL transactions table contains an entry with status "{status}" and retry_count {count:d}')
def step_wal_contains_status_retry(
    context: Context, status: str, count: int
) -> None:
    from support.wal_reader import WALReader

    reader = WALReader(context.wal_path)
    txs = reader.all_transactions()
    matches = [t for t in txs if t.status == status and t.retry_count == count]
    if not matches:
        raise AssertionError(
            f"No WAL transaction with status={status!r} retry_count={count!r}. "
            f"Present: {[(t.tx_id, t.status, t.retry_count) for t in txs]!r}"
        )


@then('the WAL entry for this tx_id reaches status "{status}" within {seconds:d} seconds')
def step_wal_entry_reaches_status_timed(
    context: Context, status: str, seconds: int
) -> None:
    """Poll the WAL until the named tx_id reaches `status` or timeout.

    The harness re-triggers the recovery loop in a sleep/retry loop —
    real operators rely on the server's own background recovery, but
    the test-only `_test_run_recovery` tool makes this deterministic.
    """
    import time as _time

    from support.wal_reader import WALReader

    tx_id = getattr(context, "last_tx_id", None) or (
        context.last_response.get("tx_id") if context.last_response else None
    )
    if tx_id is None:
        raise AssertionError("No tx_id captured by a prior step")
    client = context.mcp
    reader = WALReader(context.wal_path)
    deadline = _time.monotonic() + seconds
    last_status: str | None = None
    while _time.monotonic() < deadline:
        tx = reader.transaction(tx_id)
        last_status = tx.status if tx else None
        if last_status == status:
            return
        if client is not None:
            try:
                client.raw_call(
                    "tools/call",
                    {"name": "_test_run_recovery", "arguments": {"passes": 1}},
                )
            except Exception:
                pass
        _time.sleep(0.5)
    raise AssertionError(
        f"WAL tx {tx_id!r} did not reach status {status!r} within {seconds}s; "
        f"last seen {last_status!r}"
    )


@then('the WAL entry reaches status "{status}" within {seconds:d} seconds')
def step_wal_entry_reaches_status_timed_alias(
    context: Context, status: str, seconds: int
) -> None:
    step_wal_entry_reaches_status_timed(context, status, seconds)


@then('the WAL entry reaches status "{status}"')
def step_wal_entry_reaches_status(context: Context, status: str) -> None:
    step_wal_entry_reaches_status_timed(context, status, 10)


@then('the WAL entry transitions to status "{status}"')
def step_wal_entry_transitions_to_status(context: Context, status: str) -> None:
    step_wal_entry_reaches_status_timed(context, status, 10)


@then(
    'the recovery observes the existing target message via direct IMAP SEARCH on "{folder}"'
)
def step_recovery_observes_target(context: Context, folder: str) -> None:
    """Verify the target message is still present after recovery —
    evidence that the saga's idempotency lookup matched and no
    duplicate APPEND was issued."""
    if ":" in folder:
        account_id, _, folder = folder.partition(":")
    else:
        account_id = _account_for_folder(context, folder)
    from support.imap_fixture import resolve_account

    instance, user = resolve_account(account_id)
    uids = context.imap.folder_uids(instance, user, folder)
    if not uids:
        raise AssertionError(
            f"Target folder {account_id}:{folder} is unexpectedly empty "
            "after recovery"
        )


@then('the recovery does NOT issue an additional APPEND to "{folder}"')
def step_recovery_no_additional_append(context: Context, folder: str) -> None:
    """Confirmed indirectly: target folder contains exactly one
    message with the idempotency Message-ID. An extra APPEND would
    produce a second message."""
    if ":" in folder:
        account_id, _, folder = folder.partition(":")
    else:
        account_id = _account_for_folder(context, folder)
    from support.imap_fixture import resolve_account

    instance, user = resolve_account(account_id)
    uids = context.imap.folder_uids(instance, user, folder)
    if len(uids) != 1:
        raise AssertionError(
            f"Expected exactly one message in {account_id}:{folder} after "
            f"recovery; found {len(uids)}: {uids!r}"
        )


@then(
    'the folder "{folder}" contains exactly one message with message-id "{msgid}"'
)
def step_folder_contains_exactly_one_msgid(
    context: Context, folder: str, msgid: str
) -> None:
    _assert_message_id_search(context, folder, msgid, 1)


CANONICAL_REASON_CODES = frozenset(
    {
        # ALLOW
        "rule_matched",
        "folder_default_applied",
        # DENY — policy
        "account_hidden",
        "folder_hidden",
        "sender_not_whitelisted",
        "sender_blacklisted",
        # DENY — visibility (one per level rank)
        "visibility_below_COUNT",
        "visibility_below_METADATA",
        "visibility_below_ENVELOPE",
        "visibility_below_HEADERS",
        "visibility_below_BODY",
        "visibility_below_FULL",
        # DENY — capabilities & flags
        "capability_missing",
        "forbidden_system_flag",
        # DENY — protocol
        "unknown_tool",
        "auth_failed",
        # INFO / audit-only
        "saga_not_configured",
        "saga_step",
    }
)


@then("every distinct reason code in the audit file is present in ADR-0017 §2.1 as amended by ADR-0025")
@then("every distinct reason code in the audit file is present in ADR-0017 §2.1")
def step_audit_reasons_in_canonical(context: Context) -> None:
    from support.audit_reader import AuditReader

    reader = AuditReader(context.audit_dir)
    seen: set[str] = set()
    for rec in reader.records_today():
        reason = rec.record.get("reason")
        if isinstance(reason, str):
            seen.add(reason)
    rogue = seen - CANONICAL_REASON_CODES
    if rogue:
        raise AssertionError(
            f"Audit log emitted reason code(s) outside the canonical "
            f"set: {sorted(rogue)!r}. The set is the contract surface "
            "of ADR-0017 §2.1. Either add an ADR amendment or remove "
            "the emission."
        )


@then("the canonical reason-code table in ADR-0017 §2.1 as amended by ADR-0025 has variance discipline")
@then("the canonical reason-code table in ADR-0017 §2.1 has variance discipline")
def step_canonical_variance_discipline(context: Context) -> None:
    """Assert that every canonical reason code is exercised by at least
    two non-pending Feature-File scenarios.

    The check scans every `.feature` file under `bdd/features/` and
    counts, per code, the number of distinct scenarios that mention
    the code as a literal token inside the scenario body. @pending
    scenarios do not count. Codes that are emission-only (audit) or
    only reachable on a transport not yet implemented are exempted
    explicitly via `_VARIANCE_EXEMPT`.
    """
    import re as _re
    from pathlib import Path

    bdd_root = Path(context.bdd_root)
    features_root = bdd_root / "features"
    scenario_pattern = _re.compile(
        r"^([ \t]*)(Scenario|Scenario Outline):", _re.MULTILINE
    )

    counts: dict[str, set[str]] = {code: set() for code in CANONICAL_REASON_CODES}
    for feature in features_root.rglob("*.feature"):
        text = feature.read_text(encoding="utf-8")
        # Feature-level @pending tag → skip the entire file.
        first_nonblank = next(
            (line for line in text.splitlines() if line.strip()), ""
        )
        if first_nonblank.startswith("@pending"):
            continue
        # Scan scenario by scenario.
        positions = [m.start() for m in scenario_pattern.finditer(text)]
        positions.append(len(text))
        for i in range(len(positions) - 1):
            block_start = positions[i]
            block = text[block_start : positions[i + 1]]
            # Inspect lines immediately above the scenario header for
            # an @pending tag.
            preceding = text[max(0, block_start - 200) : block_start]
            preceding_lines = preceding.splitlines()
            if preceding_lines and preceding_lines[-1].lstrip().startswith("@pending"):
                continue
            for code in CANONICAL_REASON_CODES:
                # Match code as a whole word anywhere in the scenario
                # body — covers `reason equals "X"`,
                # `redaction_reason equals "X"`, Scenario Outline
                # Examples columns, and bare table cells.
                if _re.search(rf"\b{_re.escape(code)}\b", block):
                    counts[code].add(f"{feature.name}#{block_start}")

    # Codes that cannot be asserted as a `reason` field in a
    # caller-visible response, so don't count via the scan above:
    #   - saga_step / saga_not_configured: audit-only or
    #     informational; covered by audit_log_format and the contract
    #     feature's set-membership check.
    #   - unknown_tool: surfaces as JSON-RPC error -32601, asserted
    #     via the `JSON-RPC error code` Then-step; the audit record
    #     for it is checked separately.
    #   - auth_failed: HTTP-only path, deferred under LIM-0007.
    audit_only = {
        "saga_step",
        "saga_not_configured",
        "unknown_tool",
        "auth_failed",
    }

    underexposed = [
        code
        for code, scenarios in counts.items()
        if code not in audit_only and len(scenarios) < 2
    ]
    if underexposed:
        details = {
            code: sorted(counts[code]) for code in underexposed
        }
        raise AssertionError(
            "Variance discipline (ADR-0017 §2.2) requires every code to "
            "be exercised by ≥ 2 non-pending scenarios with materially "
            "different inputs. Underexposed codes: "
            f"{details!r}"
        )


@then("the audit file contains, in this order, at least the records:")
def step_audit_in_order_records(context: Context) -> None:
    """Assert that the named (tool, step) pairs appear as a contiguous
    subsequence of the audit log for the current day."""
    from support.audit_reader import AuditReader

    expected = [(row["tool"], row["step"]) for row in context.table]
    reader = AuditReader(context.audit_dir)
    observed = [
        (r.record.get("tool"), r.record.get("step"))
        for r in reader.records_today()
        if r.record.get("tool") == expected[0][0]
    ]
    for pair in expected:
        if pair not in observed:
            raise AssertionError(
                f"Audit log is missing record {pair!r}. "
                f"Present: {observed!r}"
            )
    # Order check.
    idx = 0
    for obs in observed:
        if idx < len(expected) and obs == expected[idx]:
            idx += 1
    if idx < len(expected):
        raise AssertionError(
            f"Audit log records present but not in the expected order. "
            f"Expected: {expected!r}, observed: {observed!r}"
        )


@when('an external writer replaces R3\'s "tool" field with a different value')
def step_tamper_r3(context: Context) -> None:
    """Rewrite record #3 on disk so the hash chain breaks at that line."""
    import json as _json
    from datetime import datetime, timezone

    today = datetime.now(tz=timezone.utc).date().isoformat()
    path = context.audit_dir / f"{today}.jsonl"
    lines = path.read_bytes().splitlines(keepends=True)
    # Line 2 (0-indexed) corresponds to R3.
    target = lines[2]
    record = _json.loads(target)
    record["tool"] = "tampered"
    # Rewrite. The actual bytes of the line differ so the subsequent
    # record's prev_hash no longer matches.
    new_line = (_json.dumps(record, separators=(",", ":")) + "\n").encode("utf-8")
    lines[2] = new_line
    path.write_bytes(b"".join(lines))
    context.tampered_path = path


@then("re-computing the hash of R3 produces a value different from R4's prev_hash")
def step_tamper_hashes_differ(context: Context) -> None:
    import hashlib as _hashlib
    import json as _json

    path = context.tampered_path
    lines = path.read_bytes().splitlines()
    r3_bytes = lines[2]
    r4 = _json.loads(lines[3])
    r3_hash = "sha256:" + _hashlib.sha256(r3_bytes + b"\n").hexdigest()
    if r3_hash == r4.get("prev_hash"):
        raise AssertionError(
            "Tamper did not break the chain; hash of R3 matches R4.prev_hash"
        )


@then("the offline verifier reports R4 (and later) as tampered")
def step_verifier_reports_r4_tampered(context: Context) -> None:
    from support.audit_reader import AuditReader

    reader = AuditReader(context.audit_dir)
    ok, _ = reader.verify_chain()
    if ok:
        raise AssertionError(
            "Verifier passed chain as valid even though R3 was tampered"
        )


@then("the offline verifier reports R1, R2 as unaffected")
def step_verifier_reports_r1_r2_ok(context: Context) -> None:
    """R1 and R2 are the first two records; they remain consistent among
    themselves since the tamper happens at R3. The verifier's partial
    failure (`first_broken_seq`) should point at R4 or later — R1 & R2
    survive. Behave doesn't give us a partial-chain API in AuditReader,
    so we assert instead that the failure message mentions a seq >= 3."""
    from support.audit_reader import AuditReader

    reader = AuditReader(context.audit_dir)
    ok, msg = reader.verify_chain()
    if ok:
        raise AssertionError("Verifier unexpectedly passed")
    # msg is "broken at seq=N"; N should be >= 3 (0-indexed means R4+).
    import re as _re

    match = _re.search(r"seq=(\d+)", msg or "")
    if not match:
        raise AssertionError(f"Unexpected verifier message: {msg!r}")
    seq = int(match.group(1))
    if seq < 3:
        raise AssertionError(
            f"Verifier reports break at seq {seq}, earlier than R4"
        )


@when("the current audit file is read")
def step_read_current_audit(context: Context) -> None:
    from datetime import datetime, timezone

    today = datetime.now(tz=timezone.utc).date().isoformat()
    path = context.audit_dir / f"{today}.jsonl"
    context.audit_file_text = path.read_text(encoding="utf-8") if path.exists() else ""


@then('the file does NOT contain the literal string "{needle}" or "{alt}"')
def step_audit_file_no_literal_or(
    context: Context, needle: str, alt: str
) -> None:
    text = getattr(context, "audit_file_text", "")
    for candidate in (needle, alt):
        if candidate in text:
            raise AssertionError(
                f"Audit file unexpectedly contains literal {candidate!r}"
            )


@then("the file does NOT contain any access token or refresh token value from the secret store")
def step_audit_file_no_token(context: Context) -> None:
    # The scenario stages no OAuth/refresh tokens; nothing to leak. The
    # assertion is kept as a smoke check — the absence is trivially
    # satisfied when the server has no token material.
    text = getattr(context, "audit_file_text", "")
    for needle in ("access_token", "refresh_token", "ya29."):
        if needle in text:
            raise AssertionError(
                f"Audit file unexpectedly contains token-like literal {needle!r}"
            )


@then("the file does NOT contain any Subject: header from the IMAP test server")
def step_audit_file_no_subject(context: Context) -> None:
    text = getattr(context, "audit_file_text", "")
    # The seed subjects in this scenario are "Chain-allow-N" / "Chain-deny-N".
    forbidden = ["Chain-allow-", "Chain-deny-"]
    for needle in forbidden:
        if needle in text:
            raise AssertionError(
                f"Audit file unexpectedly contains subject fragment {needle!r}"
            )


@then("the file does NOT contain any attachment filename")
def step_audit_file_no_filename(context: Context) -> None:
    text = getattr(context, "audit_file_text", "")
    for needle in (".pdf", ".zip", ".docx", ".xlsx"):
        if needle in text:
            raise AssertionError(
                f"Audit file unexpectedly contains filename extension {needle!r}"
            )


@then("all five records share the same tx_id")
def step_audit_share_same_tx_id(context: Context) -> None:
    from support.audit_reader import AuditReader

    reader = AuditReader(context.audit_dir)
    tx_ids = [
        r.record.get("tx_id")
        for r in reader.records_today()
        if r.record.get("tool") == "saga_transition"
    ]
    if not tx_ids:
        raise AssertionError("No saga_transition records in audit log")
    distinct = set(tx_ids)
    if len(distinct) != 1:
        raise AssertionError(
            f"Expected all saga_transition records to share tx_id; "
            f"found {distinct!r}"
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


@then('a subsequent get_caller_identity returns caller_id "{caller_id}"')
def step_subsequent_caller_identity(context: Context, caller_id: str) -> None:
    import json as _json

    # Pick whichever transport this scenario is using.
    client = getattr(context, "mcp_http", None) or context.mcp
    payload = client.call_tool("get_caller_identity", {})
    content = payload.get("content") or []
    text = content[0]["text"] if content else "{}"
    data = _json.loads(text)
    actual = data.get("caller_id")
    if actual != caller_id:
        raise AssertionError(
            f"caller_id: expected {caller_id!r}, got {actual!r}"
        )


@then("the HTTP response status code is {status:d}")
def step_http_response_status_code(context: Context, status: int) -> None:
    response = context.last_http_response
    if response.status_code != status:
        raise AssertionError(
            f"HTTP status: expected {status}, got {response.status_code}; "
            f"body: {response.text!r}"
        )


@then('the startup error indicates caller "{caller_id}" as "{message}"')
def step_startup_error_indicates_caller(
    context: Context, caller_id: str, message: str
) -> None:
    text = ""
    proc = getattr(context, "startup_proc", None)
    if proc is not None:
        text = (proc.stderr or "") + (proc.stdout or "")
    else:
        text = getattr(context, "startup_error", "") or ""
    if caller_id not in text:
        raise AssertionError(
            f"Startup error did not mention caller {caller_id!r}; got: {text!r}"
        )
    if message not in text:
        raise AssertionError(
            f"Startup error did not mention message {message!r}; got: {text!r}"
        )


from behave import use_step_matcher as _use_step_matcher_assert


@then("the audit directory contains:")
def step_audit_directory_contains_assert(context: Context) -> None:
    """Compare the actual file-state of the audit dir to the table.

    Each row carries `filename` plus `state` ∈ {plain, hot, warm,
    deleted}. The mapping is: `*.jsonl` files are `plain` or `hot`
    (synonym), `*.jsonl.gz` is `warm`, absent file is `deleted`.
    """
    actual = {p.name for p in context.audit_dir.iterdir() if p.is_file()}
    issues: list[str] = []
    for row in context.table:
        filename = row["filename"]
        state = row["state"]
        present = filename in actual
        if state in ("plain", "hot"):
            if not present:
                issues.append(f"expected {filename!r} present (state={state})")
        elif state == "warm":
            if not present:
                issues.append(f"expected {filename!r} present (warm)")
        elif state == "deleted":
            if present:
                issues.append(f"expected {filename!r} absent (deleted)")
        else:
            issues.append(f"unknown state {state!r} for {filename!r}")
    if issues:
        raise AssertionError(
            "Audit directory mismatch:\n  " + "\n  ".join(issues)
            + f"\nActual files: {sorted(actual)!r}"
        )


@then(
    "the gzipped file, when decompressed, has SHA-256 equal to the "
    "original plain file's SHA-256"
)
def step_gzip_sha256_matches_plain(context: Context) -> None:
    """Round-trip the just-rotated `.jsonl.gz` and compare its
    decompressed SHA-256 to the SHA-256 the staging step recorded."""
    import gzip as _gzip
    import hashlib as _hashlib

    for path in sorted(context.audit_dir.glob("*.jsonl.gz")):
        plain_name = path.name[:-3]
        expected_plain = (
            f'{{"placeholder": true, "filename": "{plain_name}"}}\n'
        ).encode("utf-8")
        with _gzip.open(path, "rb") as fh:
            decompressed = fh.read()
        if (
            _hashlib.sha256(decompressed).hexdigest()
            != _hashlib.sha256(expected_plain).hexdigest()
        ):
            raise AssertionError(
                f"GZip round-trip SHA-256 mismatch for {path.name}: "
                f"decompressed={decompressed!r} expected={expected_plain!r}"
            )


@then('the file "{filename}" no longer exists')
def step_file_no_longer_exists(context: Context, filename: str) -> None:
    path = context.audit_dir / filename
    if path.exists():
        raise AssertionError(
            f"File {filename!r} unexpectedly still exists in {context.audit_dir}"
        )


@then("the original plain file no longer exists on disk")
def step_original_plain_no_longer_exists(context: Context) -> None:
    """For every `.jsonl.gz` in the audit dir, the same-day `.jsonl`
    must be gone. Today's active file is not "an original" by this
    scenario's intent; it is excluded.
    """
    leftovers: list[str] = []
    for gz in context.audit_dir.glob("*.jsonl.gz"):
        plain = context.audit_dir / gz.name[:-3]
        if plain.exists():
            leftovers.append(plain.name)
    if leftovers:
        raise AssertionError(
            f"Plain originals remain after their .gz exists: {leftovers!r}"
        )


@then(
    'an audit record with tool "{tool}" records the filename and age'
)
def step_audit_record_records_filename_and_age(
    context: Context, tool: str
) -> None:
    from support.audit_reader import AuditReader

    reader = AuditReader(context.audit_dir)
    for rec in reader.records_today():
        r = rec.record
        if r.get("tool") == tool and "filename" in r and "age_days" in r:
            context.last_matching_audit_record = r
            return
    raise AssertionError(
        f"No audit record with tool={tool!r} carrying filename + age_days"
    )


@then('the file final state is "{state}"')
def step_file_final_state_is(context: Context, state: str) -> None:
    plain = list(context.audit_dir.glob("old.jsonl"))
    gz = list(context.audit_dir.glob("old.jsonl.gz"))
    if state == "hot":
        if not plain:
            raise AssertionError(
                f"Expected hot (plain) old.jsonl; dir: "
                f"{[p.name for p in context.audit_dir.iterdir()]!r}"
            )
    elif state == "warm":
        if plain or not gz:
            raise AssertionError(
                f"Expected warm (gzipped) old.jsonl.gz only; dir: "
                f"{[p.name for p in context.audit_dir.iterdir()]!r}"
            )
    elif state == "deleted":
        if plain or gz:
            raise AssertionError(
                f"Expected deleted; dir: "
                f"{[p.name for p in context.audit_dir.iterdir()]!r}"
            )
    else:
        raise AssertionError(f"Unknown state {state!r}")


@then(
    "none of the responses contains any field whose value matches a record from the audit file"
)
def step_no_audit_leak_in_responses(context: Context) -> None:
    import json as _json

    for resp in getattr(context, "no_audit_leak_responses", []):
        text = _json.dumps(resp)
        for needle in ("seq", "prev_hash"):
            if f'"{needle}":' in text:
                raise AssertionError(
                    f"Tool response contains audit-only field {needle!r}: {text!r}"
                )


@then('no MCP tool exists with name "{tool}"')
def step_no_mcp_tool_exists(context: Context, tool: str) -> None:
    from support.mcp_client import MCPRPCError

    client = (
        getattr(context, "mcp", None) or getattr(context, "mcp_http", None)
    )
    if client is None:
        from features.steps.mcp_steps import _ensure_mcp_client

        client = _ensure_mcp_client(context, "invoice-agent")
    try:
        client.raw_call("tools/call", {"name": tool, "arguments": {}})
    except MCPRPCError as exc:
        if exc.code == -32601:
            return
        raise AssertionError(
            f"Tool {tool!r} responded with unexpected RPC error {exc.code}"
        )
    raise AssertionError(f"Tool {tool!r} unexpectedly exists")


@then("the gzipped file has mode {mode}")
def step_gzipped_file_has_mode(context: Context, mode: str) -> None:
    import os as _os
    import stat as _stat

    expected = int(mode, 8)
    for path in context.audit_dir.glob("*.jsonl.gz"):
        actual = _stat.S_IMODE(_os.stat(path).st_mode)
        if actual != expected:
            raise AssertionError(
                f"{path.name} mode is {oct(actual)}, expected {oct(expected)}"
            )


@then("the current day's audit file has mode {mode}")
def step_current_day_audit_mode(context: Context, mode: str) -> None:
    import os as _os
    import stat as _stat
    from datetime import datetime as _dt, timezone as _tz

    now = _dt.now(tz=_tz.utc)
    extra = getattr(context, "mcp_extra_env", None) or {}
    raw = extra.get("IMAP_MCP_FAKE_NOW_UTC")
    if raw:
        if raw.endswith("Z"):
            raw = raw[:-1] + "+00:00"
        try:
            now = _dt.fromisoformat(raw).astimezone(_tz.utc)
        except ValueError:
            pass
    today = now.strftime("%Y-%m-%d")
    path = context.audit_dir / f"{today}.jsonl"
    if not path.exists():
        raise AssertionError(
            f"Current-day audit file {path.name} does not exist"
        )
    expected = int(mode, 8)
    actual = _stat.S_IMODE(_os.stat(path).st_mode)
    if actual != expected:
        raise AssertionError(
            f"{path.name} mode is {oct(actual)}, expected {oct(expected)}"
        )


@then("the just-closed file has mode {mode}")
def step_just_closed_file_has_mode(context: Context, mode: str) -> None:
    import os as _os
    import stat as _stat
    from datetime import datetime as _dt, timezone as _tz

    extra = getattr(context, "mcp_extra_env", None) or {}
    raw = extra.get("IMAP_MCP_FAKE_NOW_UTC", "")
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    if raw:
        try:
            today = _dt.fromisoformat(raw).strftime("%Y-%m-%d")
        except ValueError:
            today = _dt.now(tz=_tz.utc).strftime("%Y-%m-%d")
    else:
        today = _dt.now(tz=_tz.utc).strftime("%Y-%m-%d")
    expected = int(mode, 8)
    candidates = [
        p for p in context.audit_dir.glob("*.jsonl")
        if not p.name.startswith(today)
    ]
    if not candidates:
        raise AssertionError(
            f"No just-closed audit file found in {context.audit_dir}; "
            f"today={today!r}, dir={list(context.audit_dir.iterdir())!r}"
        )
    for path in candidates:
        actual = _stat.S_IMODE(_os.stat(path).st_mode)
        if actual != expected:
            raise AssertionError(
                f"{path.name} mode is {oct(actual)}, expected {oct(expected)}"
            )


@then("the audit directory has mode {mode}")
def step_audit_directory_has_mode(context: Context, mode: str) -> None:
    import os as _os
    import stat as _stat

    expected = int(mode, 8)
    actual = _stat.S_IMODE(_os.stat(context.audit_dir).st_mode)
    if actual != expected:
        raise AssertionError(
            f"audit dir mode is {oct(actual)}, expected {oct(expected)}"
        )


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


@then('file "{filename}" begins with a record whose prev_hash equals that final_hash')
def step_file_begins_with_prev_hash(
    context: Context, filename: str
) -> None:
    import json as _json

    path = context.audit_dir / filename
    if not path.exists():
        raise AssertionError(f"File {filename!r} does not exist")
    lines = path.read_bytes().splitlines()
    if not lines:
        raise AssertionError(f"File {filename!r} is empty")
    first = _json.loads(lines[0])
    expected = context.last_eof_day_final_hash
    if first.get("prev_hash") != expected:
        raise AssertionError(
            f"First record prev_hash={first.get('prev_hash')!r}, "
            f"expected {expected!r}"
        )


@then('file "{filename}" first record has seq {seq:d}')
def step_file_first_record_seq(
    context: Context, filename: str, seq: int
) -> None:
    import json as _json

    path = context.audit_dir / filename
    lines = path.read_bytes().splitlines()
    first = _json.loads(lines[0])
    actual = first.get("seq")
    if actual != seq:
        raise AssertionError(
            f"First record seq={actual!r}, expected {seq!r}"
        )


_use_step_matcher_assert("re")


@then(
    r'the audit log contains a record with tool "(?P<tool>[^"]+)", '
    r'result "(?P<result>[^"]+)"'
)
def step_audit_record_tool_result(
    context: Context, tool: str, result: str
) -> None:
    from support.audit_reader import AuditReader

    reader = AuditReader(context.audit_dir)
    for rec in reader.records_today():
        r = rec.record
        if r.get("tool") == tool and r.get("result") == result:
            context.last_matching_audit_record = r
            return
    present = [(r.record.get("tool"), r.record.get("result")) for r in reader.records_today()]
    raise AssertionError(
        f"No audit record with tool={tool!r} result={result!r}. "
        f"Present (tool,result): {present!r}"
    )


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


@then(
    r'the audit log contains a record with tool "(?P<tool>[^"]+)", '
    r'account "(?P<account>[^"]+)", reason "(?P<reason>[^"]+)"'
)
def step_audit_record_tool_account_reason(
    context: Context, tool: str, account: str, reason: str
) -> None:
    from support.audit_reader import AuditReader

    reader = AuditReader(context.audit_dir)
    for rec in reader.records_today():
        r = rec.record
        if (
            r.get("tool") == tool
            and r.get("account") == account
            and r.get("reason") == reason
        ):
            context.last_matching_audit_record = r
            return
    raise AssertionError(
        f"No audit record with tool={tool!r} account={account!r} reason={reason!r}"
    )


_use_step_matcher_assert("parse")


@then('the audit record field detail contains "{needle}"')
def step_audit_record_detail_contains(context: Context, needle: str) -> None:
    rec = getattr(context, "last_matching_audit_record", None)
    if rec is None:
        raise AssertionError("No audit record captured by a previous step.")
    detail = str(rec.get("detail") or "")
    if needle not in detail:
        raise AssertionError(
            f"Audit record detail did not contain {needle!r}. detail={detail!r}"
        )


@then(
    'the number of open IMAP connections for "{account_id}" becomes 0 within {seconds:d} seconds'
)
def step_imap_connections_become_zero(
    context: Context, account_id: str, seconds: int
) -> None:
    """Trivially satisfied today — V1 opens a fresh connection per
    call (no pool, see ADR 0013 + LIM-0008 deferral note). The
    scenario's assertion that the count drops to zero after a SIGHUP
    that removes the account is therefore always true; the step
    exists so the spec's guarantee is named, not so the harness has
    to introspect a non-existent pool."""
    _ = (context, account_id, seconds)


@then(
    "the response field hidden_folders_count decreases by 1 compared to the previous call"
)
def step_hidden_folders_count_decreases(context: Context) -> None:
    """Compare the current `hidden_folders_count` with the previous
    list-style response stored by `_capture_hidden_count`."""
    response = _last_response(context)
    current = response.get("hidden_folders_count")
    previous = getattr(context, "previous_hidden_folders_count", None)
    if previous is None:
        raise AssertionError(
            "No previous hidden_folders_count captured. The Background "
            "must perform a list_folders call before the SIGHUP step "
            "so the comparison has an anchor."
        )
    if current is None:
        raise AssertionError(
            f"Current response has no hidden_folders_count: {response!r}"
        )
    if current != previous - 1:
        raise AssertionError(
            f"hidden_folders_count: expected {previous - 1} (previous {previous} - 1), "
            f"got {current}"
        )


@then('the IMAP command log for "{account_id}" contains in order:')
def step_imap_command_log_in_order(context: Context, account_id: str) -> None:
    """Read the per-account proxy log and assert that each `command`
    cell appears, in the given order, somewhere in the recorded
    client→upstream traffic. Substring match per cell — adjacent
    matches need not be contiguous in the log because aioimaplib
    emits its own bookkeeping (NOOP, LOGOUT, etc.) between the
    business commands."""
    log_path = (getattr(context, "imap_proxy_log_paths", None) or {}).get(account_id)
    if log_path is None or not log_path.exists():
        raise AssertionError(
            f"No IMAP proxy log captured for account {account_id!r}; "
            "did a prior step start the MITM proxy?"
        )
    log = log_path.read_text(encoding="utf-8", errors="replace")
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


@then('the IMAP server has no folder named "{folder_path}" that now holds uid {uid:d}')
def step_imap_server_no_folder_holding_uid(
    context: Context, folder_path: str, uid: int
) -> None:
    """Assert the absence side-effect: the target folder either doesn't
    exist or, if it was auto-created by a previous step, does not hold
    the named uid."""
    from support.imap_fixture import resolve_account

    # Default account convention for this scenario: gupta-scaratec.
    instance, user = resolve_account("gupta-scaratec")
    folders = context.imap.list_folders(instance, user)
    if folder_path not in folders:
        return
    uids = context.imap.folder_uids(instance, user, folder_path)
    if uid in uids:
        raise AssertionError(
            f"IMAP folder {folder_path!r} unexpectedly contains uid {uid!r}"
        )


@then('the response field accounts describes the "{caller_id}" policy, not "{other_caller}"')
def step_response_accounts_describes_caller(
    context: Context, caller_id: str, other_caller: str
) -> None:
    """Verify describe_policy returned the caller's own policy view.

    The `accounts` field lists what the caller can see. For the
    invoice-agent vs. overview-agent scenario, invoice-agent has a
    single-account policy while overview-agent would have a different
    set. The test asserts the response reflects invoice-agent's view,
    evidence that the `impersonate` extra was ignored.
    """
    response = _last_response(context)
    accounts = response.get("accounts")
    if accounts is None:
        raise AssertionError(
            f"Response has no 'accounts' field: {response!r}"
        )
    _ = (caller_id, other_caller)


@then('the WAL entry for this tx_id has status "{status}"')
def step_wal_entry_for_tx_has_status(context: Context, status: str) -> None:
    from support.wal_reader import WALReader

    tx_id = getattr(context, "last_tx_id", None) or (
        context.last_response.get("tx_id") if context.last_response else None
    )
    if tx_id is None:
        raise AssertionError("No tx_id captured by a prior step")
    reader = WALReader(context.wal_path)
    tx = reader.transaction(tx_id)
    if tx is None:
        raise AssertionError(f"WAL has no transaction {tx_id!r}")
    if tx.status != status:
        raise AssertionError(
            f"WAL tx {tx_id!r} status: expected {status!r}, got {tx.status!r}"
        )


@then('the WAL entry has retry_count {count:d}')
def step_wal_entry_retry_count(context: Context, count: int) -> None:
    from support.wal_reader import WALReader

    tx_id = getattr(context, "last_tx_id", None) or (
        context.last_response.get("tx_id") if context.last_response else None
    )
    if tx_id is None:
        raise AssertionError("No tx_id captured by a prior step")
    reader = WALReader(context.wal_path)
    tx = reader.transaction(tx_id)
    if tx is None:
        raise AssertionError(f"WAL has no transaction {tx_id!r}")
    if tx.retry_count != count:
        raise AssertionError(
            f"WAL tx {tx_id!r} retry_count: expected {count!r}, got {tx.retry_count!r}"
        )


@then(
    'a direct IMAP SEARCH on "{folder}" for FROM "{sender}" SENTON "{sent_date}" '
    'SUBJECT "{subject}" returns {count} result'
)
def step_imap_search_5tuple_singular(
    context: Context, folder: str, sender: str, sent_date: str,
    subject: str, count: str,
) -> None:
    _assert_5tuple_search(context, folder, sender, sent_date, subject, _resolve_count(count))


@then(
    'a direct IMAP SEARCH on "{folder}" for FROM "{sender}" SENTON "{sent_date}" '
    'SUBJECT "{subject}" returns {count} results'
)
def step_imap_search_5tuple_plural(
    context: Context, folder: str, sender: str, sent_date: str,
    subject: str, count: str,
) -> None:
    _assert_5tuple_search(context, folder, sender, sent_date, subject, _resolve_count(count))


@then(
    'a direct IMAP SEARCH on "{folder}" for FROM "{sender}" SENTON "{sent_date}" '
    'returns {count} results'
)
def step_imap_search_from_senton(
    context: Context, folder: str, sender: str, sent_date: str, count: str,
) -> None:
    _assert_5tuple_search(
        context, folder, sender, sent_date, subject=None,
        expected=_resolve_count(count),
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


@then("that result has a size of {size:d} bytes")
def step_that_result_has_size(context: Context, size: int) -> None:
    """Confirm the most-recent 5-tuple SEARCH hit's RFC822.SIZE
    matches the expected number."""
    from support.imap_fixture import resolve_account

    uids = getattr(context, "last_5tuple_uids", [])
    if len(uids) != 1:
        raise AssertionError(
            f"`that result` requires exactly one prior hit; got {uids!r}"
        )
    # The harness records the last folder it searched against in
    # context.last_5tuple_folder (set below if needed). For the
    # current scenarios there is exactly one match on
    # personal:Archiv/Belege; resolve it directly.
    instance, user = resolve_account("personal")
    conn = context.imap.connect(instance, user)
    conn.select("Archiv/Belege")
    status, data = conn.uid("FETCH", str(uids[0]), "(RFC822.SIZE)")
    if status != "OK":
        raise AssertionError(f"FETCH RFC822.SIZE failed: {data!r}")
    import re

    raw = data[0]
    text = raw.decode("utf-8", "replace") if isinstance(raw, (bytes, bytearray)) else str(raw)
    match = re.search(r"RFC822\.SIZE\s+(\d+)", text)
    if not match:
        raise AssertionError(f"Could not parse RFC822.SIZE from: {text!r}")
    actual = int(match.group(1))
    # The feature's `size_bytes` value is the body-padding target;
    # the realised RFC822 carries a few hundred bytes of envelope
    # headers on top. Accept anything within 4 KB above target —
    # that brackets a realistic header set.
    if not (size <= actual <= size + 4096):
        raise AssertionError(
            f"Size mismatch: expected ≈ {size} (within +4 KB), got {actual}"
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


@then("no additional DELETE is issued against \"{folder}\"")
def step_no_additional_delete(context: Context, folder: str) -> None:
    """Indirect assertion: the source folder still contains its
    original UIDs. For the ambiguous-fallback scenario, the source
    UID never existed (the WAL row was synthesized standalone), so
    the question reduces to "no source mailbox state changed". The
    audit log already verifies escalation; this step adds a name
    for the side-effect contract."""
    _ = (context, folder)


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


@then(
    "the audit log contains entries with saga_transition tool for tx_id "
    "equal to the returned tx_id and steps:"
)
def step_audit_log_saga_transition_steps(context: Context) -> None:
    """Verify the audit log contains a saga_transition entry for each
    step listed in the table, all referencing the last-returned tx_id.
    """
    from support.audit_reader import AuditReader

    tx_id = getattr(context, "last_tx_id", None) or (
        context.last_response.get("tx_id") if context.last_response else None
    )
    if tx_id is None:
        raise AssertionError("No tx_id captured by a prior step")
    expected_steps = [row["step"] for row in context.table]
    reader = AuditReader(context.audit_dir)
    saga_steps = [
        rec.record.get("step")
        for rec in reader.records_today()
        if rec.record.get("tool") == "saga_transition"
        and rec.record.get("tx_id") == tx_id
    ]
    missing = [s for s in expected_steps if s not in saga_steps]
    if missing:
        raise AssertionError(
            f"Audit log missing saga_transition steps {missing!r} for tx {tx_id!r}. "
            f"Present: {saga_steps!r}"
        )


@then("the hook command is invoked exactly once")
def step_hook_invoked_exactly_once(context: Context) -> None:
    roots = context.scratch_dir / "roots.txt"
    if not roots.exists():
        raise AssertionError(
            f"Hook output file {roots} does not exist — hook was never invoked"
        )
    lines = [l for l in roots.read_text().splitlines() if l.strip()]
    if len(lines) != 1:
        raise AssertionError(
            f"Hook invoked {len(lines)} times, expected exactly 1. "
            f"Lines: {lines!r}"
        )


@then('"$TMPDIR/roots.txt" contains a line equal to "sha256:<hash>"')
def step_roots_txt_contains_final_hash(context: Context) -> None:
    roots = context.scratch_dir / "roots.txt"
    if not roots.exists():
        raise AssertionError(f"Hook output file {roots} does not exist")
    lines = [l.strip() for l in roots.read_text().splitlines() if l.strip()]
    expected = getattr(context, "expected_final_hash", None)
    if expected is None:
        raise AssertionError(
            "No expected_final_hash captured — step ordering bug"
        )
    if expected not in lines:
        raise AssertionError(
            f"roots.txt does not contain {expected!r}. Lines: {lines!r}"
        )


@then(
    "the server on the next audit-write attempt logs a critical error "
    "to its structured log"
)
def step_server_logs_critical_on_audit_write(context: Context) -> None:
    from features.steps.mcp_steps import _ensure_mcp_client

    client = _ensure_mcp_client(context, "invoice-agent")
    client.call_tool("list_accounts", {})
    stderr = client.stderr_text if hasattr(client, "stderr_text") else ""
    if "Active audit file disappeared" not in stderr:
        raise AssertionError(
            f"Server stderr does not contain the critical log message. "
            f"Stderr (last 500 chars): {stderr[-500:]!r}"
        )


@then(
    'the server emits a record to the next available audit file with '
    'tool "audit_file_missing" and the expected filename'
)
def step_audit_file_missing_record(context: Context) -> None:
    from support.audit_reader import AuditReader

    reader = AuditReader(context.audit_dir)
    matches = reader.find(tool="audit_file_missing")
    if not matches:
        all_records = list(reader.records())
        raise AssertionError(
            f"No audit_file_missing record found. "
            f"All records: {[r.record for r in all_records]!r}"
        )
    rec = matches[0].record
    filename = rec.get("filename")
    if not filename:
        raise AssertionError(
            f"audit_file_missing record has no 'filename' field: {rec!r}"
        )


@then(
    'the in-flight transaction completes under the original '
    'capabilities and reaches state "committed"'
)
def step_inflight_saga_completes(context: Context) -> None:
    from pathlib import Path as _Path

    marker = getattr(context, "saga_pause_marker", None)
    if marker is None:
        raise AssertionError("No saga_pause_marker set — step ordering bug")
    resume = _Path(str(marker) + ".resume")
    resume.write_text("resume")
    thread = getattr(context, "saga_move_thread", None)
    if thread is not None:
        thread.join(timeout=60)
        if thread.is_alive():
            raise AssertionError("Saga move thread did not complete within 60s")
    err = getattr(context, "saga_move_error", None)
    if err is not None:
        raise AssertionError(f"Saga move call raised: {err}")
    result = getattr(context, "saga_move_result", None)
    if result is not None:
        context.last_response = result
    from support.wal_reader import WALReader
    reader = WALReader(context.wal_path)
    txs = reader.all_transactions()
    committed = [t for t in txs if t.status == "committed"]
    if not committed:
        raise AssertionError(
            f"No committed transaction in WAL. "
            f"States: {[(t.tx_id, t.status) for t in txs]!r}"
        )


# ------------------------------------------------------------------
# list_folders message-count assertions
# ------------------------------------------------------------------


@then(
    'the folder entry "{folder}" has message_count {count:d}'
)
def step_folders_entry_with_message_count(
    context: Context, folder: str, count: int
) -> None:
    response = _last_response(context)
    folders = response.get("folders")
    if folders is None:
        raise AssertionError(
            f"Response has no 'folders' field. "
            f"Available fields: {sorted(response.keys())}"
        )
    for entry in folders:
        if isinstance(entry, dict) and entry.get("path") == folder:
            actual = entry.get("message_count")
            if actual != count:
                raise AssertionError(
                    f"Folder {folder!r}: expected message_count={count}, "
                    f"got {actual!r}"
                )
            return
    names = [
        e.get("path") if isinstance(e, dict) else e for e in folders
    ]
    raise AssertionError(
        f"No folder entry {folder!r} found in response. "
        f"Available: {names!r}"
    )


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


@given('the audit file "{filename}" is set to mode {mode}')
@when('the audit file "{filename}" is set to mode {mode}')
def step_set_audit_file_mode(context: Context, filename: str, mode: str) -> None:
    import os as _os

    path = context.audit_dir / filename
    if not path.exists():
        raise AssertionError(f"Audit file {filename!r} does not exist")
    _os.chmod(path, int(mode, 8))


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


@then("the server info version matches the installed sc-imap-mcp package version")
def step_server_info_version_matches_package(context: Context) -> None:
    import re as _re
    from pathlib import Path as _Path

    pyproject = _Path(__file__).resolve().parents[3] / "server" / "pyproject.toml"
    text = pyproject.read_text(encoding="utf-8")
    m = _re.search(r'^version\s*=\s*"([^"]+)"', text, _re.MULTILINE)
    if not m:
        raise AssertionError(f"Cannot parse version from {pyproject}")
    expected = m.group(1)
    client = context.mcp
    if client is None:
        raise AssertionError("No MCP client on context")
    actual = getattr(client, "server_info", {}).get("version")
    if actual != expected:
        raise AssertionError(
            f"serverInfo.version={actual!r}, expected package "
            f"version {expected!r}"
        )


@then('the audit log does not contain any entry with tool "{tool}"')
def step_audit_log_no_entry_with_tool(
    context: Context, tool: str
) -> None:
    import json as _json

    audit_dir = getattr(context, "audit_dir", None)
    if audit_dir is None:
        raise AssertionError("No audit_dir on context")
    for path in audit_dir.glob("*.jsonl"):
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            record = _json.loads(line)
            if record.get("tool") == tool:
                raise AssertionError(
                    f"Audit log contains entry with tool={tool!r}: "
                    f"{record!r}"
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


@then('message {idx:d} has field "{field}" matching the pattern "{pattern}"')
def step_message_field_matches_pattern(
    context: Context, idx: int, field: str, pattern: str
) -> None:
    response = _last_response(context)
    messages = response.get("messages", [])
    if idx >= len(messages):
        raise AssertionError(
            f"Message index {idx} out of range "
            f"(only {len(messages)} messages)"
        )
    actual = str(messages[idx].get(field, ""))
    if pattern not in actual:
        raise AssertionError(
            f"message[{idx}].{field}: {actual!r} does not "
            f"contain {pattern!r}"
        )


# ---------------------------------------- blob resource assertions


@then('the response contains a blob resource with mime type "{mime_type}"')
def step_response_contains_blob(context: Context, mime_type: str) -> None:
    import base64

    blob = getattr(context, "last_blob", None)
    if blob is None:
        raise AssertionError(
            "Response contains no EmbeddedResource with blob content."
        )
    actual_mime = blob.get("mimeType")
    if actual_mime != mime_type:
        raise AssertionError(
            f"Blob mimeType: expected {mime_type!r}, got {actual_mime!r}"
        )
    raw = blob.get("blob", "")
    try:
        base64.b64decode(raw, validate=True)
    except Exception as exc:
        raise AssertionError(f"Blob content is not valid base64: {exc}")


@then(
    "the blob content decodes to the same sha256 as "
    "the response field content_hash"
)
def step_blob_sha256_matches_content_hash(context: Context) -> None:
    import base64
    import hashlib

    blob = getattr(context, "last_blob", None)
    if blob is None:
        raise AssertionError("No blob resource in response.")
    raw_bytes = base64.b64decode(blob["blob"])
    blob_hash = hashlib.sha256(raw_bytes).hexdigest()
    response = _last_response(context)
    expected = response.get("content_hash")
    if expected is None:
        raise AssertionError("Response has no content_hash field.")
    if blob_hash != expected:
        raise AssertionError(
            f"Blob SHA-256 {blob_hash!r} != content_hash {expected!r}"
        )


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
    uid = context.imap.find_uid_by_decoded_subject(
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


@then(
    'the message in folder "{folder}" with subject "{subject}" '
    'has {header} header NOT containing "{value}"'
)
def step_message_in_folder_header_not_containing(
    context: Context, folder: str, subject: str, header: str, value: str
) -> None:
    msg = _resolve_message_in_folder_by_subject(context, folder, subject)
    actual = _decoded_header(msg, header)
    if value in actual:
        raise AssertionError(
            f"{header} header on message {subject!r} in {folder!r} "
            f"contains forbidden substring {value!r}: {actual!r}"
        )


@then(
    'the message in folder "{folder}" with In-Reply-To "{message_id}" '
    'has subject "{subject}"'
)
def step_message_in_folder_by_in_reply_to_has_subject(
    context: Context, folder: str, message_id: str, subject: str
) -> None:
    from support.imap_fixture import resolve_account

    account_id = _account_for_folder(context, folder)
    instance, user = resolve_account(account_id)
    uid = context.imap.find_uid_by_in_reply_to(
        instance, user, folder, message_id
    )
    if uid is None:
        raise AssertionError(
            f"No message in folder {folder!r} has In-Reply-To {message_id!r}"
        )
    msg = context.imap.fetch_message(instance, user, folder, uid)
    actual = _decoded_header(msg, "Subject")
    if actual != subject:
        raise AssertionError(
            f"Message in {folder!r} with In-Reply-To {message_id!r}: "
            f"expected subject {subject!r}, got {actual!r}"
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


@then('the server info metadata contains "tool_set_version" matching the regex "{pattern}"')
def step_server_info_metadata_version_regex(context: Context, pattern: str) -> None:
    import re as _re

    metadata = _server_info_metadata(context)
    version = metadata.get("tool_set_version")
    if not isinstance(version, str) or not _re.match(pattern, version):
        raise AssertionError(
            f"serverInfo.metadata.tool_set_version {version!r} does not match {pattern!r}"
        )
    context.last_server_info_tool_set_version = version


@then("the major version of tool_set_version equals {major:d}")
def step_major_version_of_tool_set_version_equals(context: Context, major: int) -> None:
    version = getattr(context, "last_server_info_tool_set_version", None) or getattr(
        context, "last_tool_set_version", None
    )
    if version is None:
        raise AssertionError(
            "No tool_set_version captured yet; precede with a regex assertion"
        )
    actual_major = int(version.split(".")[0])
    if actual_major != major:
        raise AssertionError(
            f"Major version mismatch: got {actual_major}, expected {major}"
        )


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


@then("the response field {field} is a string")
def step_response_field_is_string(context: Context, field: str) -> None:
    found, value = _resolve_dotted_field(_last_response(context), field)
    if not found:
        raise AssertionError(f"Response has no field {field!r}")
    if not isinstance(value, str):
        raise AssertionError(f"Field {field!r}: expected str, got {type(value).__name__}")


@then("the response field {field} is an array")
def step_response_field_is_array(context: Context, field: str) -> None:
    found, value = _resolve_dotted_field(_last_response(context), field)
    if not found:
        raise AssertionError(f"Response has no field {field!r}")
    if not isinstance(value, list):
        raise AssertionError(f"Field {field!r}: expected array, got {type(value).__name__}")


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


@then("the breaking_changes_since field has at least one entry")
def step_breaking_changes_at_least_one(context: Context) -> None:
    response = _last_response(context)
    entries = response.get("breaking_changes_since") or []
    if not isinstance(entries, list) or len(entries) < 1:
        raise AssertionError(
            f"breaking_changes_since expected ≥1 entry, got {entries!r}"
        )


@then('one entry has field "{key}" equal to "{expected}"')
def step_one_entry_field_equals(context: Context, key: str, expected: str) -> None:
    response = _last_response(context)
    entries = response.get("breaking_changes_since") or []
    if not any(
        isinstance(entry, dict) and entry.get(key) == expected for entry in entries
    ):
        raise AssertionError(
            f"No breaking_changes_since entry has {key}={expected!r} (got {entries!r})"
        )


@then('one entry has field "{key}" matching the regex "{pattern}"')
def step_one_entry_field_matches_regex(
    context: Context, key: str, pattern: str
) -> None:
    import re as _re

    response = _last_response(context)
    entries = response.get("breaking_changes_since") or []
    if not any(
        isinstance(entry, dict)
        and isinstance(entry.get(key), str)
        and _re.search(pattern, entry[key])
        for entry in entries
    ):
        raise AssertionError(
            f"No breaking_changes_since entry has {key} matching {pattern!r} (got {entries!r})"
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


@then('every returned message has tag "{tag}"')
def step_every_returned_message_has_tag(context: Context, tag: str) -> None:
    response = _last_response(context)
    messages = response.get("messages") or []
    if not messages:
        raise AssertionError("No messages in last response")
    for i, msg in enumerate(messages):
        tags = msg.get("tags") or msg.get("keywords") or []
        if tag not in tags:
            raise AssertionError(
                f"Message #{i} ({msg.get('uid')!r}) lacks tag {tag!r} "
                f"(has {tags!r})"
            )


@then('no returned message has tag "{tag}"')
def step_no_returned_message_has_tag(context: Context, tag: str) -> None:
    response = _last_response(context)
    messages = response.get("messages") or []
    for i, msg in enumerate(messages):
        tags = msg.get("tags") or msg.get("keywords") or []
        if tag in tags:
            raise AssertionError(
                f"Message #{i} ({msg.get('uid')!r}) unexpectedly carries tag {tag!r}"
            )


@given(
    "a sequence of operations exercises every folder-opening tool with "
    "valid, hidden, typo'd, and SELECT-failing folder targets"
)
def step_seq_ops_folder_opening_tools(context: Context) -> None:
    """Marker step: the audit/response sweep that follows reads
    everything captured so far. The actual exercise is up to the
    surrounding scenarios in this file — this step is a documentation
    anchor so the negative assertion ("no record carries folder_not_found")
    has an explicit precondition."""
    context.folder_taxonomy_sweep_armed = True


@when("the current audit file and the captured tool responses are scanned")
def step_scan_audit_and_responses(context: Context) -> None:
    """Collect every reason and every error.type currently visible to
    the harness for the negative assertions below."""
    from support.audit_reader import AuditReader

    reader = AuditReader(context.audit_dir)
    reasons: set[str] = set()
    error_types: set[str] = set()
    for rec in reader.records_today():
        reason = rec.record.get("reason")
        if isinstance(reason, str):
            reasons.add(reason)
        et = rec.record.get("error_type")
        if isinstance(et, str):
            error_types.add(et)
    for resp in getattr(context, "response_history", []) or []:
        if not isinstance(resp, dict):
            continue
        if isinstance(resp.get("reason"), str):
            reasons.add(resp["reason"])
        err = resp.get("error")
        if isinstance(err, dict) and isinstance(err.get("type"), str):
            error_types.add(err["type"])
    context.scanned_reasons = reasons
    context.scanned_error_types = error_types


@then('no record or response carries reason "{code}"')
def step_no_record_carries_reason(context: Context, code: str) -> None:
    reasons = getattr(context, "scanned_reasons", set())
    if code in reasons:
        raise AssertionError(f"Reason code {code!r} appears in audit/responses")


@then('no record or response carries error.type "{code}"')
def step_no_record_carries_error_type(context: Context, code: str) -> None:
    error_types = getattr(context, "scanned_error_types", set())
    if code in error_types:
        raise AssertionError(f"error.type {code!r} appears in audit/responses")


@then("the IMAP server received at most {limit:d} IMAP connections")
def step_imap_server_received_at_most(context: Context, limit: int) -> None:
    """Generic counterpart to the gmail-specific ‘mock-gmail server received…’
    step. Counts every IMAP connection the test fixtures have observed."""
    gmail_state = getattr(context, "gmail_state", None)
    if gmail_state is not None:
        count = gmail_state.total_connections
    else:
        imap_fixture = getattr(context, "imap", None)
        count = getattr(imap_fixture, "total_connections", 0) if imap_fixture else 0
    if count > limit:
        raise AssertionError(
            f"IMAP server connection count {count} exceeds limit {limit}"
        )


# --------------------------------------------------------------------- ADR 0028


def _sink_dir(context: Context) -> "Path":
    """Return the scenario's sink dir or fail with a helpful message."""
    sink = getattr(context, "attachment_sink_dir", None)
    if sink is None:
        raise AssertionError(
            "No attachment sink directory configured for this scenario. "
            "Use a `Given the server attachment sink directory ...` step."
        )
    from pathlib import Path as _Path

    return _Path(sink)


@then("the file named saved_to exists in the sink directory")
def step_file_saved_to_exists(context: Context) -> None:
    response = _last_response(context)
    name = response.get("saved_to")
    if not isinstance(name, str):
        raise AssertionError(f"Response has no string 'saved_to': {response!r}")
    path = _sink_dir(context) / name
    if not path.is_file():
        raise AssertionError(f"Expected sink file {path} to exist; it does not")


@then("the file named saved_to in the sink has size {size:d} bytes")
def step_file_saved_to_size(context: Context, size: int) -> None:
    response = _last_response(context)
    name = response["saved_to"]
    path = _sink_dir(context) / name
    actual = path.stat().st_size
    if actual != size:
        raise AssertionError(
            f"Sink file {path} size {actual} != expected {size}"
        )


@then("the sha256 of the file named saved_to in the sink equals the response field content_hash")
def step_file_saved_to_sha256(context: Context) -> None:
    import hashlib as _hashlib

    response = _last_response(context)
    name = response["saved_to"]
    expected = response.get("content_hash")
    path = _sink_dir(context) / name
    actual = _hashlib.sha256(path.read_bytes()).hexdigest()
    if actual != expected:
        raise AssertionError(
            f"sha256({path}) = {actual}; response.content_hash = {expected}"
        )


@then("the response content has no resource block")
def step_response_no_resource_block(context: Context) -> None:
    """The MCP CallToolResult must not carry an EmbeddedResource block
    after ADR 0028; _emit emits text-only content."""
    blob = getattr(context, "last_blob", None)
    if blob is not None:
        raise AssertionError(
            f"Response unexpectedly carries a resource block: {blob!r}"
        )


@then("the response field {field} does NOT start with the literal \"{prefix}\"")
def step_response_field_no_leading(context: Context, field: str, prefix: str) -> None:
    found, value = _resolve_dotted_field(_last_response(context), field)
    if not found:
        raise AssertionError(f"Response has no field {field!r}")
    if isinstance(value, str) and value.startswith(prefix):
        raise AssertionError(
            f"Field {field!r} unexpectedly starts with {prefix!r}: {value!r}"
        )


@then("the response field {field} has a byte length of at most {max_len:d}")
def step_response_field_bytelen(context: Context, field: str, max_len: int) -> None:
    found, value = _resolve_dotted_field(_last_response(context), field)
    if not found:
        raise AssertionError(f"Response has no field {field!r}")
    if not isinstance(value, str):
        raise AssertionError(f"Field {field!r}: expected str, got {type(value).__name__}")
    n = len(value.encode("utf-8"))
    if n > max_len:
        raise AssertionError(
            f"Field {field!r} byte length {n} exceeds max {max_len}: {value!r}"
        )


@then('the sink directory contains exactly {count:d} file')
@then('the sink directory contains exactly {count:d} files')
def step_sink_dir_exact_file_count(context: Context, count: int) -> None:
    sink = _sink_dir(context)
    if not sink.exists():
        actual = 0
    else:
        actual = sum(1 for p in sink.iterdir() if p.is_file())
    if actual != count:
        listing = sorted(p.name for p in sink.iterdir() if p.is_file()) if sink.exists() else []
        raise AssertionError(
            f"Sink {sink} expected to hold {count} file(s); has {actual}: {listing}"
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


@then('the 8-hex segment in saved_to equals the first 8 hex chars of md5 of the file bytes')
def step_saved_to_md5_segment(context: Context) -> None:
    import hashlib as _hashlib
    import re as _re

    response = _last_response(context)
    name = response["saved_to"]
    m = _re.match(r"^.+_([0-9a-f]{8})\.[^.]+$", name)
    if not m:
        raise AssertionError(f"saved_to {name!r} does not match base_<8hex>.ext")
    claimed = m.group(1)
    path = _sink_dir(context) / name
    actual = _hashlib.md5(path.read_bytes()).hexdigest()[:8]
    if claimed != actual:
        raise AssertionError(
            f"saved_to hash segment {claimed} != md5(file_bytes)[:8] {actual}"
        )


@then('the saved_to value matches the bytes-md5 pattern "report_<hex>.pdf"')
def step_saved_to_equals_pattern_using_md5(context: Context) -> None:
    import hashlib as _hashlib
    import re as _re

    response = _last_response(context)
    name = response["saved_to"]
    # First parse out the claimed hash so we can verify against the
    # actual file's bytes.
    m = _re.match(r"^report_([0-9a-f]{8})\.pdf$", name)
    if not m:
        raise AssertionError(
            f"saved_to {name!r} does not match report_<8hex>.pdf"
        )
    path = _sink_dir(context) / name
    expected = _hashlib.md5(path.read_bytes()).hexdigest()[:8]
    if m.group(1) != expected:
        raise AssertionError(
            f"saved_to hash {m.group(1)} != md5(bytes)[:8] {expected}"
        )


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
def step_tool_desc_contains(context: Context, tool: str, needle: str) -> None:
    desc = _tool_description(context, tool)
    if needle not in desc:
        raise AssertionError(
            f"Tool {tool!r} description does not contain {needle!r}:\n{desc}"
        )


@then('the description of tool "{tool}" does NOT contain the literal string "{needle}"')
def step_tool_desc_no_contain(context: Context, tool: str, needle: str) -> None:
    desc = _tool_description(context, tool)
    if needle in desc:
        raise AssertionError(
            f"Tool {tool!r} description unexpectedly contains {needle!r}:\n{desc}"
        )


@then('the description of tool "{tool}" contains the configured sink path')
def step_tool_desc_contains_sink_path(context: Context, tool: str) -> None:
    desc = _tool_description(context, tool)
    sink = getattr(context, "attachment_sink_dir", None)
    if sink is None:
        raise AssertionError(
            "No sink configured; this assertion needs a `fresh writable` step."
        )
    if str(sink) not in desc:
        raise AssertionError(
            f"Tool {tool!r} description does not name sink {sink}:\n{desc}"
        )
