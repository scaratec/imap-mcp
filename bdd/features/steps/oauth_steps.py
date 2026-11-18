"""Steps for OAuth2 PKCE bootstrap and XOAUTH2 lifecycle (ADR 0050, ADR 0059, ADR 0064)."""

from __future__ import annotations

import os
import subprocess
from urllib.parse import parse_qs, urlparse

import httpx
from behave import given, then, when
from behave.runner import Context

from features.environment import SERVER_BINARY


def _oauth_bootstrap_binary() -> str:
    bin_path = SERVER_BINARY.parent / "imap-mcp-oauth-bootstrap"
    return str(bin_path)


@given("the mock OAuth2 server is reset and healthy")
def step_mock_oauth_reset(context: Context) -> None:
    client = getattr(context, "mock_oauth_client", None)
    if client is not None:
        client.reset()


@given(
    'account "{account_id}" is configured for OAuth2 against the mock OAuth2 server '
    'and mock-gmail with scope "{scope}" and token_cache "{token_cache}"'
)
def step_configure_account_oauth2(
    context: Context, account_id: str, scope: str, token_cache: str
) -> None:
    from features.steps.policy_steps import _ensure_builder

    builder = _ensure_builder(context)
    builder.add_account(
        id=account_id,
        provider="google-mock",
        host="127.0.0.1",
        port=13143,
        user="test@scaratec.com",
        auth_type="xoauth2",
        secret_ref=f"secret://accounts/{account_id}/refresh_token",
        oauth_scope=scope,
        token_cache=token_cache,
    )
    builder.write()

    env = getattr(context, "mcp_extra_env", None) or {}
    env["IMAP_MCP_OAUTH_AUTH_URI"] = "http://127.0.0.1:19085/default/authorize"
    env["IMAP_MCP_OAUTH_TOKEN_URI"] = "http://127.0.0.1:19085/default/token"
    context.mcp_extra_env = env


@given(
    'account "{account_id}" is configured with provider "{provider}", auth "{auth_type}", '
    'oauth_scope "{scope}", and token_cache "{token_cache}"'
)
def step_configure_custom_oauth_account(
    context: Context, account_id: str, provider: str, auth_type: str, scope: str, token_cache: str
) -> None:
    from features.steps.policy_steps import _ensure_builder

    builder = _ensure_builder(context)
    actual_auth_type = "xoauth2" if auth_type == "oauth2" else auth_type
    builder.add_account(
        id=account_id,
        provider=provider,
        host="127.0.0.1",
        port=11143,
        user="test@scaratec.com",
        auth_type=actual_auth_type,
        secret_ref=f"secret://accounts/{account_id}/refresh_token",
        oauth_scope=scope,
        token_cache=token_cache,
    )
    builder.write()


@given('account "{account_id}" is configured with provider "google", oauth_scope "{scope}", and token_cache "{token_cache}"')
def step_configure_google_oauth_account(
    context: Context, account_id: str, scope: str, token_cache: str
) -> None:
    from features.steps.policy_steps import _ensure_builder

    builder = _ensure_builder(context)
    builder.add_account(
        id=account_id,
        provider="google",
        host="127.0.0.1",
        port=13143,
        user="test@scaratec.com",
        auth_type="xoauth2",
        secret_ref=f"secret://accounts/{account_id}/refresh_token",
        oauth_scope=scope,
        token_cache=token_cache,
    )
    builder.write()


@given(
    'the mock OAuth2 server is primed to reject the next authorization_code exchange '
    'with OAuth error "{error}" and description "{desc}"'
)
def step_prime_auth_code_error(context: Context, error: str, desc: str) -> None:
    client = context.mock_oauth_client
    client.prime_token_error(error=error, description=desc, grant_type="authorization_code")


@given(
    'the mock OAuth2 server is primed to reject the next refresh_token exchange '
    'with OAuth error "{error}"'
)
def step_prime_refresh_error(context: Context, error: str) -> None:
    client = context.mock_oauth_client
    client.prime_token_error(error=error, grant_type="refresh_token")


@given("the mock OAuth2 server issues access tokens with expires_in {seconds:d} seconds")
def step_mock_oauth_set_lifetime(context: Context, seconds: int) -> None:
    client = context.mock_oauth_client
    client.set_token_lifetime(seconds)


@given('the secret store contains a valid refresh token for "{account_id}"')
@given('the secret store contains a refresh token for "{account_id}"')
def step_seed_refresh_token(context: Context, account_id: str) -> None:
    token_file = context.secrets_dir / "accounts" / account_id / "refresh_token"
    token_file.parent.mkdir(parents=True, exist_ok=True)
    token = "valid-mock-refresh-token"
    token_file.write_text(f"{token}\n", encoding="utf-8")
    token_file.chmod(0o600)
    oauth_client = getattr(context, "mock_oauth_client", None)
    if oauth_client is not None:
        oauth_client.register_refresh_token(token)


@given("the mock-gmail server revokes the currently cached XOAUTH2 access token once and closes every open IMAP connection")
def step_mock_gmail_revoke_token_once(context: Context) -> None:
    state = context.gmail_state
    state.revoke_current_token_once()
    state.close_all_connections()


def _run_oauth_bootstrap(
    context: Context, account_id: str, callback_mode: str = "success"
) -> tuple[int, str, str]:
    env = dict(os.environ)
    env["PYTHONUNBUFFERED"] = "1"
    env["IMAP_MCP_CONFIG_DIR"] = str(context.config_dir)
    env["IMAP_MCP_OAUTH_AUTH_URI"] = "http://127.0.0.1:19085/default/authorize"
    env["IMAP_MCP_OAUTH_TOKEN_URI"] = "http://127.0.0.1:19085/default/token"
    if getattr(context, "mcp_extra_env", None):
        env.update(context.mcp_extra_env)

    proc = subprocess.Popen(
        [_oauth_bootstrap_binary(), "--account", account_id],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
        text=True,
    )

    # Read the prompt and URL
    if proc.stdout is None:
        raise AssertionError("Bootstrap process stdout is None")
    stdout_lines: list[str] = []
    auth_url = ""
    while True:
        line = proc.stdout.readline()
        if not line:
            break
        stdout_lines.append(line)
        if "http://127.0.0.1:19085/default/authorize" in line:
            auth_url = line.strip()
        if line.strip().endswith("paste it below:"):
            break

    if not auth_url:
        stdout_rest, stderr_rest = proc.communicate(timeout=5.0)
        stdout_lines.append(stdout_rest)
        return proc.returncode, "".join(stdout_lines), stderr_rest

    # Parse state and parameters from auth_url
    parsed = urlparse(auth_url)
    q = parse_qs(parsed.query)
    state = q.get("state", [""])[0]

    if callback_mode == "access_denied":
        redirect_url = f"http://localhost:8080/callback?error=access_denied&state={state}"
    elif callback_mode == "tampered_state":
        # Follow authorize redirect to get real code
        with httpx.Client(follow_redirects=False) as http:
            r = http.get(auth_url)
            if r.status_code == 200:
                r = http.post(auth_url, data={"username": "test-user"})
            loc = r.headers.get("location", "")
        code = parse_qs(urlparse(loc).query).get("code", ["code123"])[0]
        redirect_url = f"http://localhost:8080/callback?code={code}&state=tampered_state_value"
    else:
        # Success callback: execute authorization against mock server
        with httpx.Client(follow_redirects=False) as http:
            r = http.get(auth_url)
            if r.status_code == 200:
                r = http.post(auth_url, data={"username": "test-user"})
            redirect_url = r.headers.get("location", "")

    if proc.stdin is None:
        raise AssertionError("Bootstrap process stdin is None")
    proc.stdin.write(redirect_url + "\n")
    proc.stdin.flush()

    stdout_rest, stderr_rest = proc.communicate(timeout=5.0)
    stdout_lines.append(stdout_rest)
    return proc.returncode, "".join(stdout_lines), stderr_rest


@when("the operator runs `imap-mcp-oauth-bootstrap --account {account_id}` and completes the browser consent callback")
def step_operator_bootstrap_success(context: Context, account_id: str) -> None:
    rc, out, err = _run_oauth_bootstrap(context, account_id, callback_mode="success")
    context.bootstrap_returncode = rc
    context.bootstrap_stdout = out
    context.bootstrap_stderr = err


@when("the operator runs `imap-mcp-oauth-bootstrap --account {account_id}` and the callback returns error \"{err_name}\"")
def step_operator_bootstrap_error(context: Context, account_id: str, err_name: str) -> None:
    rc, out, err = _run_oauth_bootstrap(context, account_id, callback_mode=err_name)
    context.bootstrap_returncode = rc
    context.bootstrap_stdout = out
    context.bootstrap_stderr = err


@when("the operator runs `imap-mcp-oauth-bootstrap --account {account_id}` and the callback returns a tampered state value")
def step_operator_bootstrap_tampered(context: Context, account_id: str) -> None:
    rc, out, err = _run_oauth_bootstrap(context, account_id, callback_mode="tampered_state")
    context.bootstrap_returncode = rc
    context.bootstrap_stdout = out
    context.bootstrap_stderr = err


@when("the operator runs `imap-mcp-oauth-bootstrap --account {account_id}`")
def step_operator_bootstrap_simple(context: Context, account_id: str) -> None:
    rc, out, err = _run_oauth_bootstrap(context, account_id, callback_mode="success")
    context.bootstrap_returncode = rc
    context.bootstrap_stdout = out
    context.bootstrap_stderr = err


@then("the bootstrap exits with code {code:d}")
def step_bootstrap_exits_code(context: Context, code: int) -> None:
    actual = getattr(context, "bootstrap_returncode", None)
    if actual != code:
        err = getattr(context, "bootstrap_stderr", "")
        out = getattr(context, "bootstrap_stdout", "")
        raise AssertionError(f"Expected bootstrap exit code {code}, got {actual}.\nStderr:\n{err}\nStdout:\n{out}")


@then('the bootstrap exits with a non-zero code and stderr mentioning "{text}"')
def step_bootstrap_exits_nonzero_mentioning(context: Context, text: str) -> None:
    rc = getattr(context, "bootstrap_returncode", None)
    if rc == 0:
        raise AssertionError("Expected bootstrap to fail with non-zero exit code, but it exited 0")
    err = getattr(context, "bootstrap_stderr", "")
    if text not in err:
        raise AssertionError(f"Expected bootstrap stderr mentioning {text!r}, got:\n{err}")


@then("the mock OAuth2 token endpoint recorded {count:d} token request")
@then("the mock OAuth2 token endpoint recorded {count:d} token requests")
def step_mock_oauth_recorded_token_requests(context: Context, count: int) -> None:
    client = context.mock_oauth_client
    actual = client.token_exchange_count()
    if actual != count:
        raise AssertionError(f"Expected {count} token exchange requests, but mock recorded {actual}")


@then('the secret store file "{rel_path}" exists with mode {mode:o} and non-empty content')
def step_secret_store_file_mode_non_empty(context: Context, rel_path: str, mode: int) -> None:
    path = context.secrets_dir / rel_path
    if not path.is_file():
        raise AssertionError(f"Secret store file {path} does not exist")
    actual_mode = path.stat().st_mode & 0o777
    if actual_mode != mode:
        raise AssertionError(f"File {path} mode: expected {oct(mode)}, got {oct(actual_mode)}")
    content = path.read_text(encoding="utf-8")
    if not content.strip():
        raise AssertionError(f"File {path} is empty")


@then('the secret store file "{rel_path}" exists with mode {mode:o}')
def step_secret_store_file_mode(context: Context, rel_path: str, mode: int) -> None:
    path = context.secrets_dir / rel_path
    if not path.is_file():
        raise AssertionError(f"Secret store file {path} does not exist")
    actual_mode = path.stat().st_mode & 0o777
    if actual_mode != mode:
        raise AssertionError(f"File {path} mode: expected {oct(mode)}, got {oct(actual_mode)}")


@then('the secret store file "{rel_path}" exists')
def step_secret_store_file_exists(context: Context, rel_path: str) -> None:
    path = context.secrets_dir / rel_path
    if not path.is_file():
        raise AssertionError(f"Secret store file {path} does not exist")


@then('no file exists in the secret store at "{rel_path}"')
def step_no_file_in_secret_store(context: Context, rel_path: str) -> None:
    path = context.secrets_dir / rel_path
    if path.exists():
        raise AssertionError(f"File {path} exists when it should not")


@given('{caller_id} calls list_folders with account "{account_id}"')
def step_given_caller_calls_list_folders(context: Context, caller_id: str, account_id: str) -> None:
    from features.steps.mcp_steps import _ensure_mcp_client, _store_result

    client = _ensure_mcp_client(context, caller_id)
    payload = client.call_tool("list_folders", {"account": account_id})
    _store_result(context, payload)


@given('the shared fake clock file is set to "{ts}"')
@given('the shared fake clock file advances to "{ts}"')
@when('the shared fake clock file advances to "{ts}"')
def step_shared_fake_clock_set(context: Context, ts: str) -> None:
    clock_file = context.scratch_dir / "audit-clock"
    clock_file.write_text(ts + "\n", encoding="utf-8")
    extra = getattr(context, "mcp_extra_env", None) or {}
    extra["IMAP_MCP_FAKE_NOW_FILE"] = str(clock_file)
    context.mcp_extra_env = extra


@then('the account entry for "{account_id}" has field "{field}" equal to "{expected}"')
def step_account_entry_field_equal(
    context: Context, account_id: str, field: str, expected: str
) -> None:
    resp = getattr(context, "last_response", None) or {}
    accounts = resp.get("accounts", [])
    account = next((a for a in accounts if a.get("id") == account_id), None)
    if account is None:
        raise AssertionError(f"No account entry for {account_id!r} in {accounts!r}")
    actual = str(account.get(field))
    if actual != expected:
        raise AssertionError(
            f"Account entry for {account_id!r} field {field!r}: expected {expected!r}, got {actual!r}"
        )
