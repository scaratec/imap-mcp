"""Steps that invoke MCP tools against the server subprocess.

The harness starts the server lazily: the very first `When … calls …`
step in a scenario creates the `MCPClient`, which in turn spawns the
server subprocess with the scenario's final config directory. That
way every Given step gets a chance to contribute to the config before
the server reads it.

Per BDD Guidelines §1.3 these steps do not translate arguments
semantically. They hand whatever the feature file said to the MCP
client as tool arguments.
"""

from __future__ import annotations

import json
from pathlib import Path

from behave import given, when
from behave.runner import Context

from support.mcp_client import MCPClient, MCPClientError, MCPHttpClient, MCPRPCError

SERVER_BINARY_ENV = "IMAP_MCP_SERVER_BINARY"


def _server_binary(context: Context) -> Path:
    import os

    return Path(
        os.environ.get(
            SERVER_BINARY_ENV,
            context.bdd_root.parent / "server" / ".venv" / "bin" / "imap-mcp",
        )
    )


def _ensure_mcp_client(context: Context, caller_id: str) -> MCPClient:
    # Flush any staged message seeding before every tool call. Messages
    # staged *after* the server first started must still reach Dovecot
    # before the server issues its next IMAP read.
    from features.steps.policy_steps import flush_staged_messages

    flush_staged_messages(context)

    client = getattr(context, "mcp", None)
    if client is not None:
        return client

    builder = getattr(context, "policy_builder", None)
    if builder is not None:
        builder.write()

    import os

    server_binary = Path(
        os.environ.get(
            SERVER_BINARY_ENV,
            context.bdd_root.parent / "server" / ".venv" / "bin" / "imap-mcp",
        )
    )
    extra_env = getattr(context, "mcp_extra_env", None) or {}
    extra_env.setdefault("IMAP_MCP_TEST_MODE", "1")
    context.mcp_extra_env = extra_env
    client = MCPClient(
        server_binary=server_binary,
        config_dir=context.config_dir,
        caller_id=caller_id,
        extra_env=extra_env,
    )
    client.start()
    context.mcp = client
    return client


def _store_result(context: Context, payload: dict[str, object]) -> None:
    """Parse the MCP tool payload into the scenario's last-response slot."""
    content = payload.get("content") or []
    is_error = bool(payload.get("isError"))
    if not content:
        stderr = context.mcp.stderr_text if context.mcp else ""
        raise AssertionError(
            f"MCP tool response has no content: {payload!r}\n"
            f"Server stderr:\n{stderr}"
        )
    first = content[0]
    text = first.get("text") if isinstance(first, dict) else None
    if not isinstance(text, str):
        raise AssertionError(
            f"MCP tool response content is not a text block: {first!r}"
        )
    if is_error:
        stderr = context.mcp.stderr_text if context.mcp else ""
        raise AssertionError(
            f"MCP tool returned isError=true; text: {text!r}\n"
            f"Server stderr:\n{stderr}"
        )
    try:
        context.last_response = json.loads(text)
    except json.JSONDecodeError as exc:
        raise AssertionError(
            f"MCP tool text payload is not valid JSON: {exc}. "
            f"Raw text: {text!r}"
        )


@when("{caller_id} calls the MCP list_tools method")
def step_caller_calls_list_tools(context: Context, caller_id: str) -> None:
    client = _ensure_mcp_client(context, caller_id)
    tools = client.list_tools()
    context.last_tools = tools
    context.last_response = {"tools": tools}


@when('{caller_id} calls list_accounts')
def step_caller_calls_list_accounts(context: Context, caller_id: str) -> None:
    client = _ensure_mcp_client(context, caller_id)
    payload = client.call_tool("list_accounts", {})
    _store_result(context, payload)


@when('{caller_id} calls the MCP method "{method}" with name "{tool}"')
def step_caller_calls_mcp_method_with_name(
    context: Context, caller_id: str, method: str, tool: str
) -> None:
    """Probe JSON-RPC method with an explicit tool name.

    Used by non_goal_rejection scenarios to confirm an unknown tool
    surfaces as JSON-RPC error -32601. The server's response error is
    captured in context.last_rpc_error; `last_response` is cleared.
    """
    from support.mcp_client import MCPRPCError

    client = _ensure_mcp_client(context, caller_id)
    context.last_response = None
    context.last_rpc_error = None
    try:
        payload = client.raw_call(method, {"name": tool, "arguments": {}})
        context.last_response = payload
    except MCPRPCError as exc:
        context.last_rpc_error = {
            "code": exc.code,
            "message": exc.message,
            "data": exc.data,
        }


@when("{caller_id} calls describe_policy")
def step_caller_calls_describe_policy(context: Context, caller_id: str) -> None:
    client = _ensure_mcp_client(context, caller_id)
    payload = client.call_tool("describe_policy", {})
    _store_result(context, payload)


@when("{caller_id} calls describe_policy with extra argument {extra_raw}")
def step_caller_calls_describe_policy_with_extra(
    context: Context, caller_id: str, extra_raw: str
) -> None:
    import json as _json

    client = _ensure_mcp_client(context, caller_id)
    extras = _json.loads(extra_raw)
    payload = client.call_tool("describe_policy", extras)
    _store_result(context, payload)


@when("{caller_id} calls get_caller_identity")
def step_caller_calls_get_caller_identity(context: Context, caller_id: str) -> None:
    client = _ensure_mcp_client(context, caller_id)
    payload = client.call_tool("get_caller_identity", {})
    _store_result(context, payload)


@when('{caller_id} calls list_folders with account "{account}"')
def step_caller_calls_list_folders(
    context: Context, caller_id: str, account: str
) -> None:
    client = _ensure_mcp_client(context, caller_id)
    payload = client.call_tool("list_folders", {"account": account})
    _store_result(context, payload)


@when(
    '{caller_id} calls mark_seen with account "{account}", '
    'folder "{folder}", uid {uid:d}, seen {seen_raw}'
)
def step_caller_calls_mark_seen(
    context: Context, caller_id: str, account: str, folder: str, uid: int, seen_raw: str
) -> None:
    client = _ensure_mcp_client(context, caller_id)
    lookup = getattr(context, "message_uids", {})
    actual_uid = lookup.get((account, folder, uid), uid)
    seen = seen_raw.strip().lower() == "true"
    payload = client.call_tool(
        "mark_seen",
        {"account": account, "folder": folder, "uid": actual_uid, "seen": seen},
    )
    _store_result(context, payload)


@when(
    '{caller_id} calls mark_tagged with account "{account}", '
    'folder "{folder}", uid {uid:d}, tags {tags_raw}, mode "{mode}"'
)
def step_caller_calls_mark_tagged(
    context: Context,
    caller_id: str,
    account: str,
    folder: str,
    uid: int,
    tags_raw: str,
    mode: str,
) -> None:
    import ast
    import json as _json

    client = _ensure_mcp_client(context, caller_id)
    lookup = getattr(context, "message_uids", {})
    actual_uid = lookup.get((account, folder, uid), uid)
    try:
        tags = _json.loads(tags_raw)
    except _json.JSONDecodeError:
        # Feature files write literal IMAP flags like ["\Deleted"] which
        # are invalid JSON. Python's literal_eval tolerates the single
        # backslash correctly.
        tags = ast.literal_eval(tags_raw)
    payload = client.call_tool(
        "mark_tagged",
        {
            "account": account,
            "folder": folder,
            "uid": actual_uid,
            "tags": tags,
            "mode": mode,
        },
    )
    _store_result(context, payload)


@when(
    '{caller_id} calls move with source {src_raw}, target {dst_raw}'
)
def step_caller_calls_move_structured(
    context: Context, caller_id: str, src_raw: str, dst_raw: str
) -> None:
    import json as _json

    client = _ensure_mcp_client(context, caller_id)
    src = _json.loads(src_raw)
    dst = _json.loads(dst_raw)
    lookup = getattr(context, "message_uids", {})
    uid_hint = src.get("uid")
    if uid_hint is not None:
        src["uid"] = lookup.get((src["account"], src["folder"], uid_hint), uid_hint)
    try:
        payload = client.call_tool("move", {"source": src, "target": dst})
    except Exception:
        # A crash-recovery scenario has primed IMAP_MCP_CRASH_AT on the
        # server; the move call terminates the subprocess mid-flight.
        # Resolve the tx_id by inspecting the WAL so subsequent steps
        # can reference it.
        if not getattr(context, "crash_expected", False):
            raise
        from support.wal_reader import WALReader

        reader = WALReader(context.wal_path)
        txs = reader.all_transactions()
        if not txs:
            raise
        latest = txs[-1]
        context.last_tx_id = latest.tx_id
        context.last_response = {"tx_id": latest.tx_id}
        return
    _store_result(context, payload)


@when(
    '{caller_id} calls copy with source {src_raw}, target {dst_raw}'
)
def step_caller_calls_copy_structured(
    context: Context, caller_id: str, src_raw: str, dst_raw: str
) -> None:
    import json as _json

    client = _ensure_mcp_client(context, caller_id)
    src = _json.loads(src_raw)
    dst = _json.loads(dst_raw)
    lookup = getattr(context, "message_uids", {})
    uid_hint = src.get("uid")
    if uid_hint is not None:
        src["uid"] = lookup.get((src["account"], src["folder"], uid_hint), uid_hint)
    payload = client.call_tool("copy", {"source": src, "target": dst})
    _store_result(context, payload)


@when(
    '{caller_id} triggers a DENY with reason sender_blacklisted for a message from "{address}"'
)
def step_caller_triggers_sender_blacklist(
    context: Context, caller_id: str, address: str
) -> None:
    """Seed a message matching a blacklist rule and fetch its envelope.
    The server's PDP denies with `sender_blacklisted` and the audit
    writer hashes the from-domain into `from_domain_sha256`."""
    from features.steps.policy_steps import (
        _ensure_builder,
        flush_staged_messages,
    )
    from support.policy_builder import SenderRule

    builder = _ensure_builder(context)
    # Switch the default folder to blacklist mode with a rule matching
    # the provided address.
    policy = builder.policies[0]
    account_id = next(iter(policy.accounts.keys()))
    folder = policy.accounts[account_id][0]
    folder.mode = "blacklist"
    folder.default = "ENVELOPE"
    # In blacklist mode rules must be cap-only; drop pre-existing
    # grant-style rules (background leaves a grant rule in place).
    folder.rules = [
        SenderRule(match={"from": address}, cap="NONE")
    ]
    builder.write()

    context.staged_messages = getattr(context, "staged_messages", [])
    context.staged_messages.append(
        {
            "_account_id": account_id,
            "_folder": folder.path,
            "uid_hint": 999,
            "from": address,
            "to": None,
            "subject": "Ping",
            "message_id_override": None,
            "has_attachment": False,
            "size_hint": 0,
            "date": None,
            "extra_attachments": [],
            "extra_headers": [],
            "body_override": None,
        }
    )
    flush_staged_messages(context)

    client = _ensure_mcp_client(context, caller_id)
    uid_lookup = getattr(context, "message_uids", {})
    actual_uid = uid_lookup.get((account_id, folder.path, 999), 999)
    payload = client.call_tool(
        "fetch_envelope",
        {"account": account_id, "folder": folder.path, "uid": actual_uid},
    )
    _store_result(context, payload)


@given('the server process is started with transport "http" on a random port')
@given('the server is started with transport "http" on a random port')
@given('the server process is started with transport "http"')
@given('the server is started with transport "http"')
def step_server_started_http(context: Context) -> None:
    _start_http_server_for_test(context)


def _start_http_server_for_test(context: Context) -> None:
    """Launch the server subprocess in HTTP mode.

    On success, the running client is parked in `context.mcp_http`.
    On startup failure (e.g. an ADR-0015-violating config that refuses
    to bind), the captured exit code + stderr are placed in
    `context.startup_proc` so that subsequent Then-steps can assert on
    the failure mode without distinguishing the two transport
    variants.

    A stdio MCPClient that an earlier Background step started (via
    `… completes an Initialize handshake successfully`) is closed
    first — the scenario is switching transports."""
    stdio_client = getattr(context, "mcp", None)
    if stdio_client is not None:
        stdio_client.close()
        context.mcp = None

    from features.steps.policy_steps import flush_staged_messages

    flush_staged_messages(context)
    builder = getattr(context, "policy_builder", None)
    if builder is not None:
        builder.write()
    extra_env = getattr(context, "mcp_extra_env", None) or {}
    extra_env.setdefault("IMAP_MCP_TEST_MODE", "1")
    context.mcp_extra_env = extra_env
    client = MCPHttpClient(
        server_binary=_server_binary(context),
        config_dir=context.config_dir,
        extra_env=extra_env,
    )
    try:
        client.start_server()
    except MCPClientError as exc:
        # Synthesize a `startup_proc`-shape result so the existing
        # `the server refuses to start` Then-step can read it.
        from types import SimpleNamespace

        proc = client._proc
        rc = proc.returncode if proc is not None else 1
        context.startup_proc = SimpleNamespace(
            returncode=rc,
            stderr=client.stderr_text,
            stdout="",
        )
        context.http_startup_error = str(exc)
        return
    context.mcp_http = client


from behave import use_step_matcher as _use_step_matcher


_use_step_matcher("re")


@when(
    r'the MCP client performs an Initialize handshake with caller_id '
    r'"(?P<caller_id>[^"]+)" and bearer token "(?P<token>[^"]*)"'
)
def step_mcp_client_init_with_token(
    context: Context, caller_id: str, token: str
) -> None:
    client = context.mcp_http
    context.last_handshake_error = None
    try:
        client.initialize(caller_id, token)
        context.last_handshake_succeeded = True
    except MCPRPCError as exc:
        context.last_handshake_succeeded = False
        context.last_handshake_error = exc.message


@when(
    r'a client sends an Initialize with caller_id "(?P<caller_id>[^"]+)" '
    r'and bearer token "(?P<token>[^"]*)"'
)
def step_client_send_initialize(
    context: Context, caller_id: str, token: str
) -> None:
    step_mcp_client_init_with_token(context, caller_id, token)


_use_step_matcher("parse")


@when("an HTTP client makes GET /admin against the server")
def step_http_client_get_admin(context: Context) -> None:
    import httpx

    client = context.mcp_http
    response = httpx.get(
        f"http://{client.host}:{client.port}/admin", timeout=2.0
    )
    context.last_http_response = response


@when("an HTTP client makes POST /admin/reload-policy against the server")
def step_http_client_post_admin_reload(context: Context) -> None:
    import httpx

    client = context.mcp_http
    response = httpx.post(
        f"http://{client.host}:{client.port}/admin/reload-policy", timeout=2.0
    )
    context.last_http_response = response


@when("the server's background recovery loop runs {passes:d} times")
def step_recovery_loop_runs(context: Context, passes: int) -> None:
    """Invoke the test-only `_test_run_recovery` tool via raw_call.

    Not part of the production tool surface; exposed only when the
    server runs with IMAP_MCP_TEST_MODE=1 (set by the retry-limit step).
    """
    client = context.mcp
    if client is None:
        # Client not yet started — crash-recovery scenarios may have
        # reached this step without an active server. Start one now.
        client = _ensure_mcp_client(context, "invoice-agent")
    payload = client.raw_call(
        "tools/call",
        {"name": "_test_run_recovery", "arguments": {"passes": passes}},
    )
    context.last_recovery_result = payload


@when("the server's background recovery loop runs once")
def step_recovery_loop_runs_once(context: Context) -> None:
    step_recovery_loop_runs(context, 1)


@when("the server terminates ungracefully")
def step_server_terminates_ungracefully(context: Context) -> None:
    """Consume the checkpoint step. The preceding move step is
    expected to have crashed the server (IMAP_MCP_CRASH_AT). If the
    client is still alive, force-close it."""
    client = context.mcp
    if client is not None:
        # Server may already be dead; MCPClient.close tolerates that.
        client.close()
    context.mcp = None


@when("the server is restarted")
def step_server_restarted(context: Context) -> None:
    """Start a fresh MCP subprocess pointing at the same config/WAL,
    without the CRASH_AT env var."""
    if context.mcp is not None:
        context.mcp.close()
        context.mcp = None
    env = getattr(context, "mcp_extra_env", None) or {}
    env.pop("IMAP_MCP_CRASH_AT", None)
    env["IMAP_MCP_TEST_MODE"] = "1"
    context.mcp_extra_env = env
    # Don't start the client yet — the next step will, via
    # _ensure_mcp_client, which picks up mcp_extra_env.
    context.crash_expected = False


@when("{caller_id} calls get_transaction_status with the returned tx_id")
def step_caller_calls_get_transaction_status(
    context: Context, caller_id: str
) -> None:
    client = _ensure_mcp_client(context, caller_id)
    tx_id = context.last_response["tx_id"]
    context.last_tx_id = tx_id
    payload = client.call_tool("get_transaction_status", {"tx_id": tx_id})
    _store_result(context, payload)


# behave treats "Then ... calls ..." as a Then-step; register the same
# handler under @then so Then-line invocations of status polling work.
from behave import then as _behave_then  # noqa: E402


@_behave_then(
    "{caller_id} calls get_transaction_status with the returned tx_id"
)
def step_then_caller_calls_get_transaction_status(
    context: Context, caller_id: str
) -> None:
    step_caller_calls_get_transaction_status(context, caller_id)


@when(
    '{caller_id} calls move with account "{account}", source folder '
    '"{src_folder}" uid {uid:d}, target folder "{dst_folder}"'
)
def step_caller_calls_move_intra(
    context: Context,
    caller_id: str,
    account: str,
    src_folder: str,
    uid: int,
    dst_folder: str,
) -> None:
    client = _ensure_mcp_client(context, caller_id)
    lookup = getattr(context, "message_uids", {})
    actual_uid = lookup.get((account, src_folder, uid), uid)
    payload = client.call_tool(
        "move",
        {
            "source": {"account": account, "folder": src_folder, "uid": actual_uid},
            "target": {"account": account, "folder": dst_folder},
        },
    )
    _store_result(context, payload)


@when('{caller_id} calls create_draft with account "{account}", folder "{folder}", rfc822 payload:')
def step_caller_calls_create_draft(
    context: Context, caller_id: str, account: str, folder: str
) -> None:
    client = _ensure_mcp_client(context, caller_id)
    rfc822 = context.text or ""
    payload = client.call_tool(
        "create_draft",
        {"account": account, "folder": folder, "rfc822": rfc822},
    )
    _store_result(context, payload)


@when(
    '{caller_id} calls fetch_headers with account "{account}", '
    'folder "{folder}", uid {uid:d}'
)
def step_caller_calls_fetch_headers(
    context: Context, caller_id: str, account: str, folder: str, uid: int
) -> None:
    client = _ensure_mcp_client(context, caller_id)
    lookup = getattr(context, "message_uids", {})
    actual_uid = lookup.get((account, folder, uid), uid)
    payload = client.call_tool(
        "fetch_headers",
        {"account": account, "folder": folder, "uid": actual_uid},
    )
    _store_result(context, payload)


@when(
    '{caller_id} calls fetch_attachment with account "{account}", '
    'folder "{folder}", uid {uid:d}, part_id "{part_id}"'
)
def step_caller_calls_fetch_attachment_with_part(
    context: Context,
    caller_id: str,
    account: str,
    folder: str,
    uid: int,
    part_id: str,
) -> None:
    client = _ensure_mcp_client(context, caller_id)
    lookup = getattr(context, "message_uids", {})
    actual_uid = lookup.get((account, folder, uid), uid)
    payload = client.call_tool(
        "fetch_attachment",
        {
            "account": account,
            "folder": folder,
            "uid": actual_uid,
            "part_id": part_id,
        },
    )
    _store_result(context, payload)


@when(
    '{caller_id} calls fetch_attachment with account "{account}", '
    'folder "{folder}", uid {uid:d}'
)
def step_caller_calls_fetch_attachment(
    context: Context, caller_id: str, account: str, folder: str, uid: int
) -> None:
    client = _ensure_mcp_client(context, caller_id)
    lookup = getattr(context, "message_uids", {})
    actual_uid = lookup.get((account, folder, uid), uid)
    payload = client.call_tool(
        "fetch_attachment",
        {"account": account, "folder": folder, "uid": actual_uid},
    )
    _store_result(context, payload)


@when(
    '{caller_id} calls fetch_body with account "{account}", '
    'folder "{folder}", uid {uid:d}'
)
def step_caller_calls_fetch_body(
    context: Context, caller_id: str, account: str, folder: str, uid: int
) -> None:
    client = _ensure_mcp_client(context, caller_id)
    lookup = getattr(context, "message_uids", {})
    actual_uid = lookup.get((account, folder, uid), uid)
    payload = client.call_tool(
        "fetch_body",
        {"account": account, "folder": folder, "uid": actual_uid},
    )
    _store_result(context, payload)


@when(
    '{caller_id} calls folder_stats with account "{account}", folder "{folder}"'
)
def step_caller_calls_folder_stats(
    context: Context, caller_id: str, account: str, folder: str
) -> None:
    client = _ensure_mcp_client(context, caller_id)
    payload = client.call_tool(
        "folder_stats", {"account": account, "folder": folder}
    )
    _store_result(context, payload)


@when("{caller_id} calls search with criteria {criteria_raw}")
def step_caller_calls_search_shortcut(
    context: Context, caller_id: str, criteria_raw: str
) -> None:
    """Shortcut used by audit-log-format: no account/folder given; the
    scenario only cares about the audit-side behaviour. Use the
    default minimal account+folder."""
    import json as _json

    client = _ensure_mcp_client(context, caller_id)
    criteria = _json.loads(criteria_raw)
    context.last_search_criteria = criteria
    context.last_call_account = "gupta-scaratec"
    context.last_call_folder = "INBOX/Rechnungen"
    payload = client.call_tool(
        "search",
        {
            "account": context.last_call_account,
            "folder": context.last_call_folder,
            "criteria": criteria,
        },
    )
    _store_result(context, payload)


@when(
    '{caller_id} calls search with account "{account}", folder "{folder}", criteria {criteria_raw}'
)
def step_caller_calls_search(
    context: Context,
    caller_id: str,
    account: str,
    folder: str,
    criteria_raw: str,
) -> None:
    import json as _json

    client = _ensure_mcp_client(context, caller_id)
    criteria = _json.loads(criteria_raw)
    context.last_call_account = account
    context.last_call_folder = folder
    payload = client.call_tool(
        "search",
        {"account": account, "folder": folder, "criteria": criteria},
    )
    _store_result(context, payload)


@when(
    '{caller_id} calls fetch_envelope with account "{account}", '
    'folder "{folder}", uid {uid:d}'
)
def step_caller_calls_fetch_envelope(
    context: Context, caller_id: str, account: str, folder: str, uid: int
) -> None:
    """Invoke fetch_envelope with the uid mentioned literally in the scenario.

    Feature files state a uid-hint that matches the row of the seed
    table. The actual server-assigned uid may differ (IMAP controls
    UID assignment); `message_uids` resolves the hint to the server
    uid if a seed step registered it, otherwise the hint is passed
    through for DENY-path scenarios that never reach IMAP.
    """
    client = _ensure_mcp_client(context, caller_id)
    lookup = getattr(context, "message_uids", {})
    actual_uid = lookup.get((account, folder, uid), uid)
    payload = client.call_tool(
        "fetch_envelope",
        {"account": account, "folder": folder, "uid": actual_uid},
    )
    _store_result(context, payload)
