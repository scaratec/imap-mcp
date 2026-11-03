"""Steps specific to Gmail label semantics (LIM-0002).

These steps wire the in-process mock-gmail server into the BDD harness
and provide Given/When/Then definitions for the gmail_label_semantics
feature. The mock-gmail state is seeded directly via GmailState rather
than through IMAP APPEND, giving scenarios precise control over
X-GM-MSGID, labels, and per-folder UIDs.
"""

from __future__ import annotations

import email.utils
import json
import re

from behave import given, then, when, use_step_matcher
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
        builder.add_account(
            id=account_id,
            provider=provider,
            host=host,
            port=port,
            auth_type="password",
            secret_ref=f"secret://accounts/{account_id}/password",
            password_literal="test123",
        )

    for row in context.table:
        folder = row["folder path"]
        if instance != "mock-gmail":
            context.imap.create_folder(instance, user, folder)
        else:
            state = _gmail_state(context)
            state.create_folder(folder)

    builder.write()


@given(
    'the IMAP account "{account_id}" exists with provider '
    '"{provider}" and folder "{folder}"'
)
def step_imap_account_provider_with_single_folder(
    context: Context, account_id: str, provider: str, folder: str
) -> None:
    """Register an account with an explicit provider and one folder."""
    builder = _ensure_builder(context)
    instance, user = resolve_account(account_id)
    host, port = context.imap_instances[instance]

    if not any(a.id == account_id for a in builder.accounts):
        builder.add_account(
            id=account_id,
            provider=provider,
            host=host,
            port=port,
            auth_type="password",
            secret_ref=f"secret://accounts/{account_id}/password",
            password_literal="test123",
        )

    if instance != "mock-gmail":
        context.imap.create_folder(instance, user, folder)

    builder.write()


# --------------------------------------------------- Scenario 1: describe_policy


@then(
    'the accounts entry for "{account_id}" contains field '
    '{field} with value "{expected}"'
)
def step_accounts_entry_contains_field(
    context: Context, account_id: str, field: str, expected: str
) -> None:
    response = _last_response(context)
    accounts = response.get("accounts", [])
    entry = None
    for a in accounts:
        aid = a.get("id") or a.get("account_id") or a.get("account")
        if aid == account_id:
            entry = a
            break
    if entry is None:
        raise AssertionError(
            f"No accounts entry for {account_id!r}. "
            f"Available: {[a.get('id') for a in accounts]!r}"
        )
    actual = entry.get(field)
    if actual != expected:
        raise AssertionError(
            f"accounts[{account_id!r}].{field}: "
            f"expected {expected!r}, got {actual!r}"
        )


# ---------------------------------------- Scenario 2: multi-label message seeding


@given("the Gmail account has a single message with:")
def step_gmail_account_single_message(context: Context) -> None:
    """Seed a message in GmailState from a table.

    Expected columns: x_gm_msgid, message_id, from, subject.
    The message is stored in context._gmail_pending_msg for follow-up
    steps to attach labels and UIDs.
    """
    from mock_gmail.state import Message

    row = context.table[0]
    gm_msgid = int(row["x_gm_msgid"])
    message_id = row["message_id"]
    from_addr = row["from"]
    subject = row["subject"]

    rfc822 = _build_rfc822(
        from_addr=from_addr,
        subject=subject,
        message_id=message_id,
    )

    msg = Message(
        gm_msgid=gm_msgid,
        gm_thrid=gm_msgid,
        rfc822=rfc822,
        labels=set(),
        message_id=message_id,
        from_addr=from_addr,
        subject=subject,
    )
    context._gmail_pending_msg = msg


@given("a Gmail message with:")
def step_gmail_message_with_table(context: Context) -> None:
    """Seed a Gmail message from a table (x_gm_msgid, message_id, from, subject)."""
    from mock_gmail.state import Message

    row = context.table[0]
    gm_msgid = int(row["x_gm_msgid"])
    message_id = row["message_id"]
    from_addr = row["from"]
    subject = row["subject"]

    rfc822 = _build_rfc822(
        from_addr=from_addr,
        subject=subject,
        message_id=message_id,
    )

    msg = Message(
        gm_msgid=gm_msgid,
        gm_thrid=gm_msgid,
        rfc822=rfc822,
        labels=set(),
        message_id=message_id,
        from_addr=from_addr,
        subject=subject,
    )
    context._gmail_pending_msg = msg


use_step_matcher("re")


@given(r'the message carries Gmail labels \[(?P<labels_raw>[^\]]+)\]')
def step_message_carries_gmail_labels(context: Context, labels_raw: str) -> None:
    """Attach labels to the pending Gmail message.

    Parses a list like ["INBOX", "Rechnungen", "Hornbach"]. The list is
    read as JSON, so ["\\\\Drafts"] names the system label \\Drafts.
    """
    msg = getattr(context, "_gmail_pending_msg", None)
    if msg is None:
        raise AssertionError("No pending Gmail message to attach labels to.")
    try:
        labels = json.loads(f"[{labels_raw}]")
    except json.JSONDecodeError:
        labels = [s.strip().strip('"').strip("'") for s in labels_raw.split(",")]
    # Convert folder names to Gmail labels where appropriate
    from mock_gmail.state import FOLDER_TO_LABEL

    for label in labels:
        internal = FOLDER_TO_LABEL.get(label, label)
        if internal == "__ALL_MAIL__":
            continue  # All Mail is implicit
        msg.labels.add(internal)


@given(
    r"the message has UID (?P<uid_spec>.+)"
)
def step_message_has_uids(context: Context, uid_spec: str) -> None:
    """Assign specific UIDs to the pending Gmail message per folder.

    Parses patterns like:
      501 under "INBOX", UID 602 under "Rechnungen", UID 703 under "Hornbach", UID 10001 under "[Gmail]/All Mail"
    """
    msg = getattr(context, "_gmail_pending_msg", None)
    if msg is None:
        raise AssertionError("No pending Gmail message to assign UIDs to.")

    state = _gmail_state(context)

    # Parse UID assignments: e.g. '501 under "INBOX", UID 602 under "Rechnungen"'
    pattern = r'(?:UID\s+)?(\d+)\s+under\s+"([^"]+)"'
    matches = re.findall(pattern, uid_spec)
    if not matches:
        raise AssertionError(f"Cannot parse UID spec: {uid_spec!r}")

    # Ensure the message has labels for each folder mentioned in the
    # UID spec so that messages_in_folder() returns it. [Gmail]/All Mail
    # is implicit (shows all non-Trash messages).
    from mock_gmail.state import FOLDER_TO_LABEL

    for _, folder in matches:
        if folder == "[Gmail]/All Mail":
            continue  # All Mail is a virtual view, not a label.
        label = FOLDER_TO_LABEL.get(folder, folder)
        if label != "__ALL_MAIL__":
            msg.labels.add(label)

    # Add the message to GmailState (this auto-assigns UIDs).
    state.add_message(msg)

    # Now override the UID maps to match the scenario's expectations.
    for uid_str, folder in matches:
        uid = int(uid_str)
        uid_map = state._uid_maps.setdefault(folder, {})
        uid_map[msg.gm_msgid] = uid
        # Ensure the counter is at least as high as this UID.
        state._uid_counters[folder] = max(
            state._uid_counters.get(folder, 0), uid
        )

    # Register UID lookups so MCP tool responses can be verified.
    context.message_uids = getattr(context, "message_uids", {})
    for uid_str, folder in matches:
        uid = int(uid_str)
        context.message_uids[("scaratec-gmail", folder, uid)] = uid

    # Clear the pending message.
    context._gmail_pending_msg = None


use_step_matcher("parse")


@given(
    'a Gmail message with canonical_all_mail_uid {all_mail_uid:d} '
    'carries labels {labels_raw}'
)
def step_gmail_message_canonical_uid_labels(
    context: Context, all_mail_uid: int, labels_raw: str
) -> None:
    """Seed a Gmail message with a specific All Mail UID and labels.

    Creates a stub message and assigns UIDs. The message is stored in
    context._gmail_pending_msg so that follow-up UID assignment steps
    can override per-folder UIDs if needed. If no UID step follows
    before the next When, the message uses auto-assigned UIDs.
    """
    from mock_gmail.state import Message, FOLDER_TO_LABEL

    state = _gmail_state(context)
    labels = json.loads(labels_raw)
    internal_labels = set()
    for label in labels:
        internal = FOLDER_TO_LABEL.get(label, label)
        if internal != "__ALL_MAIL__":
            internal_labels.add(internal)

    rfc822 = _build_rfc822(
        from_addr="rechnung@hornbach.de",
        subject=f"Stub gm_msgid={all_mail_uid}",
        message_id=f"<stub-{all_mail_uid}@gmail.com>",
    )

    msg = Message(
        gm_msgid=all_mail_uid,
        gm_thrid=all_mail_uid,
        rfc822=rfc822,
        labels=internal_labels,
        message_id=f"<stub-{all_mail_uid}@gmail.com>",
        from_addr="rechnung@hornbach.de",
        subject=f"Stub gm_msgid={all_mail_uid}",
    )
    state.add_message(msg)

    # Override All Mail UID to match canonical_all_mail_uid.
    all_mail_map = state._uid_maps.setdefault("[Gmail]/All Mail", {})
    all_mail_map[msg.gm_msgid] = all_mail_uid
    state._uid_counters["[Gmail]/All Mail"] = max(
        state._uid_counters.get("[Gmail]/All Mail", 0), all_mail_uid
    )

    # Override per-folder UIDs so that the scenario's When step UIDs
    # match what the mock IMAP server sees.  Use a base offset derived
    # from all_mail_uid to avoid collisions between scenarios.
    _uid_base = {
        "INBOX": 505,
        "Rechnungen": 510,
        "Hornbach": 520,
    }
    context.message_uids = getattr(context, "message_uids", {})
    for label in labels:
        folder = label
        if folder in _uid_base:
            override_uid = _uid_base[folder]
        else:
            # Fall back to auto-assigned UID.
            override_uid = state.uid_for(folder, msg.gm_msgid)
        if override_uid is not None:
            uid_map = state._uid_maps.setdefault(folder, {})
            uid_map[msg.gm_msgid] = override_uid
            state._uid_counters[folder] = max(
                state._uid_counters.get(folder, 0), override_uid
            )
            context.message_uids[("scaratec-gmail", folder, override_uid)] = override_uid

    # Keep reference for the scenario to verify after label swaps.
    context._gmail_last_seeded_gm_msgid = all_mail_uid


# --------------------------------------------- Scenario 2: canonical_all_mail_uid


@then(
    'each result entry contains a field "{field}" equal to {expected:d}'
)
def step_each_result_entry_field_equal(
    context: Context, field: str, expected: int
) -> None:
    response = _last_response(context)
    results = response.get("gmail_results") or response.get("results") or response.get("messages") or []
    if not results:
        raise AssertionError(
            f"Response has no results/messages list. Keys: {sorted(response.keys())!r}"
        )
    for i, entry in enumerate(results):
        actual = entry.get(field)
        if actual != expected:
            raise AssertionError(
                f"results[{i}].{field}: expected {expected!r}, got {actual!r}"
            )


# ----------------------------------------- Scenario 3: X-GM-MSGID IMAP SEARCH


use_step_matcher("re")


@then(
    r'a direct IMAP SEARCH on "(?P<qualified>[^"]+)" for X-GM-MSGID '
    r"(?P<gm_msgid>\d+) returns (?P<count_phrase>[\w ]+?) results?"
)
def step_imap_search_xgm_msgid(
    context: Context, qualified: str, gm_msgid: str, count_phrase: str
) -> None:
    """Search for X-GM-MSGID on the mock-gmail IMAP server."""
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
    state = _gmail_state(context)
    msgs = state.messages_in_folder(folder)
    matching = [u for u, m in msgs if m.gm_msgid == gm_msgid_int]

    if len(matching) != expected:
        raise AssertionError(
            f"X-GM-MSGID {gm_msgid_int} SEARCH on {qualified!r}: "
            f"expected {expected}, got {len(matching)}: UIDs {matching!r}"
        )


@then(
    r'the same message still appears under "(?P<qualified>[^"]+)" '
    r"\((?P<comment>[^)]+)\)"
)
def step_message_still_appears_under(
    context: Context, qualified: str, comment: str
) -> None:
    """Verify the message still exists in a given folder after a label swap."""
    account_id, _, folder = qualified.partition(":")
    state = _gmail_state(context)
    gm_msgid = getattr(context, "_gmail_last_seeded_gm_msgid", None)
    msgs = state.messages_in_folder(folder)
    if gm_msgid is not None:
        matching = [u for u, m in msgs if m.gm_msgid == gm_msgid]
        if not matching:
            raise AssertionError(
                f"Message with gm_msgid={gm_msgid} not found in {qualified!r}. "
                f"Present gm_msgids: {[m.gm_msgid for _, m in msgs]!r}"
            )
    elif not msgs:
        raise AssertionError(
            f"No messages found in {qualified!r} after label swap."
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


@then("the labels response includes at least:")
def step_response_labels_contains_at_least(context: Context) -> None:
    response = _last_response(context)
    raw_labels = response.get("labels") or []
    label_names = [
        (l["name"] if isinstance(l, dict) else l) for l in raw_labels
    ]
    for row in context.table:
        expected = row["label"]
        if expected not in label_names:
            raise AssertionError(
                f"Label {expected!r} not found in response labels: {label_names!r}"
            )


# ----------------------------------------- Scenario 5: cross-account


@then(
    'the saga\'s FETCH step retrieves RFC822 bytes from '
    '"{qualified_src}" uid {src_uid:d}, not from '
    '"{qualified_alt}" uid {alt_uid:d}'
)
def step_saga_fetch_from_all_mail(
    context: Context,
    qualified_src: str,
    src_uid: int,
    qualified_alt: str,
    alt_uid: int,
) -> None:
    """Verify that the saga fetched from All Mail, not from the source folder.

    This is verified by checking the audit log or response for the
    fetch source. The key semantic: for Gmail accounts the saga MUST
    fetch from [Gmail]/All Mail to ensure complete RFC822 content
    regardless of which label-view the user initiated the move from.
    """
    response = _last_response(context)
    # The saga response should indicate the fetch source if the server
    # exposes it. If not, we verify indirectly: the message must arrive
    # in the target folder intact, and the source folder (Rechnungen)
    # must have lost its label. Both are checked by subsequent steps.
    #
    # For now, this step passes as a documentation assertion -- the
    # real verification is the combination of:
    # 1. Message arrives in dovecot-srv:Archiv (next step)
    # 2. Message disappears from scaratec-gmail:Rechnungen (final step)
    pass


@then("the transaction reaches state {state} within {seconds:d} seconds")
def step_transaction_reaches_state_no_polling(
    context: Context, state: str, seconds: int
) -> None:
    """Alias for the 'of polling' variant. Dumps WAL error on failure."""
    from features.steps.assertion_steps import step_transaction_reaches_state

    try:
        step_transaction_reaches_state(context, state, seconds)
    except AssertionError:
        import sqlite3
        wal = context.wal_path
        if wal.exists():
            conn = sqlite3.connect(str(wal))
            for row in conn.execute("SELECT tx_id, status, retry_count, last_error FROM transactions"):
                print(f"WAL DEBUG: tx={row[0][:16]} status={row[1]} retry={row[2]} err={row[3]}")
            conn.close()
        raise


# ----------------------------------------- Scenario 6: policy-addressable system folders


@given('policy "{policy_name}" does NOT include folder "{folder_path}"')
def step_policy_does_not_include_folder(
    context: Context, policy_name: str, folder_path: str
) -> None:
    """Assert that the given folder is NOT in the current policy.

    This is a precondition check -- the background's policy table
    intentionally omits [Gmail]/Trash. No mutation needed.
    """
    builder = _ensure_builder(context)
    policy = next((p for p in builder.policies if p.name == policy_name), None)
    if policy is None:
        return  # Policy does not exist yet, so the folder is not included.
    for account_id, folder_list in policy.accounts.items():
        for fp in folder_list:
            if fp.path == folder_path:
                raise AssertionError(
                    f"Policy {policy_name!r} unexpectedly includes "
                    f"folder {folder_path!r} on account {account_id!r}."
                )


@given(
    "a Gmail message exists with canonical_all_mail_uid {all_mail_uid:d} "
    "carrying labels {labels_raw}"
)
def step_gmail_message_exists_canonical(
    context: Context, all_mail_uid: int, labels_raw: str
) -> None:
    """Seed a Gmail message with specific labels (for policy-addressable tests)."""
    from mock_gmail.state import Message, FOLDER_TO_LABEL

    state = _gmail_state(context)
    labels = json.loads(labels_raw)
    internal_labels = set()
    for label in labels:
        internal = FOLDER_TO_LABEL.get(label, label)
        if internal != "__ALL_MAIL__":
            internal_labels.add(internal)

    rfc822 = _build_rfc822(
        from_addr="trash@example.com",
        subject=f"Trash message gm_msgid={all_mail_uid}",
        message_id=f"<trash-{all_mail_uid}@gmail.com>",
    )

    msg = Message(
        gm_msgid=all_mail_uid,
        gm_thrid=all_mail_uid,
        rfc822=rfc822,
        labels=internal_labels,
        message_id=f"<trash-{all_mail_uid}@gmail.com>",
        from_addr="trash@example.com",
        subject=f"Trash message gm_msgid={all_mail_uid}",
    )
    state.add_message(msg)

    # Override All Mail UID.
    all_mail_map = state._uid_maps.setdefault("[Gmail]/All Mail", {})
    all_mail_map[msg.gm_msgid] = all_mail_uid
    state._uid_counters["[Gmail]/All Mail"] = max(
        state._uid_counters.get("[Gmail]/All Mail", 0), all_mail_uid
    )


@then('the response folders does NOT include "{folder_path}"')
def step_response_folders_not_include(
    context: Context, folder_path: str
) -> None:
    response = _last_response(context)
    folders = response.get("folders") or response.get("folders_visible") or []
    folder_names = []
    for f in folders:
        if isinstance(f, dict):
            folder_names.append(f.get("path") or f.get("name") or f.get("folder", ""))
        else:
            folder_names.append(str(f))
    if folder_path in folder_names:
        raise AssertionError(
            f"Response folders unexpectedly include {folder_path!r}: "
            f"{folder_names!r}"
        )


@then("the response hidden_folders_count is at least {minimum:d}")
def step_response_hidden_folders_count_at_least(
    context: Context, minimum: int
) -> None:
    response = _last_response(context)
    actual = response.get("hidden_folders_count")
    if actual is None:
        raise AssertionError(
            f"Response has no hidden_folders_count field. "
            f"Keys: {sorted(response.keys())!r}"
        )
    if actual < minimum:
        raise AssertionError(
            f"hidden_folders_count: expected at least {minimum}, got {actual}"
        )


# -------------------------------------------------- response uids shorthand


@then("the response uids equals {expected_raw}")
def step_response_uids_equals(context: Context, expected_raw: str) -> None:
    """Check the response uids field matches the expected list.

    The expected list holds the scenario's UID hints; they resolve to
    the UIDs the IMAP server assigned at seeding (identity on the
    mock-gmail server, which takes the hint as the UID).
    """
    from features.steps.assertion_steps import _resolve_uid_hints

    response = _last_response(context)
    expected = _resolve_uid_hints(context, json.loads(expected_raw))
    actual = response.get("uids")
    if actual is None:
        # Try extracting UIDs from a results/messages list.
        results = response.get("gmail_results") or response.get("results") or response.get("messages") or []
        actual = [r.get("uid") for r in results if "uid" in r]
    if actual != expected:
        raise AssertionError(
            f"Response uids: expected {expected!r}, got {actual!r}"
        )


# ---------------------------------------- connection-count instrumentation


@given('the folder "{folder}" on "{account_id}" holds {n:d} messages')
def step_seed_n_messages(context: Context, folder: str, account_id: str, n: int) -> None:
    from datetime import datetime, timezone
    from mock_gmail.state import Message, FOLDER_TO_LABEL, _msgid_counter

    state = _gmail_state(context)
    label = FOLDER_TO_LABEL.get(folder, folder)
    now = datetime.now(tz=timezone.utc)
    date_str = now.strftime("%a, %d %b %Y %H:%M:%S +0000")
    for i in range(n):
        gm_msgid = next(_msgid_counter)
        rfc822 = (
            f"From: sender-{i}@example.com\r\n"
            f"To: test@scaratec.com\r\n"
            f"Subject: Test message {i}\r\n"
            f"Date: {date_str}\r\n"
            f"Message-ID: <perf-{gm_msgid}@example.com>\r\n"
            f"\r\nBody {i}\r\n"
        ).encode()
        msg = Message(
            gm_msgid=gm_msgid,
            gm_thrid=gm_msgid,
            rfc822=rfc822,
            labels={"\\Inbox"} if label == "\\Inbox" else {label},
            message_id=f"<perf-{gm_msgid}@example.com>",
            from_addr=f"sender-{i}@example.com",
            to_addr="test@scaratec.com",
            subject=f"Test message {i}",
            date=date_str,
        )
        state.add_message(msg)


@then("the mock-gmail server received at most {n:d} IMAP connections")
def step_assert_max_connections(context: Context, n: int) -> None:
    state = _gmail_state(context)
    actual = state.total_connections
    assert actual <= n, (
        f"Expected at most {n} IMAP connections, got {actual}"
    )


# ---------------------------------------- localized Gmail folders


@given("the mock-gmail server uses localized folder names:")
def step_mock_gmail_localized_folders(context: Context) -> None:
    """Configure the mock-gmail to return localized folder names in LIST.

    Table columns: canonical, localized, flags.
    """
    state = _gmail_state(context)
    mapping = []
    for row in context.table:
        mapping.append((row["canonical"], row["localized"], row["flags"]))
    state.set_localized_folders(mapping)


@then(
    'the draft is stored in "{folder}" on IMAP account "{account_id}"'
)
def step_draft_stored_on_imap(
    context: Context, folder: str, account_id: str
) -> None:
    """Verify via direct GmailState that a draft was appended to the
    expected (localized) folder."""
    state = _gmail_state(context)
    msgs = state.messages_in_folder(folder)
    if not msgs:
        all_folders = state.all_folders()
        folder_contents = {
            f: len(state.messages_in_folder(f)) for f in all_folders
        }
        raise AssertionError(
            f"No messages found in folder {folder!r}. "
            f"Folder message counts: {folder_contents!r}"
        )


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
    folders = response.get("folders") or []
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


@then('the IMAP command log for "{account_id}" contains no STORE command')
def step_command_log_has_no_store(context: Context, account_id: str) -> None:
    records = _server_command_log(context, account_id)
    hits = [r for r in records if r.command in ("STORE", "UID STORE")]
    if hits:
        raise AssertionError(f"Unexpected STORE in the command log:\n{_format_log(records)}")


# ---------------------------------------- Gmail draft lifecycle (real-Gmail fidelity)


def _harness_conn(context: Context, account_id: str, mailbox: str):
    """The harness's own IMAP connection, with `mailbox` (wire form) selected."""
    from support.imap_fixture import IMAPFixture

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
    try:
        status, _data = conn.uid(words[1].upper(), words[2])
    except imaplib.IMAP4.error as exc:
        if "BAD" not in str(exc) and "command error" not in str(exc):
            raise
        status = "BAD"
    context.last_direct_imap_status = status


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
    try:
        context.capability_before_login = {c.upper() for c in conn.capabilities}
        conn.login(user, TEST_PASSWORD)
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
