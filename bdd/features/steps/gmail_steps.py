"""Steps specific to Gmail label semantics (LIM-0002).

These steps wire the in-process mock-gmail server into the BDD harness
and provide Given/When/Then definitions for the gmail_label_semantics
feature. The mock-gmail state is seeded directly via GmailState rather
than through IMAP APPEND, giving scenarios precise control over
X-GM-MSGID, labels, and per-folder UIDs.
"""

from __future__ import annotations

import email.utils
import itertools
import json
import re

from behave import given, then, use_step_matcher, when
from behave.runner import Context

from support.imap_fixture import resolve_account
from support.policy_builder import PolicyBuilder

# ----------------------------------------------------------------- helpers


def _ensure_builder(context: Context) -> PolicyBuilder:
    from features.steps.policy_steps import _ensure_builder

    return _ensure_builder(context)


def _gmail_state(context: Context):
    """Retrieve the shared GmailState from the context."""
    state = getattr(context, "gmail_state", None)
    if state is None:
        raise AssertionError(
            "No gmail_state on context. The mock-gmail server must be "
            "started in before_all."
        )
    return state


def _last_response(context: Context) -> dict:
    response = getattr(context, "last_response", None)
    if response is None:
        raise AssertionError("No MCP tool response captured yet.")
    return response


def _build_rfc822(
    *,
    from_addr: str,
    to_addr: str = "test@bdd.local",
    subject: str = "Test",
    message_id: str | None = None,
    body: str = "Test body.\r\n",
) -> bytes:
    """Build a minimal RFC822 message as bytes."""
    from email.message import EmailMessage

    msg = EmailMessage()
    msg["From"] = from_addr
    msg["To"] = to_addr
    msg["Subject"] = subject
    if message_id is not None:
        msg["Message-ID"] = message_id
    msg["Date"] = email.utils.formatdate(localtime=False)
    msg.set_content(body)
    return msg.as_bytes()


# ---------------------------------------------------------- Background steps


@given(
    'the IMAP account "{account_id}" exists with provider '
    '"{provider}" and folders:'
)
def step_imap_account_provider_with_folders(
    context: Context, account_id: str, provider: str
) -> None:
    """Register an account with an explicit provider and create folders.

    For mock-gmail accounts (provider=google) the folders are registered
    in GmailState; for standard accounts they are created via IMAP.
    """
    builder = _ensure_builder(context)
    instance, user = resolve_account(account_id)
    host, port = context.imap_instances[instance]

    # Register the account in the policy builder with the right provider.
    if not any(a.id == account_id for a in builder.accounts):
        pwd = "test" if instance == "mock-gmail" else "test123"
        builder.add_account(
            id=account_id,
            provider=provider,
            host=host,
            port=port,
            auth_type="password",
            secret_ref=f"secret://accounts/{account_id}/password",
            password_literal=pwd,
        )

    for row in context.table:
        folder = row["folder path"]
        if instance != "mock-gmail":
            context.imap.create_folder(instance, user, folder)
        else:
            state = _gmail_state(context)
            state.create_folder(folder)

    builder.write()




# --------------------------------------------------- Scenario 1: describe_policy




# ---------------------------------------- Scenario 2: multi-label message seeding






@given('a Gmail message on "{account_id}" has:')
def step_gmail_message_on_account_has(context: Context, account_id: str) -> None:
    import email.utils
    from datetime import datetime
    from email.message import EmailMessage

    from mock_gmail.state import Message

    row = context.table[0]
    gm_msgid = int(row["x_gm_msgid"])
    message_id = row.get("message_id")
    from_addr = row.get("from_address") or row.get("from")
    if not from_addr:
        raise AssertionError("Missing mandatory 'from_address' or 'from' in Gmail message seeding table")
    to_addr = row.get("to_address") or row.get("to")
    if not to_addr:
        raise AssertionError("Missing mandatory 'to_address' or 'to' in Gmail message seeding table")
    subject = row.get("subject")
    if not subject:
        raise AssertionError("Missing mandatory 'subject' in Gmail message seeding table")
    date_str = row.get("date")
    if not date_str:
        raise AssertionError("Missing mandatory 'date' in Gmail message seeding table")

    email_msg = EmailMessage()
    email_msg["From"] = from_addr
    email_msg["To"] = to_addr
    email_msg["Subject"] = subject
    if message_id:
        email_msg["Message-ID"] = message_id
    parsed_dt = datetime.fromisoformat(date_str)
    date_header = email.utils.format_datetime(parsed_dt)
    email_msg["Date"] = date_header
    email_msg.set_content(f"Body for {subject}\r\n")

    msg = Message(
        gm_msgid=gm_msgid,
        gm_thrid=gm_msgid,
        rfc822=email_msg.as_bytes(),
        labels=set(),
        message_id=message_id,
        from_addr=from_addr,
        to_addr=to_addr,
        subject=subject,
        date=date_header or "",
    )
    if parsed_dt:
        msg.internal_date = parsed_dt
    context._last_gmail_account = account_id
    context._gmail_pending_msgs = getattr(context, "_gmail_pending_msgs", {})
    context._gmail_pending_msgs[(account_id, gm_msgid)] = msg
    context._gmail_pending_msg = msg


use_step_matcher("re")






@given(
    r'the Gmail message (?P<gm_msgid>\d+)(?: on "(?P<account_id>[^"]+)")? '
    r'carries labels (?P<labels_raw>\[[^\]]*\]) with (?P<uid_spec>.+)'
)
def step_gmail_message_account_carries_labels_with_uids(
    context: Context, gm_msgid: str, account_id: str | None, labels_raw: str, uid_spec: str
) -> None:
    msg_id = int(gm_msgid)
    if not account_id:
        account_id = getattr(context, "_last_gmail_account", None)
    if not account_id:
        raise AssertionError("No Gmail account specified and no previous account recorded in context")
    pending_msgs = getattr(context, "_gmail_pending_msgs", {})
    msg = pending_msgs.get((account_id, msg_id))
    if msg is None:
        raise AssertionError(f"No pending Gmail message with msgid {msg_id} on {account_id}")
        raise AssertionError(f"No pending Gmail message with msgid {msg_id} on {account_id}")

    state = _gmail_state(context)
    labels = json.loads(labels_raw)
    from mock_gmail.state import FOLDER_TO_LABEL
    for label in labels:
        state.create_folder(label)
        internal = FOLDER_TO_LABEL.get(label, label)
        if internal != "__ALL_MAIL__":
            msg.labels.add(internal)

    matches = re.findall(r'(?:UID\s+)?(\d+)\s+in\s+"([^"]+)"', uid_spec)
    if not matches:
        raise AssertionError(f"Cannot parse UID spec: {uid_spec!r}")

    for _, folder in matches:
        state.create_folder(folder)
        if folder != "[Gmail]/All Mail":
            internal = FOLDER_TO_LABEL.get(folder, folder)
            if internal != "__ALL_MAIL__":
                msg.labels.add(internal)

    state.add_message(msg)

    context.message_uids = getattr(context, "message_uids", {})
    for uid_str, folder in matches:
        uid = int(uid_str)
        uid_map = state._uid_maps.setdefault(folder, {})
        uid_map[msg.gm_msgid] = uid
        state._uid_counters[folder] = max(state._uid_counters.get(folder, 0), uid)
        context.message_uids[(account_id, folder, uid)] = uid
        context.message_uids[(account_id, folder, msg.gm_msgid)] = uid


use_step_matcher("parse")




# --------------------------------------------- Scenario 2: canonical_all_mail_uid




# ----------------------------------------- Scenario 3: X-GM-MSGID IMAP SEARCH


use_step_matcher("re")


@then(
    r'a direct IMAP SEARCH on "(?P<qualified>[^"]+)" for X-GM-MSGID '
    r"(?P<gm_msgid>\d+) returns (?P<count_phrase>[\w ]+?) results?"
)
def step_imap_search_xgm_msgid(
    context: Context, qualified: str, gm_msgid: str, count_phrase: str
) -> None:
    """Search for X-GM-MSGID on the mock-gmail IMAP server via direct IMAP SEARCH."""
    import imaplib

    from support.imap_fixture import resolve_account

    gm_msgid_int = int(gm_msgid)
    phrase = count_phrase.strip()
    count_map = {
        "zero": 0, "no": 0, "one": 1, "two": 2,
        "exactly one": 1, "exactly two": 2,
    }
    expected = count_map.get(phrase)
    if expected is None:
        try:
            expected = int(phrase)
        except ValueError:
            raise AssertionError(f"Cannot parse count phrase: {phrase!r}")

    account_id, _, folder = qualified.partition(":")
    instance, user = resolve_account(account_id)
    wire_folder = context.imap.to_wire(instance, user, folder)
    pwd = getattr(context.gmail_state, "password", "test")

    conn = imaplib.IMAP4("127.0.0.1", 13143)
    try:
        conn.login("test@scaratec.com", pwd)
        st, _ = conn.select(context.imap._q(wire_folder))
        if st != "OK":
            raise AssertionError(f"Direct IMAP select on {wire_folder!r} failed")
        status, data = conn.uid("SEARCH", None, "X-GM-MSGID", str(gm_msgid_int))
        if status != "OK":
            raise AssertionError(f"Direct IMAP SEARCH X-GM-MSGID failed: {data!r}")
        matching = data[0].split() if data and data[0] else []
    finally:
        try:
            conn.logout()
        except Exception:
            pass

    if len(matching) != expected:
        raise AssertionError(
            f"X-GM-MSGID {gm_msgid_int} SEARCH on {qualified!r}: "
            f"expected {expected}, got {len(matching)}: UIDs {matching!r}"
        )




use_step_matcher("parse")


# -------------------------------------------- Scenario 4: list_labels


@when('{caller_id} calls list_labels with account "{account}"')
def step_caller_calls_list_labels(
    context: Context, caller_id: str, account: str
) -> None:
    from features.steps.mcp_steps import _ensure_mcp_client, _store_result

    client = _ensure_mcp_client(context, caller_id)
    payload = client.call_tool("list_labels", {"account": account})
    _store_result(context, payload)




# ----------------------------------------- Scenario 5: cross-account






# ----------------------------------------- Scenario 6: policy-addressable system folders










# -------------------------------------------------- response uids shorthand




# ---------------------------------------- connection-count instrumentation




# ---------------------------------------- localized Gmail folders


@given("the mock-gmail server uses localized folder names:")
@given('the mock-gmail server for "{account_id}" uses localized folder names:')
def step_mock_gmail_localized_folders(context: Context, account_id: str | None = None) -> None:
    """Configure the mock-gmail to return localized folder names in LIST.

    Table columns: canonical, localized, flags.
    """
    state = _gmail_state(context)
    mapping = []
    wire_map = {}
    for row in context.table:
        mapping.append((row["canonical"], row["localized"], row["flags"]))
        wire_map[row["canonical"]] = row["localized"]
    state.set_localized_folders(mapping)
    if account_id:
        instance, user = resolve_account(account_id)
        context.imap.register_wire_folders(instance, user, wire_map)
    else:
        context.imap.register_wire_folders("mock-gmail", "test", wire_map)




@then('the response contains folder "{folder_path}"')
def step_response_contains_folder(context: Context, folder_path: str) -> None:
    response = _last_response(context)
    folders = response.get("folders") or []
    folder_names = []
    for f in folders:
        if isinstance(f, dict):
            folder_names.append(
                f.get("path") or f.get("name") or f.get("folder", "")
            )
        else:
            folder_names.append(str(f))
    if folder_path not in folder_names:
        raise AssertionError(
            f"Folder {folder_path!r} not found in response. "
            f"Available: {folder_names!r}"
        )


@then('the response does not contain folder "{folder_path}"')
def step_response_does_not_contain_folder(
    context: Context, folder_path: str
) -> None:
    response = _last_response(context)
    folders = response.get("folders")
    if not isinstance(folders, list) or len(folders) == 0:
        raise AssertionError(
            f"Expected response to contain a non-empty folders list, got {folders!r}"
        )
    folder_names = []
    for f in folders:
        if isinstance(f, dict):
            folder_names.append(
                f.get("path") or f.get("name") or f.get("folder", "")
            )
        else:
            folder_names.append(str(f))
    if folder_path in folder_names:
        raise AssertionError(
            f"Folder {folder_path!r} unexpectedly found in response. "
            f"All folders: {folder_names!r}"
        )


# ---------------------------------------- STORE-operation log / rejection hooks


@given("the mock Gmail server records all X-GM-LABELS STORE operations")
def step_mock_gmail_records_store_ops(context: Context) -> None:
    state = _gmail_state(context)
    state.record_store_operations = True
    state.store_operations.clear()


@given(
    'the mock Gmail server is configured to reject the next '
    'X-GM-LABELS STORE with status "{status}" and text "{text}"'
)
def step_mock_gmail_reject_next_store(
    context: Context, status: str, text: str
) -> None:
    state = _gmail_state(context)
    state.next_store_rejection = (status, text)


@then(
    "the recorded X-GM-LABELS STORE operations on UID {uid:d} are in order:"
)
def step_recorded_store_ops_in_order(context: Context, uid: int) -> None:
    state = _gmail_state(context)
    recorded = [
        (op, label) for (recorded_uid, op, label) in state.store_operations
        if recorded_uid == uid
    ]
    expected = [(row["op"], row["label"]) for row in context.table]
    if recorded != expected:
        raise AssertionError(
            f"X-GM-LABELS STORE ops on UID {uid}: "
            f"expected {expected!r}, got {recorded!r}"
        )


# ---------------------------------------- mock IMAP command log (ADR 0033)


@when('a direct IMAP SELECT of "{mailbox}" is issued against "{account_id}"')
def step_direct_imap_select(context: Context, mailbox: str, account_id: str) -> None:
    """Issue SELECT with the mailbox name exactly as written (wire form)."""
    from support.imap_fixture import IMAPFixture

    instance, user = resolve_account(account_id)
    conn = context.imap.connect(instance, user)
    status, _data = conn.select(IMAPFixture._q(mailbox))
    context.last_direct_imap_status = status


@then('the direct IMAP command is answered with status "{status}"')
def step_direct_imap_status(context: Context, status: str) -> None:
    actual = getattr(context, "last_direct_imap_status", None)
    if actual is None:
        raise AssertionError("No direct IMAP command has been issued in this scenario.")
    if actual != status:
        raise AssertionError(f"Direct IMAP command: expected status {status!r}, got {actual!r}")


@given("no tool has been called in this server session yet")
def step_no_tool_called_yet(context: Context) -> None:
    """Precondition: the server process has not been started, so the
    next tool call is the first of its session (no cached state)."""
    if getattr(context, "mcp", None) is not None:
        raise AssertionError("An MCP server session is already running in this scenario.")
    if getattr(context, "response_history", None):
        raise AssertionError(
            f"Tool calls already happened in this scenario: {context.response_history!r}"
        )


def _server_command_log(context: Context, account_id: str) -> list:
    """Commands the mock-gmail server received from anyone but the harness.

    The harness's own direct IMAP connection logs in as the fixture
    user of the account; everything else on the mock came from the
    server under test. An empty result is a harness error: absence
    assertions would pass vacuously.
    """
    instance, harness_user = resolve_account(account_id)
    if instance != "mock-gmail":
        raise AssertionError(
            f"Account {account_id!r} is not backed by the mock-gmail server; "
            "no IMAP command log is recorded for it."
        )
    records = [r for r in _gmail_state(context).command_log if r.user != harness_user]
    if not records:
        raise AssertionError(
            f"The mock-gmail server recorded no command from the server for {account_id!r}."
        )
    return records


def _format_log(records: list) -> str:
    return "\n".join(f"  {r.command} {r.mailbox or ''}".rstrip() for r in records)


@then(
    'the IMAP command log for "{account_id}" contains a SELECT or EXAMINE '
    'of the wire mailbox "{mailbox}"'
)
def step_command_log_has_select(context: Context, account_id: str, mailbox: str) -> None:
    records = _server_command_log(context, account_id)
    if not any(r.command in ("SELECT", "EXAMINE") and r.mailbox == mailbox for r in records):
        raise AssertionError(
            f"No SELECT/EXAMINE of {mailbox!r} in the command log:\n{_format_log(records)}"
        )


@then(
    'the IMAP command log for "{account_id}" contains no SELECT or EXAMINE '
    'of the wire mailbox "{mailbox}"'
)
def step_command_log_has_no_select(context: Context, account_id: str, mailbox: str) -> None:
    records = _server_command_log(context, account_id)
    hits = [r for r in records if r.command in ("SELECT", "EXAMINE") and r.mailbox == mailbox]
    if hits:
        raise AssertionError(
            f"Unexpected SELECT/EXAMINE of {mailbox!r} in the command log:\n"
            f"{_format_log(records)}"
        )


@then(
    'the IMAP command log for "{account_id}" contains no command addressing '
    'the mailbox "{mailbox}"'
)
def step_command_log_never_addresses(context: Context, account_id: str, mailbox: str) -> None:
    records = _server_command_log(context, account_id)
    hits = [r for r in records if r.mailbox == mailbox]
    if hits:
        raise AssertionError(
            f"Unexpected command addressing {mailbox!r} in the command log:\n"
            f"{_format_log(records)}"
        )


# ---------------------------------------- Gmail draft lifecycle (real-Gmail fidelity)


def _harness_conn(context: Context, account_id: str, mailbox: str):
    """The harness's own IMAP connection, with `mailbox` (wire form) selected.
    Messages the scenario staged are seeded first, as before a tool call."""
    from features.steps.policy_steps import flush_staged_messages
    from support.imap_fixture import IMAPFixture

    flush_staged_messages(context)
    instance, user = resolve_account(account_id)
    conn = context.imap.connect(instance, user)
    status, data = conn.select(IMAPFixture._q(mailbox))
    if status != "OK":
        raise AssertionError(f"Direct SELECT of {mailbox!r} failed: {status} {data!r}")
    return conn


@when('a direct IMAP command is issued in "{mailbox}" on "{account_id}":')
def step_direct_imap_command(context: Context, mailbox: str, account_id: str) -> None:
    """Send the DocString as one UID command, byte for byte, from the
    harness's own connection. A BAD answer is recorded as status "BAD"
    (imaplib raises on it) so the Then can assert it."""
    import imaplib

    words = (context.text or "").strip().split(None, 2)
    if len(words) < 3 or words[0].upper() != "UID":
        raise AssertionError(f"Expected 'UID <command> <arguments>', got {context.text!r}")
    conn = _harness_conn(context, account_id, mailbox)
    data: list = []
    try:
        status, data = conn.uid(words[1].upper(), words[2])
    except imaplib.IMAP4.error as exc:
        if "BAD" not in str(exc) and "command error" not in str(exc):
            raise
        status = "BAD"
    context.last_direct_imap_status = status
    context.last_direct_imap_command = (account_id, mailbox, words[1].upper(), data)


def _direct_fetch_items(context: Context, qualified: str, uid: int) -> str:
    account_id, sep, mailbox = qualified.partition(":")
    if not sep:
        raise AssertionError(f'Expected an "account:folder" qualified name, got {qualified!r}')
    conn = _harness_conn(context, account_id, mailbox)
    status, data = conn.uid("FETCH", str(uid), "(FLAGS X-GM-LABELS)")
    frames = [
        (item if isinstance(item, bytes) else b"").decode("utf-8", errors="replace")
        for item in (data or [])
    ]
    text = " ".join(frame for frame in frames if frame)
    if status != "OK" or f"UID {uid}" not in text:
        raise AssertionError(f"Direct FETCH of uid {uid} in {qualified!r} returned {data!r}")
    return text


@then('a direct IMAP FETCH of uid {uid:d} in "{qualified}" shows the flag "{flag}"')
def step_direct_fetch_shows_flag(context: Context, uid: int, qualified: str, flag: str) -> None:
    text = _direct_fetch_items(context, qualified, uid)
    match = re.search(r"FLAGS \(([^)]*)\)", text)
    flags = match.group(1).split() if match else []
    if flag not in flags:
        raise AssertionError(f"uid {uid} in {qualified!r}: flag {flag!r} not in {flags!r}")


@then('a direct IMAP FETCH of uid {uid:d} in "{qualified}" does NOT show the flag "{flag}"')
def step_direct_fetch_not_flag(context: Context, uid: int, qualified: str, flag: str) -> None:
    text = _direct_fetch_items(context, qualified, uid)
    match = re.search(r"FLAGS \(([^)]*)\)", text)
    flags = match.group(1).split() if match else []
    if flag in flags:
        raise AssertionError(f"uid {uid} in {qualified!r}: unexpected flag {flag!r} in {flags!r}")


@then('a direct IMAP FETCH of uid {uid:d} in "{qualified}" shows X-GM-LABELS "{labels}"')
def step_direct_fetch_shows_labels(
    context: Context, uid: int, qualified: str, labels: str
) -> None:
    text = _direct_fetch_items(context, qualified, uid)
    match = re.search(r"X-GM-LABELS (\([^)]*\))", text)
    actual = match.group(1) if match else None
    if actual != labels:
        raise AssertionError(
            f"uid {uid} in {qualified!r}: expected X-GM-LABELS {labels}, got {actual!r}"
        )


@when(
    'a fresh direct IMAP connection to "{account_id}" asks for CAPABILITY '
    "before and after LOGIN"
)
def step_capability_before_after_login(context: Context, account_id: str) -> None:
    import imaplib

    from support.imap_fixture import TEST_PASSWORD

    instance, user = resolve_account(account_id)
    host, port = context.imap_instances[instance]
    conn = imaplib.IMAP4(host, port)
    pwd = "test" if instance == "mock-gmail" else TEST_PASSWORD
    try:
        context.capability_before_login = {c.upper() for c in conn.capabilities}
        conn.login(user, pwd)
        status, data = conn.capability()
        if status != "OK" or not data:
            raise AssertionError(f"CAPABILITY after LOGIN failed: {status} {data!r}")
        context.capability_after_login = {
            c.upper() for c in data[0].decode("ascii", errors="replace").split()
        }
    finally:
        try:
            conn.logout()
        except (imaplib.IMAP4.error, OSError):
            pass


@then('the CAPABILITY before LOGIN lists neither "{first}" nor "{second}"')
def step_capability_before_lacks(context: Context, first: str, second: str) -> None:
    present = {first, second} & context.capability_before_login
    if present:
        raise AssertionError(
            f"Pre-authentication CAPABILITY lists {sorted(present)!r}: "
            f"{sorted(context.capability_before_login)!r}"
        )


@then('the CAPABILITY after LOGIN lists both "{first}" and "{second}"')
def step_capability_after_has(context: Context, first: str, second: str) -> None:
    missing = {first, second} - context.capability_after_login
    if missing:
        raise AssertionError(
            f"Post-authentication CAPABILITY lacks {sorted(missing)!r}: "
            f"{sorted(context.capability_after_login)!r}"
        )


@then('the message carrying the returned draft_id in "{qualified}" has the headers:')
def step_message_with_handle_has_headers(context: Context, qualified: str) -> None:
    """Second channel: SEARCH the mock for the handle, FETCH the one hit
    and compare decoded header values."""
    from email.header import decode_header, make_header

    draft_id = getattr(context, "last_draft_id", None)
    if not draft_id:
        raise AssertionError("No draft_id has been returned in this scenario yet.")
    account_id, _, folder = qualified.partition(":")
    instance, user = resolve_account(account_id)
    hits = context.imap.search_header(instance, user, folder, "X-SC-Draft-Id", draft_id)
    if len(hits) != 1:
        raise AssertionError(
            f"Expected exactly one message carrying {draft_id!r} in {qualified!r}, "
            f"found UIDs {hits!r}"
        )
    message = context.imap.fetch_message(instance, user, folder, hits[0])
    for row in context.table:
        raw = message.get(row["header"])
        actual = None if raw is None else str(make_header(decode_header(raw))).strip()
        if actual != row["value"]:
            raise AssertionError(
                f"{qualified!r} uid {hits[0]}: header {row['header']!r} is {actual!r}, "
                f"expected {row['value']!r}"
            )


def _server_label_stores(context: Context, account_id: str) -> list:
    """X-GM-LABELS STOREs the mock received from the server under test
    (anyone but the harness's own login), with the mock's parse result."""
    instance, harness_user = resolve_account(account_id)
    if instance != "mock-gmail":
        raise AssertionError(f"Account {account_id!r} is not backed by the mock-gmail server.")
    records = [r for r in _gmail_state(context).label_store_log if r.user != harness_user]
    if not records:
        raise AssertionError(
            f"The mock-gmail server received no X-GM-LABELS STORE from the server "
            f"for {account_id!r}."
        )
    return records


def _format_label_stores(records: list) -> str:
    return "\n".join(
        f"  {r.op}X-GM-LABELS {r.raw} -> {r.status} parsed={r.labels!r}" for r in records
    )


@then(
    "the mock-gmail server answered every X-GM-LABELS STORE from the server "
    'for "{account_id}" with status "{status}"'
)
def step_label_stores_all_status(context: Context, account_id: str, status: str) -> None:
    records = _server_label_stores(context, account_id)
    bad = [r for r in records if r.status != status]
    if bad:
        raise AssertionError(
            f"X-GM-LABELS STOREs not answered {status}:\n{_format_label_stores(records)}"
        )


@then(
    'the mock-gmail server received the label "{label}" in an X-GM-LABELS STORE '
    '"{op}" from the server for "{account_id}"'
)
def step_label_store_received(context: Context, label: str, op: str, account_id: str) -> None:
    records = _server_label_stores(context, account_id)
    if not any(r.op == op and r.labels is not None and label in r.labels for r in records):
        raise AssertionError(
            f"No parsed X-GM-LABELS STORE {op}{label!r}:\n{_format_label_stores(records)}"
        )


@then('the accounts entry for "{account_id}" contains field "{field}" equal to "{expected}"')
def step_accounts_entry_field_equal(
    context: Context, account_id: str, field: str, expected: str
) -> None:
    resp = _last_response(context)
    accounts = resp.get("accounts", [])
    entry = next((a for a in accounts if a.get("id") == account_id), None)
    if entry is None:
        raise AssertionError(f"Account {account_id!r} not found in accounts: {accounts!r}")
    actual = entry.get(field)
    if actual != expected:
        raise AssertionError(f"Account {account_id!r} field {field!r}: expected {expected!r}, got {actual!r}")


@then('message {index:d} has field "{field}" matching "{pattern}"')
def step_message_field_matching(
    context: Context, index: int, field: str, pattern: str
) -> None:
    resp = _last_response(context)
    messages = resp.get("messages", [])
    if index >= len(messages):
        raise AssertionError(f"Message index {index} out of range (total: {len(messages)})")
    msg = messages[index]
    actual = msg.get(field)
    if actual is None:
        raise AssertionError(f"Message {index} has no field {field!r}: {msg!r}")
    if not re.search(pattern, str(actual)):
        raise AssertionError(f"Message {index} field {field!r}={actual!r} does not match {pattern!r}")


@then('the value of message {index:d} field "{field}" is remembered as "{key}"')
def step_remember_message_field(
    context: Context, index: int, field: str, key: str
) -> None:
    resp = _last_response(context)
    messages = resp.get("messages", [])
    if index >= len(messages):
        raise AssertionError(f"Message index {index} out of range (total: {len(messages)})")
    val = messages[index].get(field)
    context.remembered_values = getattr(context, "remembered_values", {})
    context.remembered_values[key] = val




@then('the labels response includes {labels_desc}')
def step_labels_response_includes(context: Context, labels_desc: str) -> None:
    resp = _last_response(context)
    expected_labels = re.findall(r'"([^"]+)"', labels_desc)
    actual_labels = resp.get("labels", [])
    for exp in expected_labels:
        if exp not in actual_labels:
            raise AssertionError(f"Label {exp!r} not in labels response: {actual_labels!r}")


@then(
    'the mock-gmail command log for "{account_id}" shows an '
    'RFC822 UID FETCH in "{f1}" for UID {uid:d} '
    'and no RFC822 UID FETCH in "{f2}"'
)
def step_mock_gmail_command_log_rfc822_fetch(
    context: Context, account_id: str, f1: str, f2: str, uid: int
) -> None:
    records = _server_command_log(context, account_id)
    current_mailbox = None
    fetch_mailboxes = []
    target_uid = str(uid)
    for r in records:
        if r.command in ("SELECT", "EXAMINE") and r.mailbox:
            current_mailbox = r.mailbox
        elif r.command == "UID FETCH" and "RFC822" in r.args and target_uid in r.args:
            fetch_mailboxes.append(current_mailbox)

    if f1 not in fetch_mailboxes:
        raise AssertionError(
            f"Expected RFC822 UID FETCH for UID {uid} in {f1!r}, but found in: {fetch_mailboxes!r}. All records: {[(r.command, r.mailbox, r.args) for r in records]}"
        )
    if f2 in fetch_mailboxes:
        raise AssertionError(
            f"Expected NO RFC822 UID FETCH for UID {uid} in {f2!r}, but found it! All records: {[(r.command, r.mailbox, r.args) for r in records]}"
        )


@given(
    '{caller_id} creates a draft in "{qualified}" '
    'with subject "{subject}" and body "{body}"'
)
def step_caller_creates_draft_with_subject_body(
    context: Context, caller_id: str, qualified: str, subject: str, body: str
) -> None:
    from features.steps.mcp_steps import _ensure_mcp_client, _store_result

    account_id, _, folder = qualified.partition(":")
    client = _ensure_mcp_client(context, caller_id)
    payload = client.call_tool(
        "create_draft",
        {
            "account": account_id,
            "folder": folder,
            "subject": subject,
            "body_text": body,
        },
    )
    _store_result(context, payload)


use_step_matcher("re")


@given(
    r'an interrupted revision leaves both authentic revision 1 '
    r'\("(?P<sub1>[^"]+)"\) and authentic revision 2 '
    r'\("(?P<sub2>[^"]+)"\) for that draft_id in '
    r'"(?P<account_id>[^:]+):(?P<folder>[^"]+)" with no active WAL entry'
)
def step_interrupted_revision_leaves_two_authentic_revisions(
    context: Context, sub1: str, sub2: str, account_id: str, folder: str
) -> None:
    from features.steps.mcp_steps import _ensure_mcp_client, _store_result
    from support.imap_fixture import resolve_account

    draft_id = getattr(context, "last_draft_id", None)
    if not draft_id:
        raise AssertionError("No draft_id available to revise")

    instance, user = resolve_account(account_id)
    wire_folder = context.imap.to_wire(instance, user, folder)
    hits = context.imap.search_header(instance, user, wire_folder, "X-SC-Draft-Id", draft_id)
    if not hits:
        raise AssertionError(f"No draft found with X-SC-Draft-Id={draft_id} in {folder}")
    rev1_uid = hits[0]
    rev1_raw = context.imap.fetch_raw(instance, user, wire_folder, rev1_uid)

    caller = next(iter(context.policy_builder.callers), None) if getattr(context, "policy_builder", None) else None
    caller_id = getattr(context, "last_caller_id", None) or getattr(context, "mcp_caller_id", None) or (caller.id if caller else None)
    if not caller_id:
        raise AssertionError("No caller configured in scenario")
    client = _ensure_mcp_client(context, caller_id)
    payload = client.call_tool(
        "update_draft",
        {
            "account": account_id,
            "folder": folder,
            "draft_id": draft_id,
            "base_revision": 1,
            "changes": {"subject": sub2},
        },
    )
    _store_result(context, payload)

    context.imap.append_verbatim(
        instance, user, wire_folder, rev1_raw, flags=(r"\Draft", r"\Seen")
    )


use_step_matcher("parse")


# ---------------------------------------- Gmail search, structure and counts (mock fidelity)


def _seeded_uid(context: Context, account_id: str, folder: str, hint: int) -> int:
    """The UID the IMAP server assigned to the message seeded under `hint`.
    A hint the scenario never seeded is a harness error, not a UID."""
    uids = getattr(context, "message_uids", {})
    if (account_id, folder, hint) not in uids:
        raise AssertionError(
            f"No message was seeded as {hint} in {account_id}:{folder}; seeded: "
            f"{sorted(k[2] for k in uids if k[:2] == (account_id, folder))!r}"
        )
    return uids[(account_id, folder, hint)]


@then("the direct IMAP SEARCH answer lists exactly the messages {hints_raw}")
def step_direct_search_lists_messages(context: Context, hints_raw: str) -> None:
    command = getattr(context, "last_direct_imap_command", None)
    if command is None or command[2] != "SEARCH":
        raise AssertionError("No direct IMAP UID SEARCH has been issued in this scenario.")
    if context.last_direct_imap_status != "OK":
        raise AssertionError(
            f"The direct UID SEARCH was answered {context.last_direct_imap_status!r}."
        )
    account_id, mailbox, _verb, data = command
    expected = sorted(
        _seeded_uid(context, account_id, mailbox, hint) for hint in json.loads(hints_raw)
    )
    raw = b" ".join(item for item in data if isinstance(item, bytes))
    actual = sorted(int(token) for token in raw.split())
    if actual != expected:
        raise AssertionError(
            f"Direct UID SEARCH in {account_id}:{mailbox}: expected UIDs {expected!r} "
            f"(messages {hints_raw}), got {actual!r}"
        )


def _parse_imap_list(text: str, start: int) -> tuple[list, int]:
    """Parse one parenthesised IMAP list starting at `text[start] == "("`.
    Atoms stay strings, NIL becomes None, quoted strings are unescaped."""
    items: list = []
    i = start + 1
    while i < len(text):
        ch = text[i]
        if ch == " ":
            i += 1
        elif ch == "(":
            inner, i = _parse_imap_list(text, i)
            items.append(inner)
        elif ch == ")":
            return items, i + 1
        elif ch == '"':
            out = []
            i += 1
            while text[i] != '"':
                if text[i] == "\\":
                    i += 1
                out.append(text[i])
                i += 1
            items.append("".join(out))
            i += 1
        else:
            j = i
            while j < len(text) and text[j] not in ' ()"':
                j += 1
            atom = text[i:j]
            items.append(None if atom.upper() == "NIL" else atom)
            i = j
    raise AssertionError(f"Unbalanced IMAP list in {text!r}")


def _leaf_parts(body: list, prefix: str = "") -> list[tuple[str, list]]:
    """(section number, leaf body) for every non-multipart part (RFC 3501 §6.4.5)."""
    if body and isinstance(body[0], list):
        leaves: list[tuple[str, list]] = []
        children = itertools.takewhile(lambda item: isinstance(item, list), body)
        for index, item in enumerate(children, start=1):
            leaves.extend(_leaf_parts(item, f"{prefix}{index}."))
        return leaves
    return [(prefix.rstrip(".") or "1", body)]


def _leaf_disposition(leaf: list) -> tuple[str, str]:
    """(disposition type, filename) of a leaf body; empty strings for none."""
    maintype = str(leaf[0]).lower()
    subtype = str(leaf[1]).lower()
    index = 9
    if maintype == "message" and subtype == "rfc822":
        index = 11
    elif maintype != "text":
        index = 8
    disposition = leaf[index] if len(leaf) > index else None
    if not isinstance(disposition, list) or not disposition:
        return "", ""
    params = disposition[1] if len(disposition) > 1 and isinstance(disposition[1], list) else []
    pairs = dict(zip(params[::2], params[1::2]))
    filename = next((v for k, v in pairs.items() if str(k).lower() == "filename"), "")
    return str(disposition[0]).lower(), filename or ""


@then(
    'a direct IMAP FETCH of the BODYSTRUCTURE of message {hint:d} in "{qualified}" '
    'shows a {content_type} body with the parts:'
)
def step_direct_bodystructure_parts(
    context: Context, hint: int, qualified: str, content_type: str
) -> None:
    account_id, sep, mailbox = qualified.partition(":")
    if not sep:
        raise AssertionError(f'Expected an "account:folder" qualified name, got {qualified!r}')
    conn = _harness_conn(context, account_id, mailbox)
    uid = _seeded_uid(context, account_id, mailbox, hint)
    status, data = conn.uid("FETCH", str(uid), "(BODYSTRUCTURE)")
    text = " ".join(item.decode("utf-8", "replace") for item in data or [] if isinstance(item, bytes))
    marker = text.find("BODYSTRUCTURE (")
    if status != "OK" or marker < 0:
        raise AssertionError(f"Direct FETCH BODYSTRUCTURE of uid {uid} returned {data!r}")
    body, _end = _parse_imap_list(text, marker + len("BODYSTRUCTURE "))
    if body and isinstance(body[0], list):
        subtype = next(item for item in body if not isinstance(item, list))
        actual_type = f"multipart/{str(subtype).lower()}"
    else:
        actual_type = f"{str(body[0]).lower()}/{str(body[1]).lower()}"
    if actual_type != content_type:
        raise AssertionError(f"uid {uid}: BODYSTRUCTURE is {actual_type}, not {content_type}: {text}")
    actual_parts = []
    for section, leaf in _leaf_parts(body):
        disposition, filename = _leaf_disposition(leaf)
        actual_parts.append(
            {
                "part": section,
                "type": f"{str(leaf[0]).lower()}/{str(leaf[1]).lower()}",
                "disposition": disposition,
                "filename": filename,
            }
        )
    expected_parts = [
        {key: row[key] for key in ("part", "type", "disposition", "filename")}
        for row in context.table
    ]
    if actual_parts != expected_parts:
        raise AssertionError(
            f"uid {uid}: BODYSTRUCTURE parts {actual_parts!r}, expected {expected_parts!r}"
        )


@then('a direct IMAP STATUS of "{mailbox}" on "{account_id}" reports MESSAGES {count:d}')
def step_direct_status_messages(
    context: Context, mailbox: str, account_id: str, count: int
) -> None:
    import imaplib

    from features.steps.policy_steps import flush_staged_messages
    from support.imap_fixture import IMAPFixture

    flush_staged_messages(context)
    instance, user = resolve_account(account_id)
    conn = context.imap.connect(instance, user)
    try:
        status, data = conn.status(IMAPFixture._q(mailbox), "(MESSAGES)")
    except imaplib.IMAP4.error as exc:
        raise AssertionError(f"Direct STATUS of {mailbox!r} was refused: {exc}") from exc
    text = " ".join(item.decode("utf-8", "replace") for item in data or [] if isinstance(item, bytes))
    match = re.search(r"\bMESSAGES (\d+)", text)
    if status != "OK" or match is None:
        raise AssertionError(f"Direct STATUS of {mailbox!r} returned {status} {data!r}")
    if int(match.group(1)) != count:
        raise AssertionError(
            f"Direct STATUS of {mailbox!r} on {account_id!r} reports MESSAGES "
            f"{match.group(1)}, expected {count}"
        )
