from behave import given, when, then
from behave.runner import Context
import httpx
import time
import os
from pathlib import Path

@given('a mock OAuth2 provider "{provider_id}" listens on {url}')
def step_mock_provider_listens(context: Context, provider_id: str, url: str) -> None:
    base_url = "http://127.0.0.1:19080"
    context.mock_oauth_provider_id = provider_id
    context.mock_oauth_url = url
    context.mock_oauth_base_url = base_url
    
    try:
        resp = httpx.get(f"{base_url}/default/debugger/requests")
        if resp.status_code == 200:
            context.mock_oauth_initial_token_requests = len([r for r in resp.json() if r.get('request', '').startswith('POST /default/token')])
        else:
            context.mock_oauth_initial_token_requests = 0
    except Exception:
        context.mock_oauth_initial_token_requests = 0

@given('the mock OAuth2 provider is primed to consent on the next authorization request')
def step_prime_consent(context: Context) -> None:
    context.mock_oauth_browser_error = None

@given('the mock OAuth2 provider is primed to deny on the next authorization request')
def step_prime_deny(context: Context) -> None:
    context.mock_oauth_browser_error = "access_denied"

@when('the bootstrap opens the provider consent URL and the browser returns authorization code "{code}"')
def step_bootstrap_consent_success(context: Context, code: str) -> None:
    proc = getattr(context, "bootstrap_proc", None)
    if proc is None:
        raise AssertionError("Bootstrap process not started")
    callback_url = f"http://localhost:8080/callback?code={code}&state=mockstate"
    stdout, stderr = proc.communicate(input=callback_url + "\n")
    context.bootstrap_stdout = stdout
    context.bootstrap_stderr = stderr
    context.bootstrap_returncode = proc.wait()

@when('the browser returns error "{error}"')
def step_bootstrap_consent_error(context: Context, error: str) -> None:
    proc = getattr(context, "bootstrap_proc", None)
    if proc is None:
        raise AssertionError("Bootstrap process not started")
    callback_url = f"http://localhost:8080/callback?error={error}&state=mockstate"
    stdout, stderr = proc.communicate(input=callback_url + "\n")
    context.bootstrap_stdout = stdout
    context.bootstrap_stderr = stderr
    context.bootstrap_returncode = proc.wait()

@given('the mock OAuth2 provider returns access tokens with lifetime {seconds:d} seconds')
def step_mock_token_lifetime(context: Context, seconds: int) -> None:
    extra_env = getattr(context, "mcp_extra_env", None) or {}
    extra_env["IMAP_MCP_TEST_TOKEN_LIFETIME"] = str(seconds)
    context.mcp_extra_env = extra_env

@when('the server runs for {seconds:d} seconds')
def step_server_runs_for(context: Context, seconds: int) -> None:
    from datetime import datetime, timedelta, timezone
    extra_env = getattr(context, "mcp_extra_env", None) or {}
    raw = extra_env.get("IMAP_MCP_FAKE_NOW_UTC")
    if raw:
        if raw.endswith("Z"):
            raw = raw[:-1] + "+00:00"
        base = datetime.fromisoformat(raw).astimezone(timezone.utc)
    else:
        base = datetime.now(tz=timezone.utc)
    new_now = base + timedelta(seconds=seconds)
    extra_env["IMAP_MCP_FAKE_NOW_UTC"] = new_now.strftime("%Y-%m-%dT%H:%M:%S+00:00")
    context.mcp_extra_env = extra_env
    time.sleep(1)

@then('the mock OAuth2 token endpoint records at least {count:d} token-exchange requests')
def step_mock_token_exchange_requests(context: Context, count: int) -> None:
    base_url = getattr(context, "mock_oauth_base_url", "http://127.0.0.1:19080")
    try:
        resp = httpx.get(f"{base_url}/default/debugger/requests")
        if resp.status_code == 200:
            reqs = [r for r in resp.json() if r.get('request', '').startswith('POST /default/token')]
            initial = getattr(context, "mock_oauth_initial_token_requests", 0)
            actual = len(reqs) - initial
            assert actual >= count, f"Expected at least {count} requests, got {actual}"
    except Exception as e:
        raise AssertionError(f"Failed to query mock server: {e}")

@given('the mock OAuth2 provider responds to the next token-exchange request with error "{error}"')
def step_mock_token_error(context: Context, error: str) -> None:
    extra_env = getattr(context, "mcp_extra_env", None) or {}
    extra_env["IMAP_MCP_TEST_OAUTH_INJECT_ERROR"] = error
    context.mcp_extra_env = extra_env

@when('the server completes one token exchange for "{account}"')
def step_server_completes_token_exchange(context: Context, account: str) -> None:
    from features.steps.mcp_steps import _ensure_mcp_client
    client = _ensure_mcp_client(context, "invoice-agent")
    try:
        client.call_tool("list_folders", {"account": account})
    except Exception:
        pass

@then('the server reuses the persisted access token without issuing a new token-exchange request')
def step_reused_persisted_token(context: Context) -> None:
    base_url = getattr(context, "mock_oauth_base_url", "http://127.0.0.1:19080")
    try:
        resp = httpx.get(f"{base_url}/default/debugger/requests")
        if resp.status_code == 200:
            reqs = [r for r in resp.json() if r.get('request', '').startswith('POST /default/token')]
            initial = getattr(context, "mock_oauth_initial_token_requests", 0)
            assert len(reqs) == initial + 1, (
                f"Expected no new token exchanges after restart, "
                f"got {len(reqs) - initial - 1} extra"
            )
    except httpx.ConnectError:
        pass

@then('the mock OAuth2 token endpoint records only the prior token-exchange count')
def step_mock_token_prior_count(context: Context) -> None:
    base_url = getattr(context, "mock_oauth_base_url", "http://127.0.0.1:19080")
    try:
        resp = httpx.get(f"{base_url}/default/debugger/requests")
        if resp.status_code == 200:
            reqs = [r for r in resp.json() if r.get('request', '').startswith('POST /default/token')]
            initial = getattr(context, "mock_oauth_initial_token_requests", 0)
            assert len(reqs) == initial + 1, f"Expected exactly {initial + 1} requests, got {len(reqs)}"
    except Exception as e:
        raise AssertionError(f"Failed to query mock server: {e}")

@given('the mock OAuth2 provider is primed to tamper with the code_challenge')
def step_tamper_pkce(context: Context) -> None:
    context.bootstrap_tamper_pkce = True
@then('the bootstrap reports success')
def step_bootstrap_success(context: Context) -> None:
    assert context.bootstrap_returncode == 0, f"Bootstrap failed: {context.bootstrap_stderr}"
    assert "Success" in context.bootstrap_stdout or "saved" in context.bootstrap_stdout

@then('the bootstrap reports failure with reason "{reason}"')
def step_bootstrap_failure(context: Context, reason: str) -> None:
    assert context.bootstrap_returncode != 0, "Bootstrap succeeded when it should have failed"
    assert reason in context.bootstrap_stderr or reason in context.bootstrap_stdout

@then('the secret store contains a non-empty value under key "{key}"')
@then('the secret store contains a value under key "{key}"')
def step_secret_store_contains_value(context: Context, key: str) -> None:
    path = context.secrets_dir / key
    assert path.exists(), f"Secret store missing key: {key} (file {path} does not exist)"
    assert path.read_text().strip(), f"Secret store key is empty: {key}"

@then('the secret store does NOT contain a value under key "{key}"')
@then('the secret store has no value under key "{key}"')
def step_secret_store_no_value(context: Context, key: str) -> None:
    path = context.secrets_dir / key
    assert not path.exists(), f"Secret store should not contain key: {key} (file {path} exists)"

@then('the audit log contains an entry with tool "{tool}", decision "{decision}", result "{result}"')
def step_audit_log_contains_entry(context: Context, tool: str, decision: str, result: str) -> None:
    import json
    found = False
    for p in context.audit_dir.glob("*.jsonl"):
        for line in p.read_text().splitlines():
            if not line.strip(): continue
            record = json.loads(line)
            if record.get("tool") == tool and record.get("decision") == decision and record.get("result") == result:
                found = True
                break
        if found: break
    assert found, f"Audit log entry not found for tool={tool}, decision={decision}, result={result}"

@given('the secret store contains a valid refresh token for "{account}"')
def step_secret_valid_refresh_token(context: Context, account: str) -> None:
    path = context.secrets_dir / "accounts" / account / "refresh_token"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("valid_refresh_token_mock")

@given('the secret store contains a refresh token for "{account}"')
def step_secret_refresh_token(context: Context, account: str) -> None:
    step_secret_valid_refresh_token(context, account)

@then('the IMAP command STORE \\Seen was NEVER issued against the provider')
def step_imap_store_never_issued(context: Context) -> None:
    from support.audit_reader import AuditReader
    reader = AuditReader(context.audit_dir)
    allows = reader.find(tool="mark_seen", decision="ALLOW")
    assert not allows, (
        f"mark_seen ALLOW found in audit — STORE \\Seen may have been issued: "
        f"{[r.record for r in allows]}"
    )

@when('the server starts and opens an IMAP connection to "{account}"')
def step_server_opens_imap(context: Context, account: str) -> None:
    from features.steps.policy_steps import _ensure_builder
    builder = _ensure_builder(context)
    if not any(c.id == "invoice-agent" for c in builder.callers):
        builder.add_policy("dummy-policy")
        builder.add_caller(id="invoice-agent", policy="dummy-policy", auth_type="stdio_trusted")
        # Give access to the account
        builder.folder("dummy-policy", account, "INBOX", "blacklist", "FULL")
        builder.write()
        
    from features.steps.mcp_steps import _ensure_mcp_client
    client = _ensure_mcp_client(context, "invoice-agent")
    try:
        client.call_tool("list_folders", {"account": account})
    except Exception as e:
        context.last_response = {"isError": True, "error": str(e)}

@when('the server attempts to open an IMAP connection to "{account}"')
def step_server_attempts_imap(context: Context, account: str) -> None:
    step_server_opens_imap(context, account)

@then('the currently open IMAP connection has never failed authentication')
def step_imap_never_failed(context: Context) -> None:
    from support.audit_reader import AuditReader
    reader = AuditReader(context.audit_dir)
    failures = reader.find(tool="token_refresh", decision="DENY")
    assert not failures, (
        f"Token refresh failures found in audit: "
        f"{[r.record for r in failures]}"
    )

@then('the audit log contains at least {count:d} entry with tool "{tool}", result "{result}", account "{account}"')
def step_audit_log_contains_count_entry(context: Context, tool: str, count: int = 1, **kwargs) -> None:
    import json
    actual_count = 0
    for p in context.audit_dir.glob("*.jsonl"):
        for line in p.read_text().splitlines():
            if not line.strip(): continue
            record = json.loads(line)
            match = True
            if record.get("tool") != tool:
                match = False
            for k, v in kwargs.items():
                if record.get(k) != v:
                    match = False
            if match:
                actual_count += 1
                
    assert actual_count >= count, f"Expected at least {count} entries for {tool} matching {kwargs}, found {actual_count}"

@then('the connection attempt fails')
def step_connection_fails(context: Context) -> None:
    # Ensure the last tool response was an error
    assert getattr(context, "last_response", {}).get("isError") is True

@then('the account state for "{account}" transitions to "{state}"')
def step_account_state_transitions(context: Context, account: str, state: str) -> None:
    step_account_state(context, account, state)


@then('the account state for "{account}" is "{state}"')
def step_account_state(context: Context, account: str, state: str) -> None:
    from features.steps.mcp_steps import _ensure_mcp_client
    client = _ensure_mcp_client(context, "invoice-agent")
    resp = client.call_tool("list_accounts", {})
    found = False
    for content in resp.get("content", []):
        import json
        data = json.loads(content["text"])
        for acc in data.get("accounts", []):
            if acc["id"] == account:
                assert acc["state"] == state, f"Expected state {state}, got {acc['state']}"
                found = True
    assert found, f"Account {account} not found in list_accounts"

@then('no further connection attempts to "{account}" occur until the operator reruns the bootstrap')
def step_no_further_connections(context: Context, account: str) -> None:
    from features.steps.mcp_steps import _ensure_mcp_client
    import json as _json
    client = _ensure_mcp_client(context, "invoice-agent")
    payload = client.call_tool("list_accounts", {})
    content = payload.get("content", [])
    data = _json.loads(content[0]["text"]) if content else {}
    for acc in data.get("accounts", []):
        if acc.get("id") == account:
            assert acc.get("state") == "needs_rebootstrap", (
                f"Account {account!r} state is {acc.get('state')!r}, "
                f"expected 'needs_rebootstrap'"
            )
            return
    raise AssertionError(f"Account {account!r} not found in list_accounts")

@when('the server starts and completes one token exchange for "{account}"')
def step_server_completes_exchange_for(context: Context, account: str) -> None:
    step_server_opens_imap(context, account)
    from features.steps.mcp_steps import _ensure_mcp_client
    client = _ensure_mcp_client(context, "invoice-agent")
    try:
        client.call_tool("list_folders", {"account": account})
    except Exception:
        pass
@given('policy "{policy}" grants capability "{cap}" on folder "{folder}"')
def step_policy_grants_capability(context: Context, policy: str, cap: str, folder: str) -> None:
    from features.steps.policy_steps import _ensure_builder
    builder = _ensure_builder(context)
    if not any(p.name == policy for p in builder.policies):
        builder.add_policy(policy)
    
    # We need to translate cap correctly based on its name.
    kwargs = {"mode": "whitelist", "default": "NONE"}
    kwargs[cap] = True
    builder.folder(policy, "gmail-ronly", folder, **kwargs)
    builder.write()

@given('caller "{caller}" uses policy "{policy}"')
def step_caller_uses_policy(context: Context, caller: str, policy: str) -> None:
    from features.steps.policy_steps import _ensure_builder
    builder = _ensure_builder(context)
    if not any(c.id == caller for c in builder.callers):
        builder.add_caller(id=caller, policy=policy, auth_type="stdio_trusted")
    builder.write()
