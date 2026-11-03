"""Steps that witness IMAP connections and commands (ADR 0013 pool).

Every Then reads a channel the server does not control: the mock-gmail
server's own connection bookkeeping and command log for mock-gmail
accounts, the logging IMAP proxy (support/imap_proxy.py) for
Dovecot-backed accounts. The proxy is wired in front of a witnessed
account before the server starts (`capture_witnessed_command_logs`).
"""

from __future__ import annotations

from pathlib import Path

from behave import given, then, use_step_matcher, when
from behave.runner import Context

from support.imap_fixture import resolve_account

use_step_matcher("parse")


# ----------------------------------------------------------------- helpers


def imap_command_lines(context: Context, account_id: str) -> list[str]:
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
    if not lines:
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


@then('the IMAP command log for "{account_id}" contains exactly {n:d} AUTHENTICATE command')
@then('the IMAP command log for "{account_id}" contains exactly {n:d} AUTHENTICATE commands')
def step_command_log_authenticate_count(context: Context, account_id: str, n: int) -> None:
    commands = imap_command_lines(context, account_id)
    hits = [c for c in commands if _verb(c) in _AUTHENTICATION_VERBS]
    if len(hits) != n:
        listing = "\n".join(f"  {c}" for c in commands)
        raise AssertionError(
            f"Expected {n} authentication command(s) for {account_id!r}, "
            f"found {len(hits)}:\n{listing}"
        )


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


@then("the mock-gmail server never held more than {n:d} IMAP connections at the same time")
def step_mock_gmail_high_water(context: Context, n: int) -> None:
    state = _gmail_state(context)
    peak = state.max_concurrent_connections
    if peak < 1:
        raise AssertionError("The mock-gmail server saw no connection in this scenario.")
    if peak > n:
        raise AssertionError(
            f"The mock-gmail server held {peak} IMAP connections at the same time; "
            f"at most {n} expected."
        )


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

    client = _ensure_mcp_client(context, caller_id)
    lookup = getattr(context, "message_uids", {})
    arguments = {"account": account, "folder": folder, "uid": lookup.get((account, folder, uid), uid)}
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
