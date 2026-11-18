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


def _parse_query_tail(tail: str) -> tuple[str, dict[str, object]]:
    """Split a search/list/bulk tail into (criteria-JSON, extra-args).

    The feature-file form is
      criteria {...}, limit N, offset N, scope "X", seen true, ...
    where every `, key value` pair is optional. Returns the JSON part
    that holds the criteria object, plus a dict of the trailing key/
    value pairs. Unknown keys are returned as-is so callers can decide
    whether to honour or reject them.
    """
    import json as _json
    import re as _re

    matchers = {
        "limit": _re.compile(r",\s*limit\s+(\d+)"),
        "offset": _re.compile(r",\s*offset\s+(\d+)"),
        "scope": _re.compile(r',\s*scope\s+"([^"]+)"'),
        "seen": _re.compile(r",\s*seen\s+(true|false)"),
        "mode": _re.compile(r',\s*mode\s+"([^"]+)"'),
        "tags": _re.compile(r",\s*tags\s+(\[[^\]]*\])"),
    }
    earliest: int | None = None
    extras: dict[str, object] = {}
    for key, pattern in matchers.items():
        m = pattern.search(tail)
        if m is None:
            continue
        if earliest is None or m.start() < earliest:
            earliest = m.start()
        raw = m.group(1)
        if key in ("limit", "offset"):
            extras[key] = int(raw)
        elif key == "seen":
            extras[key] = raw.lower() == "true"
        elif key == "tags":
            import ast as _ast

            try:
                extras[key] = _json.loads(raw)
            except _json.JSONDecodeError:
                # Tolerate `["\Deleted"]` etc.
                extras[key] = _ast.literal_eval(raw)
        else:
            extras[key] = raw
    criteria_str = tail[:earliest] if earliest is not None else tail
    return criteria_str, extras


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
    from features.steps.policy_steps import (
        capture_witnessed_command_logs,
        flush_staged_messages,
    )

    flush_staged_messages(context)

    clients = getattr(context, "mcp_clients", None)
    if clients is None:
        clients = {}
        context.mcp_clients = clients
    if caller_id in clients:
        context.mcp = clients[caller_id]
        return clients[caller_id]

    capture_witnessed_command_logs(context)

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
    extra_env = dict(getattr(context, "mcp_extra_env", None) or {})
    needs_test_mode = (
        getattr(context, "is_test_hooks", False)
        or getattr(context, "needs_test_mode", False)
        or "IMAP_MCP_FAKE_NOW_UTC" in extra_env
        or "IMAP_MCP_FAKE_NOW_FILE" in extra_env
        or "IMAP_MCP_CRASH_AT" in extra_env
        or "IMAP_MCP_SAGA_PAUSE_AT" in extra_env
        or "IMAP_MCP_TEST_INJECT_INTERNAL_ERROR" in extra_env
        or extra_env.get("IMAP_MCP_TEST_MODE") == "1"
    )
    if needs_test_mode and not getattr(context, "without_test_mode", False):
        extra_env["IMAP_MCP_TEST_MODE"] = "1"
    else:
        extra_env.pop("IMAP_MCP_TEST_MODE", None)
    context.mcp_extra_env = extra_env
    client = MCPClient(
        server_binary=server_binary,
        config_dir=context.config_dir,
        caller_id=caller_id,
        extra_env=extra_env,
        unset_env=tuple(getattr(context, "mcp_unset_env", ())),
        **_client_info_kwargs(context),
    )
    client.start()
    clients[caller_id] = client
    context.mcp = client
    # Remembered so that steps which restart the server on their own —
    # the recovery loop after a crash — can put the scenario's caller
    # back rather than inventing one.
    context.mcp_caller_id = caller_id
    return client


def _client_info_kwargs(context: Context) -> dict[str, str]:
    """The Initialize `clientInfo` a scenario set for the MCP test client,
    or nothing, which keeps the client's own default."""
    info = getattr(context, "mcp_client_info", None)
    if info is None:
        return {}
    name, version = info
    return {"client_name": name, "client_version": version}


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
    # The text exactly as received, for witnesses that measure what the
    # client had to read (ADR 0038 response size).
    context.last_raw_payload = payload
    context.last_content_blocks = content
    context.last_response_texts = [
        block["text"]
        for block in content
        if isinstance(block, dict) and isinstance(block.get("text"), str)
    ]
    if is_error:
        stderr = context.mcp.stderr_text if context.mcp else ""
        raise AssertionError(
            f"MCP tool returned isError=true; text: {text!r}\n"
            f"Server stderr:\n{stderr}"
        )
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        raise AssertionError(
            f"MCP tool text payload is not valid JSON: {exc}. "
            f"Raw text: {text!r}"
        )
    context.last_response = parsed
    # Draft handles are opaque and server-assigned (ADR 0030), so a
    # scenario cannot name one. It refers to "the returned draft_id",
    # which is the one the previous call handed back.
    if isinstance(parsed, dict):
        returned_draft_id = parsed.get("draft_id")
        if isinstance(returned_draft_id, str) and returned_draft_id:
            context.last_draft_id = returned_draft_id
    history = getattr(context, "response_history", None)
    if history is None:
        history = []
        context.response_history = history
    history.append(parsed)
    for block in content[1:]:
        if isinstance(block, dict) and block.get("type") == "resource":
            resource = block.get("resource", {})
            if "blob" in resource:
                context.last_blob = resource


@when("{caller_id} calls the MCP list_tools method")
@when("{caller_id} lists the available tools")
def step_caller_calls_list_tools(context: Context, caller_id: str) -> None:
    client = _ensure_mcp_client(context, caller_id)
    tools = client.list_tools()
    context.last_tools = tools
    context.last_response = {"tools": tools}


@given('{caller_id} calls list_accounts')
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




@when("{caller_id} calls get_caller_identity")
def step_caller_calls_get_caller_identity(context: Context, caller_id: str) -> None:
    client = _ensure_mcp_client(context, caller_id)
    payload = client.call_tool("get_caller_identity", {})
    _store_result(context, payload)


@when('{caller_id} calls list_folders with account "{account}"')
def step_caller_calls_list_folders(
    context: Context, caller_id: str, account: str
) -> None:
    # Stash the current hidden_folders_count (if any) as the
    # "previous" anchor for `decreases by 1 compared to the previous
    # call`-style assertions in policy_reload.feature.
    prior = getattr(context, "last_response", None) or {}
    if "hidden_folders_count" in prior:
        context.previous_hidden_folders_count = prior["hidden_folders_count"]
    client = _ensure_mcp_client(context, caller_id)
    payload = client.call_tool("list_folders", {"account": account})
    _store_result(context, payload)


def _res_uid(context: Context, account: str, folder: str, uid: int) -> int:
    from support.imap_fixture import resolve_uid

    return resolve_uid(context, account, folder, uid, allow_unseeded_probe=True)


@when(
    '{caller_id} calls mark_seen with account "{account}", '
    'folder "{folder}", uid {uid:d}, seen {seen_raw}'
)
def step_caller_calls_mark_seen(
    context: Context, caller_id: str, account: str, folder: str, uid: int, seen_raw: str
) -> None:
    client = _ensure_mcp_client(context, caller_id)
    actual_uid = _res_uid(context, account, folder, uid)
    seen = seen_raw.strip().lower() == "true"
    payload = client.call_tool(
        "mark_seen",
        {"account": account, "folder": folder, "uid": actual_uid, "seen": seen},
    )
    _store_result(context, payload)


@when(
    '{caller_id} calls bulk_mark_seen with account "{account}", '
    'folder "{folder}", criteria {tail}'
)
def step_caller_calls_bulk_mark_seen(
    context: Context, caller_id: str, account: str, folder: str, tail: str
) -> None:
    import json as _json

    criteria_str, extras = _parse_query_tail(tail)
    if "seen" not in extras:
        raise AssertionError(
            "bulk_mark_seen step requires `, seen true|false` in the tail"
        )
    args: dict[str, object] = {
        "account": account,
        "folder": folder,
        "criteria": _json.loads(criteria_str),
        "seen": extras["seen"],
    }
    if "scope" in extras:
        args["scope"] = extras["scope"]
    client = _ensure_mcp_client(context, caller_id)
    payload = client.call_tool("bulk_mark_seen", args)
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
    actual_uid = _res_uid(context, account, folder, uid)
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




@given(
    '{caller_id} calls move with source {src_raw}, target {dst_raw}'
)
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
    uid_hint = src.get("uid")
    if uid_hint is not None:
        src["uid"] = _res_uid(context, src["account"], src["folder"], int(uid_hint))
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
    uid_hint = src.get("uid")
    if uid_hint is not None:
        src["uid"] = _res_uid(context, src["account"], src["folder"], int(uid_hint))
    payload = client.call_tool("copy", {"source": src, "target": dst})
    _store_result(context, payload)


@given('the server process is started with transport "http" on 127.0.0.1 and a random port')
@given('the server process is started with transport "http" on a random port')
@given('the server is started with transport "http" on a random port')
@given('the server process is started with transport "http"')
@given('the server is started with transport "http"')
@when('the server process is started with transport "http"')
@when('the server is started with transport "http"')
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
    if builder is not None and not getattr(context, "custom_callers_yaml", False):
        builder.write()
    extra_env = getattr(context, "mcp_extra_env", None) or {}
    if getattr(context, "is_test_hooks", False) and not getattr(context, "without_test_mode", False):
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


@given(
    r'the MCP client performs an Initialize handshake with caller_id '
    r'"(?P<caller_id>[^"]+)" and bearer token "(?P<token>[^"]*)"'
)
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
    except (MCPRPCError, MCPClientError, Exception) as exc:
        context.last_handshake_succeeded = False
        msg = getattr(exc, "message", None) or str(exc)
        if "auth_failed" in msg:
            context.last_handshake_error = "auth_failed"
        else:
            context.last_handshake_error = msg




_use_step_matcher("parse")












@when('the file "{filename}" is deleted out-of-band')
def step_file_deleted_out_of_band(context: Context, filename: str) -> None:
    from features.steps.policy_steps import _scratch_substitute

    resolved = _scratch_substitute(context, filename)
    path = Path(resolved)
    if not path.is_absolute():
        path = context.audit_dir / filename
    if path.exists():
        path.unlink()






@when("the server receives SIGHUP")
def step_server_receives_sighup(context: Context) -> None:
    """Send SIGHUP to the running server process and give the loop a
    moment to apply the reload before the next step's tool call.

    A scenario can reach this step before performing any MCP call
    (e.g. policy_reload's parse-error case sends SIGHUP first). In
    that case the harness needs a running server to deliver the
    signal to — start the stdio MCPClient implicitly."""
    import signal as _signal
    import time as _time

    if (
        getattr(context, "mcp", None) is None
        and getattr(context, "mcp_http", None) is None
    ):
        # Lazy-start an stdio client so SIGHUP has a recipient.
        _ensure_mcp_client(context, "invoice-agent")

    proc = None
    if getattr(context, "mcp", None) is not None:
        proc = context.mcp._proc  # stdio MCPClient
    elif getattr(context, "mcp_http", None) is not None:
        proc = context.mcp_http._proc
    if proc is None or proc.poll() is not None:
        raise AssertionError("No running server to signal")
    proc.send_signal(_signal.SIGHUP)
    deadline = _time.time() + 2.0
    from support.audit_reader import AuditReader

    while _time.time() < deadline:
        try:
            reader = AuditReader(context.audit_dir)
            if reader.find(tool="policy_reload"):
                break
        except Exception:
            pass
        _time.sleep(0.02)








@when("the server is restarted")
@when("the server is restarted without crash hooks")
def step_server_restarted(context: Context) -> None:
    """Start a fresh MCP subprocess pointing at the same config/WAL,
    without the CRASH_AT env var."""
    if context.mcp is not None:
        context.mcp.close()
        context.mcp = None
    if getattr(context, "mcp_clients", None):
        for c in context.mcp_clients.values():
            c.close()
        context.mcp_clients.clear()
    env = getattr(context, "mcp_extra_env", None) or {}
    env.pop("IMAP_MCP_CRASH_AT", None)
    context.mcp_extra_env = env
    context.crash_expected = False
    caller_id = (
        getattr(context, "last_caller_id", None)
        or (
            context.policy_builder.callers[0].id
            if getattr(context, "policy_builder", None) and context.policy_builder.callers
            else None
        )
    )
    if not caller_id:
        raise AssertionError("No caller configured in scenario")
    client = _spawn_mcp_client_for(context, caller_id)
    context.mcp = client
    clients = getattr(context, "mcp_clients", None)
    if clients is None:
        clients = {}
        context.mcp_clients = clients
    clients[caller_id] = client


def _spawn_mcp_client_for(context: Context, caller_id: str) -> MCPClient:
    server_binary = _server_binary(context)
    extra_env = dict(getattr(context, "mcp_extra_env", None) or {})
    needs_test_mode = (
        getattr(context, "is_test_hooks", False)
        or getattr(context, "needs_test_mode", False)
        or any(
            k.startswith(("IMAP_MCP_TEST_", "IMAP_MCP_CRASH_", "IMAP_MCP_SAGA_"))
            or k in ("IMAP_MCP_FAKE_NOW_UTC", "IMAP_MCP_FAKE_NOW_FILE")
            for k in extra_env
        )
        or extra_env.get("IMAP_MCP_TEST_MODE") == "1"
    )
    if needs_test_mode and not getattr(context, "without_test_mode", False):
        extra_env["IMAP_MCP_TEST_MODE"] = "1"
    else:
        extra_env.pop("IMAP_MCP_TEST_MODE", None)
    client = MCPClient(
        server_binary=server_binary,
        config_dir=context.config_dir,
        caller_id=caller_id,
        extra_env=extra_env,
        unset_env=tuple(getattr(context, "mcp_unset_env", ())),
        **_client_info_kwargs(context),
    )
    client.start()
    return client


@when("two server instances start concurrently sharing the same wal.db and config")
def step_two_server_instances_start_concurrently(context: Context) -> None:
    if getattr(context, "mcp", None) is not None:
        context.mcp.close()
        context.mcp = None
    if getattr(context, "mcp_clients", None):
        for c in context.mcp_clients.values():
            c.close()
        context.mcp_clients.clear()
    env = getattr(context, "mcp_extra_env", None) or {}
    env.pop("IMAP_MCP_CRASH_AT", None)
    context.mcp_extra_env = env
    context.crash_expected = False

    caller = next(iter(context.policy_builder.callers), None) if getattr(context, "policy_builder", None) else None
    caller_id = getattr(context, "last_caller_id", None) or (caller.id if caller else None)
    if not caller_id:
        raise AssertionError("No caller configured in scenario")
    c1 = _spawn_mcp_client_for(context, caller_id)
    c2 = _spawn_mcp_client_for(context, caller_id)
    context.mcp = c1
    context.mcp_secondary = c2


@given("the WAL database file is removed before startup")
def step_wal_db_removed_before_startup(context: Context) -> None:
    if context.wal_path.exists():
        context.wal_path.unlink()
    shm = context.wal_path.with_name(context.wal_path.name + "-shm")
    wal_file = context.wal_path.with_name(context.wal_path.name + "-wal")
    if shm.exists():
        shm.unlink()
    if wal_file.exists():
        wal_file.unlink()


@given('the server is armed with IMAP_MCP_CRASH_AT="{crash_at}"')
def step_server_armed_with_crash_at(context: Context, crash_at: str) -> None:
    env = getattr(context, "mcp_extra_env", None) or {}
    env["IMAP_MCP_CRASH_AT"] = crash_at
    env["IMAP_MCP_TEST_MODE"] = "1"
    context.mcp_extra_env = env
    context.is_test_hooks = True
    context.crash_expected = True
    clients = getattr(context, "mcp_clients", None) or {}
    for c in list(clients.values()):
        c.close()
    clients.clear()
    context.mcp = None


@when(
    '{caller_id} invokes move expecting crash for uid {uid:d} from "{src_box}" to "{dst_box}"'
)
def step_caller_invokes_move_expecting_crash(
    context: Context, caller_id: str, uid: int, src_box: str, dst_box: str
) -> None:
    import time

    from support.imap_fixture import resolve_uid
    from support.mcp_client import MCPClientError

    src_acc, _, src_folder = src_box.partition(":")
    dst_acc, _, dst_folder = dst_box.partition(":")
    context.crash_expected = True
    client = _ensure_mcp_client(context, caller_id)
    actual_uid = resolve_uid(context, src_acc, src_folder, uid, allow_unseeded_probe=True)

    call_failed = False
    try:
        client.call_tool(
            "move",
            {
                "source": {"account": src_acc, "folder": src_folder, "uid": actual_uid},
                "target": {"account": dst_acc, "folder": dst_folder},
            },
        )
    except (MCPClientError, BrokenPipeError, EOFError, OSError):
        call_failed = True

    if not call_failed:
        raise AssertionError("Expected call_tool to crash but it succeeded")

    deadline = time.time() + 3.0
    exit_code = None
    while time.time() < deadline:
        exit_code = client.poll_exit()
        if exit_code is not None:
            break
        time.sleep(0.05)

    if exit_code is None:
        raise AssertionError("Crash hook did not terminate server process within 3 seconds")

    client.close()
    context.mcp = None
    clients = getattr(context, "mcp_clients", None)
    if clients:
        clients.pop(caller_id, None)
    from support.wal_reader import WALReader

    reader = WALReader(context.wal_path)
    txs = reader.all_transactions()
    if txs:
        context.crashed_tx_id = txs[-1].tx_id
        context.last_tx_id = txs[-1].tx_id


@when(
    '{caller_id} invokes update_draft expecting crash on "{box}" '
    'for the returned draft_id, base_revision {base_revision:d}, changes {changes_raw}'
)
def step_caller_invokes_update_draft_expecting_crash(
    context: Context,
    caller_id: str,
    box: str,
    base_revision: int,
    changes_raw: str,
) -> None:
    import json as _json
    import time

    from support.mcp_client import MCPClientError

    account, _, folder = box.partition(":")
    draft_id = getattr(context, "last_draft_id", None)
    if not draft_id:
        raise AssertionError("No returned draft_id found in context")
    changes = _json.loads(changes_raw)
    context.crash_expected = True
    client = _ensure_mcp_client(context, caller_id)

    call_failed = False
    res = None
    try:
        res = client.call_tool(
            "update_draft",
            {
                "account": account,
                "folder": folder,
                "draft_id": draft_id,
                "base_revision": base_revision,
                "changes": changes,
            },
        )
    except (MCPClientError, BrokenPipeError, EOFError, OSError):
        call_failed = True

    if not call_failed:
        raise AssertionError(f"Expected update_draft to crash but it succeeded: {res!r}")

    deadline = time.time() + 3.0
    exit_code = None
    while time.time() < deadline:
        exit_code = client.poll_exit()
        if exit_code is not None:
            break
        time.sleep(0.05)

    if exit_code is None:
        raise AssertionError("Crash hook did not terminate server process within 3 seconds")

    client.close()
    context.mcp = None
    clients = getattr(context, "mcp_clients", None)
    if clients:
        clients.pop(caller_id, None)
    from support.wal_reader import WALReader

    reader = WALReader(context.wal_path)
    txs = reader.all_transactions()
    if txs:
        context.crashed_tx_id = txs[-1].tx_id
        context.last_tx_id = txs[-1].tx_id




@when("{caller_id} calls get_transaction_status with the returned tx_id")
def step_caller_calls_get_transaction_status(
    context: Context, caller_id: str
) -> None:
    client = _ensure_mcp_client(context, caller_id)
    tx_id = context.last_response["tx_id"]
    context.last_tx_id = tx_id
    payload = client.call_tool("get_transaction_status", {"tx_id": tx_id})
    _store_result(context, payload)


@when('{caller_id} calls get_transaction_status with the tx_id created by {creator}')
def step_caller_calls_get_tx_status_by_creator(
    context: Context, caller_id: str, creator: str
) -> None:
    tx_id = getattr(context, "last_tx_id", None) or (context.last_response or {}).get("tx_id")
    client = _ensure_mcp_client(context, caller_id)
    payload = client.call_tool("get_transaction_status", {"tx_id": tx_id})
    _store_result(context, payload)


@when('{caller_id} calls get_transaction_status with tx_id "{tx_id}"')
def step_caller_calls_get_tx_status_literal(
    context: Context, caller_id: str, tx_id: str
) -> None:
    client = _ensure_mcp_client(context, caller_id)
    payload = client.call_tool("get_transaction_status", {"tx_id": tx_id})
    _store_result(context, payload)


@given('a second server instance is started sharing wal.db for caller "{caller_id}" with an empty policy')
def step_second_server_instance_started(context: Context, caller_id: str) -> None:
    import os
    import shutil

    import yaml

    from support.mcp_client import MCPClient

    second_config_dir = context.scratch_dir / f"config-{caller_id}"
    second_config_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(context.config_dir / "accounts.yaml", second_config_dir / "accounts.yaml")
    callers_data = {
        "callers": [
            {
                "id": caller_id,
                "policy": f"{caller_id}-policy",
                "auth": {"type": "stdio_trusted"},
            }
        ]
    }
    (second_config_dir / "callers.yaml").write_text(yaml.safe_dump(callers_data), encoding="utf-8")
    policies_dir = second_config_dir / "policies"
    policies_dir.mkdir(exist_ok=True)
    policy_data = {
        "name": f"{caller_id}-policy",
        "accounts": {},
    }
    (policies_dir / f"{caller_id}-policy.yaml").write_text(yaml.safe_dump(policy_data), encoding="utf-8")

    server_binary = Path(
        os.environ.get(
            SERVER_BINARY_ENV,
            context.bdd_root.parent / "server" / ".venv" / "bin" / "imap-mcp",
        )
    )
    extra_env = dict(getattr(context, "mcp_extra_env", None) or {})
    needs_test_mode = (
        getattr(context, "is_test_hooks", False)
        or any(k.startswith("IMAP_MCP_") for k in extra_env)
        or extra_env.get("IMAP_MCP_TEST_MODE") == "1"
    )
    if needs_test_mode and not getattr(context, "without_test_mode", False):
        extra_env["IMAP_MCP_TEST_MODE"] = "1"
    else:
        extra_env.pop("IMAP_MCP_TEST_MODE", None)
    client2 = MCPClient(
        server_binary=server_binary,
        config_dir=second_config_dir,
        caller_id=caller_id,
        extra_env=extra_env,
        **_client_info_kwargs(context),
    )
    client2.start()
    clients = getattr(context, "mcp_clients", None)
    if clients is None:
        clients = {}
        context.mcp_clients = clients
    clients[caller_id] = client2


# behave treats "Then ... calls ..." as a Then-step; register the same
# handler under @then so Then-line invocations of status polling work.
from behave import then as _behave_then


@given('the server process is started with transport "stdio" and environment IMAP_MCP_CALLER_ID="{caller_id}"')
def step_server_started_stdio_with_caller(context: Context, caller_id: str) -> None:
    """Spawn the stdio server with `IMAP_MCP_CALLER_ID=caller_id` set,
    but do NOT yet send the Initialize handshake. The MCP-client step
    that follows performs the handshake and captures success/error."""
    _spawn_stdio_unhandshaked(context, caller_id=caller_id)


@given('the server process is started with transport "stdio" and no IMAP_MCP_CALLER_ID set')
def step_server_started_stdio_no_caller(context: Context) -> None:
    _spawn_stdio_unhandshaked(context, caller_id=None)


@given('the server process is started without IMAP_MCP_TEST_MODE on transport "stdio" with IMAP_MCP_CALLER_ID="{caller_id}"')
def step_server_started_stdio_without_test_mode(context: Context, caller_id: str) -> None:
    _spawn_stdio_unhandshaked(context, caller_id=caller_id, without_test_mode=True)


def _spawn_stdio_unhandshaked(
    context: Context, *, caller_id: str | None, without_test_mode: bool = False
) -> None:
    builder = getattr(context, "policy_builder", None)
    if builder is not None:
        builder.write()
    extra_env = getattr(context, "mcp_extra_env", None) or {}
    context.mcp_extra_env = extra_env
    unset = [
        "IMAP_MCP_TEST_MODE",
        "IMAP_MCP_FAKE_NOW_UTC",
        "IMAP_MCP_FAKE_NOW_FILE",
        "IMAP_MCP_SAGA_CRASH_AT",
        "IMAP_MCP_SAGA_PAUSE_MARKER",
        "IMAP_MCP_SAGA_RESUME_FILE",
        "IMAP_MCP_TEST_INJECT_INTERNAL_ERROR",
    ] if without_test_mode else []
    client = MCPClient(
        server_binary=_server_binary(context),
        config_dir=context.config_dir,
        caller_id=caller_id or "",
        extra_env=extra_env,
        unset_env=tuple(unset),
    )
    # Bypass the auto-Initialize that the regular `start()` performs.
    # The Initialize is performed explicitly in the next When-step.
    import os as _os
    import subprocess as _subprocess
    import threading as _threading

    env = dict(_os.environ)
    env["IMAP_MCP_CONFIG_DIR"] = str(client.config_dir)
    if caller_id:
        env["IMAP_MCP_CALLER_ID"] = caller_id
    else:
        env.pop("IMAP_MCP_CALLER_ID", None)
    if without_test_mode:
        for u in unset:
            env.pop(u, None)
    if extra_env:
        for k, v in extra_env.items():
            if not without_test_mode or k not in unset:
                env[k] = v
    proc = _subprocess.Popen(
        [str(client.server_binary), "--transport", "stdio"],
        stdin=_subprocess.PIPE,
        stdout=_subprocess.PIPE,
        stderr=_subprocess.PIPE,
        env=env,
        bufsize=0,
    )
    client._proc = proc
    client._stdin = proc.stdin
    client._stdout = proc.stdout
    client._stdout_thread = _threading.Thread(
        target=client._drain_stdout, args=(proc.stdout,), daemon=True
    )
    client._stdout_thread.start()
    client._stderr_thread = _threading.Thread(
        target=client._drain_stderr, args=(proc.stderr,), daemon=True
    )
    client._stderr_thread.start()
    context.mcp = client
    if caller_id:
        if not hasattr(context, "mcp_clients") or context.mcp_clients is None:
            context.mcp_clients = {}
        context.mcp_clients[caller_id] = client
        context.mcp_caller_id = caller_id






@_behave_then('the server responds with error "{expected}"')
def step_server_responds_with_error(context: Context, expected: str) -> None:
    err = getattr(context, "last_handshake_error", None)
    if err != expected:
        raise AssertionError(
            f"Server response error: expected {expected!r}, got {err!r}"
        )


@when("the MCP client performs an Initialize handshake")
def step_mcp_client_init_stdio(context: Context) -> None:
    """Stdio variant: send the Initialize JSON-RPC and capture either
    success or an MCPRPCError. The server may legitimately respond
    with `error: {message: "unknown_caller_id" | "no_caller_identity"}`
    when the caller cannot be authenticated (ADR-0015 stdio_trusted)."""
    client = context.mcp
    context.last_handshake_error = None
    try:
        client._initialize()
        context.last_handshake_succeeded = True
    except MCPRPCError as exc:
        context.last_handshake_succeeded = False
        context.last_handshake_error = exc.message


@_behave_then(
    "{caller_id} calls get_transaction_status with the returned tx_id"
)
def step_then_caller_calls_get_transaction_status(
    context: Context, caller_id: str
) -> None:
    step_caller_calls_get_transaction_status(context, caller_id)




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


def _call_create_draft_keeping_rpc_error(
    context: Context, caller_id: str, arguments: dict[str, object]
) -> None:
    """Issue one create_draft call, keeping either the payload or the
    JSON-RPC error: ADR 0035 rejects malformed input at the protocol
    layer, so the error must survive as `context.last_rpc_error`."""
    client = _ensure_mcp_client(context, caller_id)
    context.last_response = None
    context.last_rpc_error = None
    try:
        payload = client.call_tool("create_draft", arguments)
    except MCPRPCError as exc:
        context.last_rpc_error = {
            "code": exc.code,
            "message": exc.message,
            "data": exc.data,
        }
        return
    _store_result(context, payload)


@given('{caller_id} calls create_draft with account "{account}", folder "{folder}", fields:')
@when('{caller_id} calls create_draft with account "{account}", folder "{folder}", fields:')
def step_caller_calls_create_draft_fields(
    context: Context, caller_id: str, account: str, folder: str
) -> None:
    """The DocString is a JSON object whose members are passed as
    create_draft arguments beside account and folder, unchanged."""
    fields = json.loads(context.text or "{}")
    if not isinstance(fields, dict):
        raise AssertionError(f"fields DocString must be a JSON object, got {fields!r}")
    _call_create_draft_keeping_rpc_error(
        context, caller_id, {"account": account, "folder": folder, **fields}
    )




@when(
    '{caller_id} calls create_draft with account "{account}", folder "{folder}", '
    'fields {fields_raw}'
)
def step_caller_calls_create_draft_fields_inline(
    context: Context, caller_id: str, account: str, folder: str, fields_raw: str
) -> None:
    fields = json.loads(fields_raw)
    if not isinstance(fields, dict):
        raise AssertionError(f"fields must be a JSON object, got {fields!r}")
    _call_create_draft_keeping_rpc_error(
        context, caller_id, {"account": account, "folder": folder, **fields}
    )


@when(
    '{caller_id} calls create_draft with account "{account}", folder "{folder}", '
    'rfc822 "{rfc822}"'
)
def step_caller_calls_create_draft_rfc822_inline(
    context: Context, caller_id: str, account: str, folder: str, rfc822: str
) -> None:
    decoded = json.loads(f'"{rfc822}"')
    _call_create_draft_keeping_rpc_error(
        context, caller_id, {"account": account, "folder": folder, "rfc822": decoded}
    )


@when(
    '{caller_id} calls fetch_headers with account "{account}", '
    'folder "{folder}", uid {uid:d}'
)
def step_caller_calls_fetch_headers(
    context: Context, caller_id: str, account: str, folder: str, uid: int
) -> None:
    client = _ensure_mcp_client(context, caller_id)
    actual_uid = _res_uid(context, account, folder, uid)
    payload = client.call_tool(
        "fetch_headers",
        {"account": account, "folder": folder, "uid": actual_uid},
    )
    _store_result(context, payload)


@when(
    '{caller_id} calls fetch_attachment with account "{account}", '
    'folder "{folder}", uid {uid:d}, part_id {part_id:d}'
)
def step_caller_calls_fetch_attachment_with_part(
    context: Context,
    caller_id: str,
    account: str,
    folder: str,
    uid: int,
    part_id: int,
) -> None:
    client = _ensure_mcp_client(context, caller_id)
    actual_uid = _res_uid(context, account, folder, uid)
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
    '{caller_id} calls list_attachments with account "{account}", '
    'folder "{folder}", uid {uid:d}'
)
def step_caller_calls_list_attachments(
    context: Context, caller_id: str, account: str, folder: str, uid: int
) -> None:
    client = _ensure_mcp_client(context, caller_id)
    actual_uid = _res_uid(context, account, folder, uid)
    payload = client.call_tool(
        "list_attachments",
        {"account": account, "folder": folder, "uid": actual_uid},
    )
    _store_result(context, payload)


@when(
    '{caller_id} calls bulk_mark_tagged with account "{account}", '
    'folder "{folder}", criteria {tail}'
)
def step_caller_calls_bulk_mark_tagged(
    context: Context, caller_id: str, account: str, folder: str, tail: str
) -> None:
    """bulk_mark_tagged uses the same tail grammar as bulk_mark_seen plus
    `, tags [...]` and `, mode "..."`. Both are required by the schema.
    """
    import json as _json

    criteria_str, extras = _parse_query_tail(tail)
    if "tags" not in extras or "mode" not in extras:
        raise AssertionError(
            "bulk_mark_tagged step requires `, tags [...]` and `, mode \"...\"` in the tail"
        )
    args: dict[str, object] = {
        "account": account,
        "folder": folder,
        "criteria": _json.loads(criteria_str),
        "tags": extras["tags"],
        "mode": extras["mode"],
    }
    if "scope" in extras:
        args["scope"] = extras["scope"]
    client = _ensure_mcp_client(context, caller_id)
    payload = client.call_tool("bulk_mark_tagged", args)
    _store_result(context, payload)


@when("{caller_id} calls tool_surface_info")
def step_caller_calls_tool_surface_info(context: Context, caller_id: str) -> None:
    client = _ensure_mcp_client(context, caller_id)
    payload = client.call_tool("tool_surface_info", {})
    _store_result(context, payload)


@when(
    '{caller_id} calls fetch_body with account "{account}", '
    'folder "{folder}", uid {uid:d}'
)
def step_caller_calls_fetch_body(
    context: Context, caller_id: str, account: str, folder: str, uid: int
) -> None:
    client = _ensure_mcp_client(context, caller_id)
    actual_uid = _res_uid(context, account, folder, uid)
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




@given(
    '{caller_id} calls search with account "{account}", folder "{folder}", criteria {tail}'
)
@when(
    '{caller_id} calls search with account "{account}", folder "{folder}", criteria {tail}'
)
def step_caller_calls_search(
    context: Context,
    caller_id: str,
    account: str,
    folder: str,
    tail: str,
) -> None:
    import json as _json

    criteria_str, extras = _parse_query_tail(tail)
    context.last_call_account = account
    context.last_call_folder = folder
    args: dict[str, object] = {
        "account": account,
        "folder": folder,
        "criteria": _json.loads(criteria_str),
    }
    for key in ("limit", "offset", "scope"):
        if key in extras:
            args[key] = extras[key]
    client = _ensure_mcp_client(context, caller_id)
    context.last_response = None
    context.last_rpc_error = None
    try:
        payload = client.call_tool("search", args)
        _store_result(context, payload)
    except MCPRPCError as exc:
        context.last_rpc_error = {
            "code": exc.code,
            "message": exc.message,
            "data": exc.data,
        }


@when(
    '{caller_id} calls create_reply_draft on the only visible message in "{src_box}" '
    'into "{dst_folder}" with reply_text "{reply_text}"'
)
def step_caller_calls_create_reply_draft_only_visible(
    context: Context, caller_id: str, src_box: str, dst_folder: str, reply_text: str
) -> None:
    src_acc, _, src_folder = src_box.partition(":")
    client = _ensure_mcp_client(context, caller_id)
    search_res = client.call_tool(
        "search",
        {"account": src_acc, "folder": src_folder, "criteria": {}, "scope": "all"},
    )
    content = search_res.get("content", [{}])[0].get("text", "{}")
    search_data = json.loads(content)
    uids = search_data.get("uids", [])
    if len(uids) != 1:
        raise AssertionError(f"Expected 1 visible message in {src_box}, found {len(uids)}: {uids}")
    uid = uids[0]
    payload = client.call_tool(
        "create_reply_draft",
        {
            "account": src_acc,
            "source_folder": src_folder,
            "uid": uid,
            "drafts_folder": dst_folder,
            "reply_text": reply_text,
        },
    )
    _store_result(context, payload)


_use_step_matcher("re")


@when(
    r'(?P<caller_id>\S+) calls search with account "(?P<account>[^"]+)", '
    r'folder "(?P<folder>[^"]+)"'
)
def step_caller_calls_search_no_criteria(
    context: Context, caller_id: str, account: str, folder: str
) -> None:
    """search without criteria, scope, limit or offset."""
    context.last_call_account = account
    context.last_call_folder = folder
    client = _ensure_mcp_client(context, caller_id)
    payload = client.call_tool("search", {"account": account, "folder": folder})
    _store_result(context, payload)


@when(
    r"(?P<caller_id>\S+) calls (?P<tool>[a-z_]+) with the arguments "
    r"(?P<arguments_raw>\{.*\})"
)
def step_caller_calls_tool_with_arguments(
    context: Context, caller_id: str, tool: str, arguments_raw: str
) -> None:
    """Send the JSON object exactly as written as the tool's arguments:
    nothing added, nothing coerced. A JSON-RPC error is captured for
    the Then-steps; a payload is stored as the last response."""
    import json as _json

    arguments = _json.loads(arguments_raw)
    if not isinstance(arguments, dict):
        raise AssertionError(f"The arguments must be a JSON object, got {arguments_raw!r}")
    client = _ensure_mcp_client(context, caller_id)
    context.last_response = None
    context.last_rpc_error = None
    try:
        payload = client.call_tool(tool, arguments)
        _store_result(context, payload)
    except MCPRPCError as exc:
        context.last_rpc_error = {
            "code": exc.code,
            "message": exc.message,
            "data": exc.data,
        }


_use_step_matcher("parse")


@when(
    '{caller_id} calls list_messages with account "{account}", folder "{folder}", criteria {tail}'
)
def step_caller_calls_list_messages(
    context: Context, caller_id: str, account: str, folder: str, tail: str
) -> None:
    import json as _json

    criteria_str, extras = _parse_query_tail(tail)
    args: dict[str, object] = {
        "account": account,
        "folder": folder,
        "criteria": _json.loads(criteria_str),
    }
    for key in ("limit", "offset", "scope"):
        if key in extras:
            args[key] = extras[key]
    client = _ensure_mcp_client(context, caller_id)
    context.last_response = None
    context.last_rpc_error = None
    try:
        payload = client.call_tool("list_messages", args)
        _store_result(context, payload)
    except MCPRPCError as exc:
        context.last_rpc_error = {
            "code": exc.code,
            "message": exc.message,
            "data": exc.data,
        }


@when(
    '{caller_id} calls list_messages with account "{account:S}", folder "{folder:S}"'
)
def step_caller_calls_list_messages_no_criteria(
    context: Context, caller_id: str, account: str, folder: str
) -> None:
    """no-criteria + no-scope short form. The :S type-spec on account/
    folder makes the matcher non-greedy on whitespace so a trailing
    `criteria {...}` / `scope "..."` is NOT absorbed into folder."""
    client = _ensure_mcp_client(context, caller_id)
    payload = client.call_tool("list_messages", {"account": account, "folder": folder})
    _store_result(context, payload)




@given(
    '{caller_id} calls fetch_envelope with account "{account}", '
    'folder "{folder}", uid {uid:d}'
)
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
    actual_uid = _res_uid(context, account, folder, uid)
    payload = client.call_tool(
        "fetch_envelope",
        {"account": account, "folder": folder, "uid": actual_uid},
    )
    _store_result(context, payload)


@when(
    '{caller_id} calls create_reply_draft with account "{account}", '
    'source_folder "{src_folder}", uid {uid:d}, '
    'drafts_folder "{drafts_folder}", reply_text:'
)
def step_caller_calls_create_reply_draft_docstring(
    context: Context,
    caller_id: str,
    account: str,
    src_folder: str,
    uid: int,
    drafts_folder: str,
) -> None:
    """DocString form: reply_text body lives in `context.text`."""
    client = _ensure_mcp_client(context, caller_id)
    actual_uid = _res_uid(context, account, src_folder, uid)
    payload = client.call_tool(
        "create_reply_draft",
        {
            "account": account,
            "source_folder": src_folder,
            "uid": actual_uid,
            "drafts_folder": drafts_folder,
            "reply_text": context.text or "",
        },
    )
    _store_result(context, payload)


@when(
    '{caller_id} calls create_reply_draft with account "{account}", '
    'source_folder "{src_folder}", uid {uid:d}, '
    'drafts_folder "{drafts_folder}", reply_html "{reply_html}"'
)
def step_caller_calls_create_reply_draft_reply_html(
    context: Context,
    caller_id: str,
    account: str,
    src_folder: str,
    uid: int,
    drafts_folder: str,
    reply_html: str,
) -> None:
    client = _ensure_mcp_client(context, caller_id)
    actual_uid = _res_uid(context, account, src_folder, uid)
    payload = client.call_tool(
        "create_reply_draft",
        {
            "account": account,
            "source_folder": src_folder,
            "uid": actual_uid,
            "drafts_folder": drafts_folder,
            "reply_html": reply_html,
        },
    )
    _store_result(context, payload)


@when(
    '{caller_id} calls create_reply_draft with account "{account}", '
    'source_folder "{src_folder}", uid {uid:d}, '
    'drafts_folder "{drafts_folder}", reply_text ""'
)
def step_caller_calls_create_reply_draft_empty(
    context: Context,
    caller_id: str,
    account: str,
    src_folder: str,
    uid: int,
    drafts_folder: str,
) -> None:
    """Inline empty-string form for the empty_reply_text validation path."""
    client = _ensure_mcp_client(context, caller_id)
    actual_uid = _res_uid(context, account, src_folder, uid)
    payload = client.call_tool(
        "create_reply_draft",
        {
            "account": account,
            "source_folder": src_folder,
            "uid": actual_uid,
            "drafts_folder": drafts_folder,
            "reply_text": "",
        },
    )
    _store_result(context, payload)


@when(
    '{caller_id} calls create_reply_draft with account "{account}", '
    'source_folder "{src_folder}", uid {uid:d}, '
    'drafts_folder "{drafts_folder}", reply_text "{reply_text}"'
)
def step_caller_calls_create_reply_draft_inline(
    context: Context,
    caller_id: str,
    account: str,
    src_folder: str,
    uid: int,
    drafts_folder: str,
    reply_text: str,
) -> None:
    """Inline form for a one-line reply body."""
    client = _ensure_mcp_client(context, caller_id)
    actual_uid = _res_uid(context, account, src_folder, uid)
    payload = client.call_tool(
        "create_reply_draft",
        {
            "account": account,
            "source_folder": src_folder,
            "uid": actual_uid,
            "drafts_folder": drafts_folder,
            "reply_text": reply_text,
        },
    )
    _store_result(context, payload)




@when(
    '{caller_id} calls get_draft with account "{account}", '
    'folder "{folder}", the returned draft_id'
)
def step_caller_calls_get_draft_returned_handle(
    context: Context, caller_id: str, account: str, folder: str
) -> None:
    client = _ensure_mcp_client(context, caller_id)
    draft_id = getattr(context, "last_draft_id", None)
    if not draft_id:
        raise AssertionError(
            "No draft_id has been returned in this scenario yet; "
            "the step needs a preceding create_draft or create_reply_draft."
        )
    payload = client.call_tool(
        "get_draft",
        {"account": account, "folder": folder, "draft_id": draft_id},
    )
    _store_result(context, payload)


@when(
    '{caller_id} calls get_draft with account "{account}", '
    'folder "{folder}", draft_id "{draft_id}"'
)
def step_caller_calls_get_draft_literal_handle(
    context: Context, caller_id: str, account: str, folder: str, draft_id: str
) -> None:
    """Handle named in the scenario, for drafts the harness seeded
    verbatim instead of letting create_draft mint the identifier."""
    client = _ensure_mcp_client(context, caller_id)
    payload = client.call_tool(
        "get_draft",
        {"account": account, "folder": folder, "draft_id": draft_id},
    )
    _store_result(context, payload)


@when(
    '{caller_id} calls discard_draft with account "{account}", '
    'folder "{folder}", the returned draft_id'
)
def step_caller_calls_discard_draft_returned_handle(
    context: Context, caller_id: str, account: str, folder: str
) -> None:
    draft_id = getattr(context, "last_draft_id", None)
    if not draft_id:
        raise AssertionError(
            "No draft_id has been returned in this scenario yet; "
            "the step needs a preceding create_draft or create_reply_draft."
        )
    client = _ensure_mcp_client(context, caller_id)
    payload = client.call_tool(
        "discard_draft",
        {"account": account, "folder": folder, "draft_id": draft_id},
    )
    _store_result(context, payload)


@when(
    '{caller_id} calls discard_draft with account "{account}", '
    'folder "{folder}", draft_id "{draft_id}"'
)
def step_caller_calls_discard_draft_literal_handle(
    context: Context, caller_id: str, account: str, folder: str, draft_id: str
) -> None:
    """Handle named in the scenario, for drafts the harness seeded."""
    client = _ensure_mcp_client(context, caller_id)
    payload = client.call_tool(
        "discard_draft",
        {"account": account, "folder": folder, "draft_id": draft_id},
    )
    _store_result(context, payload)


@when('{caller_id} calls list_drafts with account "{account}", folder "{folder}"')
def step_caller_calls_list_drafts(
    context: Context, caller_id: str, account: str, folder: str
) -> None:
    client = _ensure_mcp_client(context, caller_id)
    payload = client.call_tool("list_drafts", {"account": account, "folder": folder})
    _store_result(context, payload)




# --------------------------------------------------------- update_draft


def _call_update_draft(
    context: Context,
    caller_id: str,
    account: str,
    folder: str,
    draft_id: str,
    base_revision: int,
) -> None:
    """Issue one update_draft call, keeping either the payload or the
    JSON-RPC error. The malformed-argument scenarios of ADR 0031 are
    rejected at the protocol layer, so the error must survive as
    `context.last_rpc_error` instead of aborting the scenario."""
    client = _ensure_mcp_client(context, caller_id)
    changes = json.loads(context.text or "{}")
    context.last_response = None
    context.last_rpc_error = None
    try:
        payload = client.call_tool(
            "update_draft",
            {
                "account": account,
                "folder": folder,
                "draft_id": draft_id,
                "base_revision": base_revision,
                "changes": changes,
            },
        )
        _store_result(context, payload)
    except MCPRPCError as exc:
        context.last_rpc_error = {
            "code": exc.code,
            "message": exc.message,
            "data": exc.data,
        }
    except Exception:
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


@when(
    '{caller_id} calls update_draft with account "{account}", folder "{folder}", '
    "the returned draft_id, base_revision {base_revision:d}, changes:"
)
def step_caller_calls_update_draft_returned_handle(
    context: Context,
    caller_id: str,
    account: str,
    folder: str,
    base_revision: int,
) -> None:
    draft_id = getattr(context, "last_draft_id", None)
    if not draft_id:
        raise AssertionError(
            "No draft_id has been returned in this scenario yet; "
            "the step needs a preceding create_draft or create_reply_draft."
        )
    _call_update_draft(context, caller_id, account, folder, draft_id, base_revision)


@when(
    '{caller_id} calls update_draft with account "{account}", folder "{folder}", '
    'the returned draft_id, base_revision {base_revision:d}, changes {changes_raw}'
)
def step_caller_calls_update_draft_returned_handle_inline(
    context: Context,
    caller_id: str,
    account: str,
    folder: str,
    base_revision: int,
    changes_raw: str,
) -> None:
    draft_id = getattr(context, "last_draft_id", None)
    if not draft_id:
        raise AssertionError(
            "No draft_id has been returned in this scenario yet; "
            "the step needs a preceding create_draft or create_reply_draft."
        )
    client = _ensure_mcp_client(context, caller_id)
    changes = json.loads(changes_raw)
    context.last_response = None
    context.last_rpc_error = None
    try:
        payload = client.call_tool(
            "update_draft",
            {
                "account": account,
                "folder": folder,
                "draft_id": draft_id,
                "base_revision": base_revision,
                "changes": changes,
            },
        )
        _store_result(context, payload)
    except MCPRPCError as exc:
        context.last_rpc_error = {
            "code": exc.code,
            "message": exc.message,
            "data": exc.data,
        }


@when(
    '{caller_id} calls update_draft with account "{account}", folder "{folder}", '
    'draft_id "{draft_id}", base_revision {base_revision:d}, changes:'
)
def step_caller_calls_update_draft_literal_handle(
    context: Context,
    caller_id: str,
    account: str,
    folder: str,
    draft_id: str,
    base_revision: int,
) -> None:
    _call_update_draft(context, caller_id, account, folder, draft_id, base_revision)


@when("the MCP client calls get_caller_identity on the established stdio session")
def step_caller_calls_get_caller_identity_stdio(context: Context) -> None:
    client = getattr(context, "mcp", None)
    if client is None:
        raise AssertionError("No established stdio MCP client found on context")
    payload = client.call_tool("get_caller_identity", {})
    _store_result(context, payload)


# create_draft is also a precondition: the revision scenarios set up the
# draft under test in a Background/Given before exercising update_draft.
# Same handler, second step type.
given(
    '{caller_id} calls create_draft with account "{account}", folder "{folder}", '
    "rfc822 payload:"
)(step_caller_calls_create_draft)


# --------------------------------------------------- server environment


def _server_not_started_yet(context: Context, what: str) -> None:
    if getattr(context, "mcp", None) is not None:
        raise AssertionError(
            f"{what} must precede the server start; the server process already runs "
            "and would not see it."
        )


@given('the environment variable "{name}" is set to "{value}"')
def step_environment_variable_set(context: Context, name: str, value: str) -> None:
    """The variable reaches the server process the scenario starts."""
    _server_not_started_yet(context, f"Setting {name}")
    env = getattr(context, "mcp_extra_env", None) or {}
    env[name] = value
    context.mcp_extra_env = env
    context.mcp_unset_env = [n for n in getattr(context, "mcp_unset_env", []) if n != name]


@given('the environment variable "{name}" is not set')
def step_environment_variable_not_set(context: Context, name: str) -> None:
    """Absent from the server's environment, including when the shell
    running the suite exports it."""
    _server_not_started_yet(context, f"Unsetting {name}")
    env = getattr(context, "mcp_extra_env", None) or {}
    env.pop(name, None)
    context.mcp_extra_env = env
    unset = list(getattr(context, "mcp_unset_env", []))
    if name not in unset:
        unset.append(name)
    context.mcp_unset_env = unset


@given('the MCP test client identifies itself as "{name}" version "{version}"')
def step_mcp_client_identifies_itself(context: Context, name: str, version: str) -> None:
    """`clientInfo` of the Initialize request the test client sends."""
    _server_not_started_yet(context, "The client identity")
    context.mcp_client_info = (name, version)


@given(
    'the MCP test client reconnects with client name consisting of {count:d} "{char}" characters plus "\\n\\t" and version "{version}"'
)
def step_mcp_client_reconnects_custom(
    context: Context, count: int, char: str, version: str
) -> None:
    client = getattr(context, "mcp", None)
    if client is not None:
        client.close()
        context.mcp = None
    client_name = char * count + "\n\t"
    context.mcp_client_info = (client_name, version)


@when('the server process is started with transport "stdio"')
def step_server_process_started_stdio(context: Context) -> None:
    """Start the server with the scenario's configuration and environment,
    send it an Initialize, close stdin and record how it ended in
    `context.startup_proc` for `the server refuses to start`."""
    from types import SimpleNamespace

    from support.server_bootstrap import try_bootstrap_server

    builder = getattr(context, "policy_builder", None)
    if builder is not None:
        builder.write()
    caller = next(iter(builder.callers), None) if builder is not None else None
    extra_env = dict(getattr(context, "mcp_extra_env", None) or {})
    needs_test_mode = (
        getattr(context, "is_test_hooks", False)
        or any(k.startswith("IMAP_MCP_") for k in extra_env)
        or extra_env.get("IMAP_MCP_TEST_MODE") == "1"
    )
    if needs_test_mode and not getattr(context, "without_test_mode", False):
        extra_env["IMAP_MCP_TEST_MODE"] = "1"
    else:
        extra_env.pop("IMAP_MCP_TEST_MODE", None)
    result = try_bootstrap_server(
        _server_binary(context),
        context.config_dir,
        caller.id if caller is not None else "bootstrap-probe",
        extra_env=extra_env,
        unset_env=tuple(getattr(context, "mcp_unset_env", ())),
    )
    if result.timed_out:
        raise AssertionError(
            "The server neither refused to start nor ended after stdin closed; "
            f"it was killed. Stderr:\n{result.stderr}"
        )
    context.startup_proc = SimpleNamespace(
        returncode=result.exit_code, stdout=result.stdout, stderr=result.stderr
    )


@_behave_then('the startup error names "{variable}" and the allowed values "{allowed}"')
def step_startup_error_names_variable_and_values(
    context: Context, variable: str, allowed: str
) -> None:
    error = getattr(context, "startup_error", "")
    missing = [part for part in (variable, allowed) if part not in error]
    if missing:
        raise AssertionError(f"Startup error lacks {missing!r}. Full message:\n{error}")


# ------------------------------------------------- ending the server


def _running_client(context: Context) -> MCPClient:
    client = getattr(context, "mcp", None)
    if client is None:
        raise AssertionError("No stdio server runs in this scenario.")
    return client


@when("the MCP test client closes the server's stdin immediately after the response")
def step_client_closes_stdin(context: Context) -> None:
    _running_client(context).close_stdin()


@when("the MCP test client sends SIGTERM to the server immediately after the response")
def step_client_sends_sigterm(context: Context) -> None:
    import signal

    _running_client(context).send_signal(signal.SIGTERM)


@_behave_then("within {seconds:d} seconds the server process has exited")
def step_server_process_exited(context: Context, seconds: int) -> None:
    import time

    client = _running_client(context)
    deadline = time.monotonic() + seconds
    while client.poll_exit() is None:
        if time.monotonic() >= deadline:
            raise AssertionError(f"The server process still runs after {seconds} s")
        time.sleep(0.05)
