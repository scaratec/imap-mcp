"""Steps that witness IMAP connections and commands (ADR 0013 pool).

Every Then reads a channel the server does not control: the mock-gmail
server's own connection bookkeeping and command log for mock-gmail
accounts, the logging IMAP proxy (support/imap_proxy.py) for
Dovecot-backed accounts. The proxy is wired in front of a witnessed
account before the server starts (`capture_witnessed_command_logs`).
"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

from behave import given, then, use_step_matcher, when
from behave.runner import Context

from support.imap_fixture import resolve_account

use_step_matcher("parse")


# ----------------------------------------------------------------- helpers


def imap_command_lines(context: Context, account_id: str, *, allow_empty: bool = False) -> list[str]:
    """The IMAP commands the server sent for `account_id`, in order, each
    as `<COMMAND> <arguments>` without the tag.

    Accounts behind the logging proxy: the proxy log. mock-gmail
    accounts without a proxy: the mock's command log minus the
    harness's own connection (LOGIN and AUTHENTICATE carry no arguments
    there). A missing channel is a harness error, never an empty log.
    """
    log_path = (getattr(context, "imap_proxy_log_paths", None) or {}).get(account_id)
    instance, _user = resolve_account(account_id)
    if instance == "mock-gmail" and (log_path is None or not Path(log_path).exists()):
        from features.steps.gmail_steps import _server_command_log

        lines = []
        for record in _server_command_log(context, account_id):
            verb = "UID" if record.command.startswith("UID ") else record.command
            lines.append(f"{verb} {record.args}".strip())
        return lines
    if log_path is None or not Path(log_path).exists():
        if allow_empty:
            return []
        raise AssertionError(
            f"No IMAP proxy log captured for account {account_id!r}; "
            "did a prior step start the MITM proxy?"
        )
    lines = []
    for entry in Path(log_path).read_text(encoding="utf-8", errors="replace").splitlines():
        _stamp, _, command_line = entry.partition("\t")
        _tag, _, command = command_line.partition(" ")
        if command:
            lines.append(command)
    if not lines and not allow_empty:
        raise AssertionError(f"The IMAP proxy recorded no command for {account_id!r}.")
    return lines


def _verb(command: str) -> str:
    tokens = command.split()
    if not tokens:
        return ""
    if tokens[0].upper() == "UID" and len(tokens) > 1:
        return f"UID {tokens[1].upper()}"
    return tokens[0].upper()


# IMAP has two commands that authenticate a session: LOGIN (RFC 3501
# §6.2.3), which password accounts use, and AUTHENTICATE (§6.2.2), which
# XOAUTH2 accounts use. Either one is "an AUTHENTICATE command" in the
# sense of the feature files, as either one is a span imap.authenticate
# (ADR 0038).
_AUTHENTICATION_VERBS = frozenset({"LOGIN", "AUTHENTICATE"})


# ------------------------------------------------------------- command log


@then('the IMAP command log for "{account_id}" ends with a {verb} command')
def step_command_log_ends_with(context: Context, account_id: str, verb: str) -> None:
    commands = imap_command_lines(context, account_id)
    if _verb(commands[-1]) != verb.upper():
        listing = "\n".join(f"  {c}" for c in commands[-10:])
        raise AssertionError(
            f"The IMAP command log for {account_id!r} ends with {commands[-1]!r}, "
            f"not {verb}. Last commands:\n{listing}"
        )


@then(
    'within {seconds:d} seconds the IMAP command log for "{account_id}" ends with a {verb} command'
)
def step_command_log_ends_with_within(
    context: Context, seconds: int, account_id: str, verb: str
) -> None:
    """Polls the command log until its last command is `verb`, for at
    most `seconds`."""
    import time

    deadline = time.monotonic() + seconds
    while True:
        commands = imap_command_lines(context, account_id)
        if _verb(commands[-1]) == verb.upper():
            return
        if time.monotonic() >= deadline:
            listing = "\n".join(f"  {c}" for c in commands[-10:])
            raise AssertionError(
                f"After {seconds} s the IMAP command log for {account_id!r} still ends "
                f"with {commands[-1]!r}, not {verb}. Last commands:\n{listing}"
            )
        time.sleep(0.1)


# ------------------------------------------------------------- pool settings
# The operator settings of ADR 0013's pool (README, Environment variables).
# They take effect when the server starts, so they must precede the
# scenario's first call.


def _set_server_env(context: Context, name: str, value: int) -> None:
    if getattr(context, "mcp", None) is not None:
        raise AssertionError(f"{name} must be set before the server starts.")
    context.mcp_extra_env = getattr(context, "mcp_extra_env", None) or {}
    context.mcp_extra_env[name] = str(value)


@given("the server's connection pool closes connections idle for {n:d} second")
@given("the server's connection pool closes connections idle for {n:d} seconds")
def step_pool_idle_ttl(context: Context, n: int) -> None:
    _set_server_env(context, "IMAP_MCP_POOL_IDLE_TTL", n)


@given("the server's connection pool closes connections older than {n:d} seconds")
def step_pool_max_age(context: Context, n: int) -> None:
    _set_server_env(context, "IMAP_MCP_POOL_MAX_AGE", n)


# ------------------------------------------------------------- connections


@then('the IMAP connections opened for account "{account_id}" number exactly {n:d}')
def step_connections_opened_exactly(context: Context, account_id: str, n: int) -> None:
    """Counts the connections the logging proxy in front of the account
    accepted. The proxy must be running and wired into the account; a
    missing log then means no connection was opened."""
    proxy_proc = (getattr(context, "imap_proxy_procs", None) or {}).get(account_id)
    log_path = (getattr(context, "imap_proxy_connection_log_paths", None) or {}).get(account_id)
    if proxy_proc is None or log_path is None or proxy_proc.poll() is not None:
        raise AssertionError(
            f"No running IMAP proxy counts the connections of {account_id!r}."
        )
    builder = getattr(context, "policy_builder", None)
    proxy_port = (getattr(context, "imap_proxy_ports", None) or {}).get(account_id)
    if builder is None or not any(
        account.id == account_id and account.port == proxy_port for account in builder.accounts
    ):
        raise AssertionError(
            f"Account {account_id!r} is not configured to reach IMAP through "
            f"the proxy on port {proxy_port!r}."
        )
    path = Path(log_path)
    opened = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    if len(opened) != n:
        raise AssertionError(
            f"Expected exactly {n} IMAP connection(s) for {account_id!r}, "
            f"the proxy accepted {len(opened)}:\n" + "\n".join(opened)
        )


# ----------------------------------------------------- mock-gmail server


def _gmail_state(context: Context):
    from features.steps.gmail_steps import _gmail_state as state

    return state(context)


@when("the mock-gmail server closes every open connection")
def step_mock_gmail_closes_connections(context: Context) -> None:
    """Server-side close of every open connection, as Gmail drops idle
    sessions; returns once the mock has seen each one go."""
    context.mock_gmail_closed_connections = _gmail_state(context).close_all_connections()


# ------------------------------------------------------ concurrent calls


@when(
    '{caller_id} issues {n:d} concurrent calls of fetch_envelope with account '
    '"{account}", folder "{folder}", uid {uid:d}'
)
def step_concurrent_fetch_envelope(
    context: Context, caller_id: str, n: int, account: str, folder: str, uid: int
) -> None:
    """All requests are written to the server before any response is
    read, so they are in flight together."""
    from features.steps.mcp_steps import _ensure_mcp_client, _store_result
    from support.imap_fixture import resolve_uid

    client = _ensure_mcp_client(context, caller_id)
    actual_uid = resolve_uid(context, account, folder, uid, allow_unseeded_probe=True)
    arguments = {"account": account, "folder": folder, "uid": actual_uid}
    payloads = client.call_tools_concurrently([("fetch_envelope", dict(arguments))] * n)
    context.concurrent_responses = []
    for payload in payloads:
        _store_result(context, payload)
        context.concurrent_responses.append(context.last_response)


@then("all {n:d} responses have decision {decision}")
def step_all_responses_decision(context: Context, n: int, decision: str) -> None:
    responses = getattr(context, "concurrent_responses", None)
    if responses is None:
        raise AssertionError("No concurrent calls were issued in this scenario.")
    if len(responses) != n:
        raise AssertionError(f"Expected {n} responses, got {len(responses)}.")
    wrong = [r for r in responses if r.get("decision") != decision]
    if wrong:
        raise AssertionError(
            f"{len(wrong)} of {n} responses do not have decision {decision}: {wrong[0]!r}"
        )


@then('the IMAP connections opened for account "{account_id}" number between {low:d} and {high:d}')
def step_connections_opened_between(context: Context, account_id: str, low: int, high: int) -> None:
    proxy_proc = (getattr(context, "imap_proxy_procs", None) or {}).get(account_id)
    log_path = (getattr(context, "imap_proxy_connection_log_paths", None) or {}).get(account_id)
    if proxy_proc is None or log_path is None or proxy_proc.poll() is not None:
        raise AssertionError(
            f"No running IMAP proxy counts the connections of {account_id!r}."
        )
    builder = getattr(context, "policy_builder", None)
    proxy_port = (getattr(context, "imap_proxy_ports", None) or {}).get(account_id)
    if builder is None or not any(
        account.id == account_id and account.port == proxy_port for account in builder.accounts
    ):
        raise AssertionError(
            f"Account {account_id!r} is not configured to reach IMAP through "
            f"the proxy on port {proxy_port!r}."
        )
    path = Path(log_path)
    opened = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    n = len(opened)
    if not (low <= n <= high):
        raise AssertionError(
            f"Expected between {low} and {high} IMAP connections for {account_id!r}, "
            f"the proxy accepted {n}:\n" + "\n".join(opened)
        )


@then('the mock-gmail server received between {low:d} and {high:d} IMAP connections')
def step_mock_gmail_connections_between(context: Context, low: int, high: int) -> None:
    state = _gmail_state(context)
    actual = state.total_connections
    if not (low <= actual <= high):
        raise AssertionError(f"Expected between {low} and {high} IMAP connections, got {actual}")


@then('the mock-gmail server received exactly {n:d} IMAP connection')
@then('the mock-gmail server received exactly {n:d} IMAP connections')
def step_mock_gmail_connections_exact(context: Context, n: int) -> None:
    state = _gmail_state(context)
    actual = state.total_connections
    if actual != n:
        raise AssertionError(f"Expected exactly {n} IMAP connections, got {actual}")


@then('the mock-gmail server received 0 IMAP connections')
def step_mock_gmail_connections_zero(context: Context) -> None:
    state = _gmail_state(context)
    actual = state.total_connections
    if actual != 0:
        raise AssertionError(f"Expected 0 IMAP connections, got {actual}")


@then('the mock-gmail peak concurrent connections equalled {n:d}')
def step_mock_gmail_peak_concurrent(context: Context, n: int) -> None:
    state = _gmail_state(context)
    peak = state.peak_concurrent_connections
    if peak != n:
        raise AssertionError(f"Expected peak concurrent connections {n}, got {peak}")


@when('the IMAP command log for "{account_id}" is cleared')
@given('the IMAP command log for "{account_id}" is cleared')
def step_clear_command_log(context: Context, account_id: str) -> None:
    log_path = (getattr(context, "imap_proxy_log_paths", None) or {}).get(account_id)
    if log_path is not None and Path(log_path).exists():
        Path(log_path).write_text("", encoding="utf-8")
    state = getattr(context, "gmail_state", None)
    if state is not None and hasattr(state, "command_log"):
        state.command_log.clear()


@then('the IMAP command log for "{account_id}" contains a LOGIN command for user "{user}"')
def step_command_log_contains_login_user(context: Context, account_id: str, user: str) -> None:
    lines = imap_command_lines(context, account_id)
    pattern = re.compile(rf'\bLOGIN\s+["\']?{re.escape(user)}["\']?\b', re.IGNORECASE)
    if not any(pattern.search(line) for line in lines):
        raise AssertionError(f"No LOGIN command for user {user!r} found in command log for {account_id!r}:\n" + "\n".join(lines))


@then('the IMAP command log for "{account_id}" contains no LOGIN command for user "{user}"')
def step_command_log_contains_no_login_user(context: Context, account_id: str, user: str) -> None:
    lines = imap_command_lines(context, account_id)
    pattern = re.compile(rf'\bLOGIN\s+["\']?{re.escape(user)}["\']?\b', re.IGNORECASE)
    matching = [line for line in lines if pattern.search(line)]
    if matching:
        raise AssertionError(f"Forbidden LOGIN command for user {user!r} found in command log for {account_id!r}: {matching}")


@then('the IMAP command log for "{account_id}" contains {count:d} AUTHENTICATE XOAUTH2 command')
@then('the IMAP command log for "{account_id}" contains {count:d} AUTHENTICATE XOAUTH2 commands')
def step_command_log_contains_n_xoauth2(context: Context, account_id: str, count: int) -> None:
    lines = imap_command_lines(context, account_id)
    matching = [l for l in lines if "AUTHENTICATE XOAUTH2" in l.upper()]
    if len(matching) != count:
        raise AssertionError(f"Expected {count} AUTHENTICATE XOAUTH2 commands for {account_id!r}, found {len(matching)}:\n" + "\n".join(lines))


@then('the IMAP command log for "{account_id}" contains {count:d} commands')
@then('the IMAP command log for "{account_id}" contains {count:d} command')
def step_command_log_contains_n_commands(context: Context, account_id: str, count: int) -> None:
    lines = imap_command_lines(context, account_id, allow_empty=True)
    instance, _user = resolve_account(account_id)
    if instance != "mock-gmail":
        proxy_proc = (getattr(context, "imap_proxy_procs", None) or {}).get(account_id)
        if proxy_proc is None or proxy_proc.poll() is not None:
            raise AssertionError(f"No running IMAP proxy captured for account {account_id!r}.")
    if len(lines) != count:
        raise AssertionError(f"Expected {count} commands for {account_id!r}, found {len(lines)}:\n" + "\n".join(lines))


@then('the IMAP command log for "{account_id}" contains a {verb} command')
def step_command_log_contains_verb(context: Context, account_id: str, verb: str) -> None:
    lines = imap_command_lines(context, account_id)
    target_verb = verb.upper()
    if not any(_verb(line) == target_verb or line.upper().startswith(target_verb) for line in lines):
        raise AssertionError(f"No {verb!r} command found in command log for {account_id!r}:\n" + "\n".join(lines))


@then('the IMAP command log for "{account_id}" contains no {verb} command')
def step_command_log_contains_no_verb(context: Context, account_id: str, verb: str) -> None:
    instance, _user = resolve_account(account_id)
    if instance == "mock-gmail":
        from features.steps.gmail_steps import _format_log, _server_command_log

        records = _server_command_log(context, account_id)
        if not records:
            raise AssertionError(
                f"Mock-gmail command log for {account_id!r} is empty; "
                f"absence of {verb} cannot be witnessed."
            )
        target = verb.upper()
        hits = [r for r in records if r.command.upper() == target or r.command.upper() == f"UID {target}"]
        if hits:
            raise AssertionError(f"Unexpected {verb} in the command log:\n{_format_log(records)}")
        return

    log_path = (getattr(context, "imap_proxy_log_paths", None) or {}).get(account_id)
    proxy_proc = (getattr(context, "imap_proxy_procs", None) or {}).get(account_id)
    if log_path is None or proxy_proc is None or proxy_proc.poll() is not None:
        raise AssertionError(
            f"No running IMAP proxy captures the traffic of {account_id!r}; "
            f"the absence of {verb} cannot be witnessed."
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
    lines = imap_command_lines(context, account_id, allow_empty=True)
    target_verb = verb.upper()
    matching = [line for line in lines if _verb(line) == target_verb or line.upper().startswith(target_verb)]
    if matching:
        raise AssertionError(f"Forbidden {verb!r} command found in command log for {account_id!r}: {matching}")


@then('the IMAP command log for "{account_id}" contains at least {count:d} NOOP commands')
def step_command_log_at_least_n_noop(context: Context, account_id: str, count: int) -> None:
    lines = imap_command_lines(context, account_id)
    matching = [l for l in lines if _verb(l) == "NOOP" or l.upper().startswith("NOOP")]
    if len(matching) < count:
        raise AssertionError(f"Expected at least {count} NOOP commands for {account_id!r}, found {len(matching)}:\n" + "\n".join(lines))


@then('the IMAP command log for "{account_id}" contains {count:d} LOGIN or AUTHENTICATE command')
@then('the IMAP command log for "{account_id}" contains {count:d} LOGIN or AUTHENTICATE commands')
def step_command_log_n_login_or_auth(context: Context, account_id: str, count: int) -> None:
    instance, _user = resolve_account(account_id)
    if instance == "mock-gmail":
        from features.steps.gmail_steps import _server_command_log
        recs = [r for r in _server_command_log(context, account_id) if r.command in ("LOGIN", "AUTHENTICATE")]
        if len(recs) != count:
            raise AssertionError(f"Expected {count} LOGIN or AUTHENTICATE commands, found {len(recs)}")
        return
    lines = imap_command_lines(context, account_id)
    matching = [l for l in lines if _verb(l) in ("LOGIN", "AUTHENTICATE") or l.upper().startswith(("LOGIN", "AUTHENTICATE"))]
    if len(matching) != count:
        raise AssertionError(f"Expected {count} LOGIN or AUTHENTICATE commands for {account_id!r}, found {len(matching)}:\n" + "\n".join(lines))


@then('within {seconds:d} seconds the IMAP command log for "{account_id}" contains a {verb} command')
def step_command_log_contains_verb_within(context: Context, seconds: int, account_id: str, verb: str) -> None:
    import time
    deadline = time.monotonic() + seconds
    target_verb = verb.upper()
    while time.monotonic() < deadline:
        try:
            lines = imap_command_lines(context, account_id)
            if any(_verb(l) == target_verb or l.upper().startswith(target_verb) for l in lines):
                return
        except (OSError, FileNotFoundError, json.JSONDecodeError):
            pass
        time.sleep(0.1)
    raise AssertionError(f"Command {verb!r} did not appear in command log for {account_id!r} within {seconds}s")


@given("the mock-gmail server delays each UID FETCH response by {delay:d} milliseconds")
def step_mock_gmail_delays_each(context: Context, delay: int) -> None:
    _gmail_state(context).set_fetch_delay_ms(delay, once=False)


@given("the mock-gmail server delays the next UID FETCH response by {delay:d} milliseconds")
def step_mock_gmail_delays_next(context: Context, delay: int) -> None:
    _gmail_state(context).set_fetch_delay_ms(delay, once=True)


@given("the server's connection pool is configured with max_size {max_size:d} and acquire_timeout {timeout:d} second")
@given("the server's connection pool is configured with max_size {max_size:d} and acquire_timeout {timeout:d} seconds")
def step_pool_max_size_acquire_timeout(context: Context, max_size: int, timeout: int) -> None:
    _set_server_env(context, "IMAP_MCP_POOL_MAX_SIZE", max_size)
    _set_server_env(context, "IMAP_MCP_POOL_ACQUIRE_TIMEOUT", timeout)


@then('{ok_count:d} response has result "{ok_result}" and {err_count:d} response has result "{err_result}" with error.type "{error_type}"')
@then('{ok_count:d} responses have result "{ok_result}" and {err_count:d} responses have result "{err_result}" with error.type "{error_type}"')
def step_responses_result_split(
    context: Context, ok_count: int, ok_result: str, err_count: int, err_result: str, error_type: str
) -> None:
    responses = getattr(context, "concurrent_responses", None)
    if responses is None:
        raise AssertionError("No concurrent calls were issued in this scenario.")
    ok_found = [r for r in responses if r.get("result") == ok_result]
    err_found = [
        r for r in responses
        if r.get("result") == err_result and (r.get("error") or {}).get("type") == error_type
    ]
    if len(ok_found) != ok_count:
        raise AssertionError(
            f"Expected {ok_count} responses with result={ok_result!r}, got {len(ok_found)}. Responses: {responses}"
        )
    if len(err_found) != err_count:
        raise AssertionError(
            f"Expected {err_count} responses with result={err_result!r} and error.type={error_type!r}, got {len(err_found)}. Responses: {responses}"
        )


@given('the IMAP proxy for "{account_id}" strips capability "{cap}" while keeping "{keep}"')
def step_imap_proxy_strips_capability_keeping(
    context: Context, account_id: str, cap: str, keep: str
) -> None:
    config_path = context.scratch_dir / f"imap-proxy-{account_id}.json"
    if config_path.exists():
        try:
            config = json.loads(config_path.read_text(encoding="utf-8"))
        except Exception:
            config = {}
    else:
        config = {}
    strips = list(config.get("strip_capabilities", []) or [])
    if cap not in strips:
        strips.append(cap)
    if keep in strips:
        strips.remove(keep)
    config["strip_capabilities"] = strips
    config_path.write_text(json.dumps(config), encoding="utf-8")


@given('the IMAP proxy for "{account_id}" strips capabilities "{c1}" and "{c2}"')
def step_imap_proxy_strips_two_capabilities(
    context: Context, account_id: str, c1: str, c2: str
) -> None:
    config_path = context.scratch_dir / f"imap-proxy-{account_id}.json"
    if config_path.exists():
        try:
            config = json.loads(config_path.read_text(encoding="utf-8"))
        except Exception:
            config = {}
    else:
        config = {}
    strips = list(config.get("strip_capabilities", []) or [])
    for c in (c1, c2):
        if c not in strips:
            strips.append(c)
    config["strip_capabilities"] = strips
    config_path.write_text(json.dumps(config), encoding="utf-8")


@given('the IMAP proxy for "{account_id}" rewrites UIDVALIDITY of "{folder}" to {val:d} on subsequent SELECT commands')
def step_imap_proxy_rewrites_uidvalidity(
    context: Context, account_id: str, folder: str, val: int
) -> None:
    config_path = context.scratch_dir / f"imap-proxy-{account_id}.json"
    if config_path.exists():
        try:
            config = json.loads(config_path.read_text(encoding="utf-8"))
        except Exception:
            config = {}
    else:
        config = {}
    config["rewrite_uidvalidity"] = {"mailbox": folder, "new_value": str(val)}
    config_path.write_text(json.dumps(config), encoding="utf-8")


@given('the IMAP proxy for "{account_id}" rejects the next {cmd} on "{folder}" once with "{resp}"')
def step_imap_proxy_rejects_next_cmd_on_folder_once(
    context: Context, account_id: str, cmd: str, folder: str, resp: str
) -> None:
    config_path = context.scratch_dir / f"imap-proxy-{account_id}.json"
    if config_path.exists():
        try:
            config = json.loads(config_path.read_text(encoding="utf-8"))
        except Exception:
            config = {}
    else:
        config = {}
    _, _, text = resp.partition(" ")
    spec = {
        "command": cmd.upper(),
        "mailbox": folder,
        "mode": "no",
        "response_text": text,
        "remaining": 1,
    }
    existing = list(config.get("inject_failure_on", []) or [])
    existing.append(spec)
    config["inject_failure_on"] = existing
    config_path.write_text(json.dumps(config), encoding="utf-8")


@given('the IMAP proxy for "{account_id}" rejects every {cmd} on "{folder}" with "{resp}"')
def step_imap_proxy_rejects_every_cmd_on_folder(
    context: Context, account_id: str, cmd: str, folder: str, resp: str
) -> None:
    config_path = context.scratch_dir / f"imap-proxy-{account_id}.json"
    if config_path.exists():
        try:
            config = json.loads(config_path.read_text(encoding="utf-8"))
        except Exception:
            config = {}
    else:
        config = {}
    _, _, text = resp.partition(" ")
    spec = {
        "command": cmd.upper(),
        "mailbox": folder,
        "mode": "no",
        "response_text": text,
        "remaining": None,
    }
    existing = list(config.get("inject_failure_on", []) or [])
    existing.append(spec)
    config["inject_failure_on"] = existing
    config_path.write_text(json.dumps(config), encoding="utf-8")


@given('the IMAP proxy for "{account_id}" rejects every UID EXPUNGE with "{resp}"')
def step_imap_proxy_rejects_every_uid_expunge(
    context: Context, account_id: str, resp: str
) -> None:
    config_path = context.scratch_dir / f"imap-proxy-{account_id}.json"
    if config_path.exists():
        try:
            config = json.loads(config_path.read_text(encoding="utf-8"))
        except Exception:
            config = {}
    else:
        config = {}
    _, _, text = resp.partition(" ")
    spec = {
        "command": "UID EXPUNGE",
        "mailbox": None,
        "mode": "no",
        "response_text": text,
        "remaining": None,
    }
    existing = list(config.get("inject_failure_on", []) or [])
    existing.append(spec)
    config["inject_failure_on"] = existing
    config_path.write_text(json.dumps(config), encoding="utf-8")


@given('the IMAP proxy for "{account_id}" rejects the next SELECT or EXAMINE of "{folder}" with "{resp}"')
def step_imap_proxy_rejects_select_or_examine(
    context: Context, account_id: str, folder: str, resp: str
) -> None:
    config_path = context.scratch_dir / f"imap-proxy-{account_id}.json"
    if config_path.exists():
        try:
            config = json.loads(config_path.read_text(encoding="utf-8"))
        except Exception:
            config = {}
    else:
        config = {}
    _, _, text = resp.partition(" ")
    existing = list(config.get("inject_failure_on", []) or [])
    for cmd in ("SELECT", "EXAMINE"):
        existing.append({
            "command": cmd,
            "mailbox": folder,
            "mode": "no",
            "response_text": text,
            "remaining": 1,
        })
    config["inject_failure_on"] = existing
    config_path.write_text(json.dumps(config), encoding="utf-8")


@given('the IMAP proxy for "{account_id}" rejects the next {cmd} once with "{resp}"')
def step_imap_proxy_rejects_next_cmd_once(
    context: Context, account_id: str, cmd: str, resp: str
) -> None:
    config_path = context.scratch_dir / f"imap-proxy-{account_id}.json"
    if config_path.exists():
        try:
            config = json.loads(config_path.read_text(encoding="utf-8"))
        except Exception:
            config = {}
    else:
        config = {}
    _, _, text = resp.partition(" ")
    spec = {
        "command": cmd.upper(),
        "mailbox": None,
        "mode": "no",
        "response_text": text,
        "remaining": 1,
    }
    existing = list(config.get("inject_failure_on", []) or [])
    existing.append(spec)
    config["inject_failure_on"] = existing
    config_path.write_text(json.dumps(config), encoding="utf-8")


@given('the IMAP proxy for "{account_id}" forwards the next {cmd} on "{folder}" and delays its response by {delay:d} second')
@given('the IMAP proxy for "{account_id}" forwards the next {cmd} on "{folder}" and delays its response by {delay:d} seconds')
def step_imap_proxy_forwards_next_delays(
    context: Context, account_id: str, cmd: str, folder: str, delay: int
) -> None:
    config_path = context.scratch_dir / f"imap-proxy-{account_id}.json"
    if config_path.exists():
        try:
            config = json.loads(config_path.read_text(encoding="utf-8"))
        except Exception:
            config = {}
    else:
        config = {}
    config["delay_command_seconds"] = {
        "command": cmd.upper(),
        "mailbox": folder,
        "seconds": delay,
        "remaining": 1,
    }
    config_path.write_text(json.dumps(config), encoding="utf-8")


@given('the IMAP proxy for "{account_id}" forwards the next {cmd} on "{folder}" and closes the connection before returning the tagged response')
def step_imap_proxy_forwards_and_closes(
    context: Context, account_id: str, cmd: str, folder: str
) -> None:
    config_path = context.scratch_dir / f"imap-proxy-{account_id}.json"
    if config_path.exists():
        try:
            config = json.loads(config_path.read_text(encoding="utf-8"))
        except Exception:
            config = {}
    else:
        config = {}
    spec = {
        "command": cmd.upper(),
        "mailbox": folder,
        "mode": "close_after_forward",
        "remaining": 1,
    }
    existing = list(config.get("inject_failure_on", []) or [])
    existing.append(spec)
    config["inject_failure_on"] = existing
    config_path.write_text(json.dumps(config), encoding="utf-8")


@given('the IMAP proxy for "{account_id}" refuses all incoming TCP connections')
@when('the IMAP proxy for "{account_id}" refuses all incoming TCP connections')
def step_imap_proxy_refuses_incoming(context: Context, account_id: str) -> None:
    procs = getattr(context, "imap_proxy_procs", {})
    proc = procs.get(account_id)
    if proc is not None:
        proc.terminate()
        try:
            proc.wait(timeout=2)
        except (subprocess.TimeoutExpired, OSError):
            proc.kill()
            try:
                proc.wait(timeout=1)
            except (subprocess.TimeoutExpired, OSError):
                pass
    config_path = context.scratch_dir / f"imap-proxy-{account_id}.json"
    if config_path.exists():
        try:
            config = json.loads(config_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            config = {}
    else:
        config = {}
    config["refuse_connections"] = True
    config_path.write_text(json.dumps(config), encoding="utf-8")


@when("{n:d} seconds elapse for the delayed upstream APPEND to finish")
@when("{n:d} second elapses for the delayed upstream APPEND to finish")
def step_seconds_elapse_append(context: Context, n: int) -> None:
    import time
    time.sleep(n)


@when("the test recovery pass runs {n:d} time")
@when("the test recovery pass runs {n:d} times")
def step_test_recovery_pass_runs(context: Context, n: int) -> None:
    from features.steps.mcp_steps import _ensure_mcp_client
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
    client = _ensure_mcp_client(context, caller_id)
    client.call_tool("_test_run_recovery", {"passes": n})


@given("the server append_timeout is configured to {timeout:d} second")
@given("the server append_timeout is configured to {timeout:d} seconds")
def step_server_append_timeout_configured(context: Context, timeout: int) -> None:
    env = getattr(context, "mcp_extra_env", None) or {}
    env["IMAP_MCP_APPEND_TIMEOUT"] = str(timeout)
    context.mcp_extra_env = env
    context.is_test_hooks = True


@then('the WAL entry for the returned tx_id has status "{status}" and retry_count {count:d}')
def step_wal_entry_status_retry_count(
    context: Context, status: str, count: int
) -> None:
    from support.wal_reader import WALReader
    reader = WALReader(context.wal_path)
    tx_id = (getattr(context, "last_response", None) or {}).get("tx_id")
    if not tx_id:
        txs = reader.all_transactions()
        if not txs:
            raise AssertionError("No WAL transactions found")
        tx = txs[-1]
    else:
        tx = reader.transaction(tx_id)
        if tx is None:
            raise AssertionError(f"No WAL transaction found with tx_id {tx_id!r}")
    if tx.status != status:
        raise AssertionError(f"Expected WAL tx status {status!r}, got {tx.status!r}")
    if tx.retry_count != count:
        raise AssertionError(f"Expected WAL tx retry_count {count}, got {tx.retry_count}")


@then('the WAL entry for the returned tx_id has retry_count {count:d}')
def step_wal_entry_retry_count(context: Context, count: int) -> None:
    from support.wal_reader import WALReader
    reader = WALReader(context.wal_path)
    tx_id = (getattr(context, "last_response", None) or {}).get("tx_id")
    tx = reader.transaction(tx_id) if tx_id else (reader.all_transactions() or [None])[-1]
    if tx is None:
        raise AssertionError("No WAL transaction found")
    if tx.retry_count != count:
        raise AssertionError(f"Expected WAL tx retry_count {count}, got {tx.retry_count}")
