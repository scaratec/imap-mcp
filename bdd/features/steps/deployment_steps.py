"""Steps for Kubernetes projected volume reload and sidecar health probe.

Covers:
  - bdd/features/deployment/health_probe.feature
  - bdd/features/deployment/projected_config_reload.feature
"""

from __future__ import annotations

import json
import os
import shutil
import stat
import time
from pathlib import Path

import httpx
import yaml
from behave import given, then, use_step_matcher, when
from behave.runner import Context

use_step_matcher("parse")

from support.mcp_client import MCPClientError, MCPHttpClient, MCPRPCError


def _server_binary(context: Context) -> Path:
    from features.environment import SERVER_BINARY
    return getattr(context, "server_binary", SERVER_BINARY)


def _atomic_symlink_swap(dir_path: Path, new_target: str, symlink_name: str = "..data") -> None:
    tmp_link = dir_path / f"..data_tmp_{time.time_ns()}"
    tmp_link.symlink_to(new_target)
    os.replace(tmp_link, dir_path / symlink_name)


# --------------------------------------------------------------------- Given


@given(
    'the projected ConfigMap and Secret volumes are initialized for caller '
    '"{caller_id}" with bearer token "{token}" and policy "{policy_name}" '
    'granting whitelist hornbach.de -> ENVELOPE on "{account}:{folder}"'
)
def step_init_projected_volumes(
    context: Context, caller_id: str, token: str, policy_name: str, account: str, folder: str
) -> None:
    config_dir = context.scratch_dir / "projected_config"
    secrets_dir = context.scratch_dir / "projected_secrets"
    config_dir.mkdir(parents=True, exist_ok=True)
    secrets_dir.mkdir(parents=True, exist_ok=True)

    ts_config = config_dir / f"..{time.time_ns()}_config"
    ts_config.mkdir()

    # accounts.yaml
    accounts_data = {
        "accounts": [
            {
                "id": account,
                "host": "127.0.0.1",
                "port": 11143,
                "user": "gupta",
                "auth": {
                    "type": "password",
                    "secret_ref": f"secret://accounts/{account}/password",
                },
            }
        ],
        "secret_store": {
            "backend": "file_dir",
            "path": str(secrets_dir),
            "acknowledge_plaintext": True,
        },
        "audit": {"directory": str(context.audit_dir)},
        "wal": {"path": str(context.wal_path)},
    }
    (ts_config / "accounts.yaml").write_text(yaml.safe_dump(accounts_data), encoding="utf-8")

    # callers.yaml
    callers_data = {
        "callers": [
            {
                "id": caller_id,
                "policy": policy_name,
                "auth": {
                    "type": "shared_token",
                    "token_secret_ref": f"secret://callers/{caller_id}/token",
                },
            }
        ]
    }
    (ts_config / "callers.yaml").write_text(yaml.safe_dump(callers_data), encoding="utf-8")

    # policies/<policy_name>.yaml
    pol_dir = ts_config / "policies"
    pol_dir.mkdir()
    policy_data = {
        "name": policy_name,
        "accounts": {
            account: [
                {
                    "path": folder,
                    "mode": "whitelist",
                    "default": "NONE",
                    "rules": [{"match": {"from_domain": "hornbach.de"}, "grant": "ENVELOPE"}],
                }
            ]
        },
    }
    (pol_dir / f"{policy_name}.yaml").write_text(yaml.safe_dump(policy_data), encoding="utf-8")

    _atomic_symlink_swap(config_dir, ts_config.name)
    (config_dir / "accounts.yaml").symlink_to("..data/accounts.yaml")
    (config_dir / "callers.yaml").symlink_to("..data/callers.yaml")
    (config_dir / "policies").symlink_to("..data/policies")

    # Secrets
    ts_secrets = secrets_dir / f"..{time.time_ns()}_secrets"
    ts_secrets.mkdir()
    tok_file = ts_secrets / "callers" / caller_id / "token"
    tok_file.parent.mkdir(parents=True, exist_ok=True)
    tok_file.write_text(token, encoding="utf-8")

    pw_file = ts_secrets / "accounts" / account / "password"
    pw_file.parent.mkdir(parents=True, exist_ok=True)
    pw_file.write_text("test123", encoding="utf-8")

    _atomic_symlink_swap(secrets_dir, ts_secrets.name)
    (secrets_dir / "callers").symlink_to("..data/callers")
    (secrets_dir / "accounts").symlink_to("..data/accounts")

    context.projected_config_dir = config_dir
    context.projected_secrets_dir = secrets_dir
    context.projected_current_config_ts = ts_config
    context.projected_current_secrets_ts = ts_secrets
    context.superseded_config_dirs = []


@given(
    'the server process is started from the projected volumes with transport '
    '"http" on 127.0.0.1 and a random port'
)
def step_start_server_projected_volumes(context: Context) -> None:
    from features.steps.policy_steps import flush_staged_messages

    flush_staged_messages(context)

    extra_env = dict(getattr(context, "mcp_extra_env", {}) or {})
    if getattr(context, "is_test_hooks", False) and not getattr(context, "without_test_mode", False):
        extra_env.setdefault("IMAP_MCP_TEST_MODE", "1")

    client = MCPHttpClient(
        server_binary=_server_binary(context),
        config_dir=context.projected_config_dir,
        extra_env=extra_env,
    )
    client.start_server()
    context.mcp_http = client


@given("the test runner is not executing as root")
def step_assert_not_root(context: Context) -> None:
    if os.geteuid() == 0:
        raise AssertionError("BDD suite must not be run as root; file permission checks would be bypassed.")


@when("no signal is sent to the server")
def step_no_signal_sent(context: Context) -> None:
    http_client = getattr(context, "mcp_http", None)
    proc = (
        getattr(http_client, "_proc", None)
        or getattr(context, "mcp_server_proc", None)
        or getattr(context, "startup_proc", None)
    )
    if proc is None:
        raise AssertionError("No server process found to verify alive state.")
    if proc.poll() is not None:
        raise AssertionError(f"Server exited prematurely with {proc.returncode}")


@given("the audit directory and current day file are made read-only")
@when("the audit directory and current day file are made read-only")
def step_audit_dir_make_readonly(context: Context) -> None:
    for root, dirs, files in os.walk(context.audit_dir):
        for f in files:
            p = Path(root) / f
            p.chmod(stat.S_IRUSR)
        for d in dirs:
            p = Path(root) / d
            p.chmod(stat.S_IRUSR | stat.S_IXUSR)
    context.audit_dir.chmod(stat.S_IRUSR | stat.S_IXUSR)


@when("writability of the audit directory and current day file is restored")
def step_audit_dir_restore_writability(context: Context) -> None:
    context.audit_dir.chmod(stat.S_IRWXU)
    for root, dirs, files in os.walk(context.audit_dir):
        for d in dirs:
            (Path(root) / d).chmod(stat.S_IRWXU)
        for f in files:
            (Path(root) / f).chmod(stat.S_IRUSR | stat.S_IWUSR)


# --------------------------------------------------------------------- When (HTTP & Probes)


@then(
    'a new MCP HTTP Initialize handshake with caller_id "{caller_id}" '
    'and bearer token "{token}" succeeds'
)
def step_new_handshake_succeeds(context: Context, caller_id: str, token: str) -> None:
    client: MCPHttpClient = context.mcp_http
    new_client = MCPHttpClient(
        server_binary=client.server_binary,
        config_dir=client.config_dir,
        host=client.host,
        port=client.port,
    )
    new_client._proc = client._proc
    new_client.initialize(caller_id, token)
    context.mcp_http = new_client


@then(
    'a new MCP HTTP Initialize handshake with caller_id "{caller_id}" '
    'and bearer token "{token}" fails with error "{error}"'
)
def step_new_handshake_fails(context: Context, caller_id: str, token: str, error: str) -> None:
    client: MCPHttpClient = context.mcp_http
    new_client = MCPHttpClient(
        server_binary=client.server_binary,
        config_dir=client.config_dir,
        host=client.host,
        port=client.port,
    )
    new_client._proc = client._proc
    failed = False
    exc_obj: Exception | None = None
    try:
        new_client.initialize(caller_id, token)
    except (MCPRPCError, MCPClientError, httpx.HTTPError, RuntimeError) as exc:
        failed = True
        exc_obj = exc

    if not failed:
        raise AssertionError("Handshake unexpectedly succeeded")
    if exc_obj is not None and error not in str(exc_obj):
        raise AssertionError(f"Expected error mentioning {error!r}, got {exc_obj!r}") from exc_obj


@when(
    'the MCP client calls {tool_name} on the established HTTP session with '
    'account "{account}", folder "{folder}", uid {uid:d}'
)
def step_http_call_tool_uid(
    context: Context, tool_name: str, account: str, folder: str, uid: int
) -> None:
    from features.steps.mcp_steps import _store_result
    from support.imap_fixture import resolve_uid

    client: MCPHttpClient = context.mcp_http
    real_uid = resolve_uid(context, account, folder, uid, allow_unseeded_probe=True)
    args = {"account": account, "folder": folder, "uid": real_uid}
    payload = client.call_tool(tool_name, args)
    _store_result(context, payload)


@when("the MCP client calls get_caller_identity on the established HTTP session")
@when("the MCP client calls get_caller_identity on the established HTTP session without re-initializing")
def step_http_call_get_caller_identity(context: Context) -> None:
    from features.steps.mcp_steps import _store_result
    client: MCPHttpClient = context.mcp_http
    payload = client.call_tool("get_caller_identity", {})
    _store_result(context, payload)


@when(
    'the MCP client calls search on the established HTTP session with account "{account}", '
    'folder "{folder}", criteria {criteria_raw}, scope "{scope}"'
)
def step_http_call_search(
    context: Context, account: str, folder: str, criteria_raw: str, scope: str
) -> None:
    from features.steps.mcp_steps import _store_result
    client: MCPHttpClient = context.mcp_http
    criteria = json.loads(criteria_raw)
    payload = client.call_tool(
        "search",
        {"account": account, "folder": folder, "criteria": criteria, "scope": scope},
    )
    _store_result(context, payload)


@when(
    'the MCP client sends a second Initialize request on the same HTTP session '
    'claiming caller_id "{caller_id}"'
)
def step_http_second_initialize(context: Context, caller_id: str) -> None:
    client: MCPHttpClient = context.mcp_http
    url = f"http://{client.host}:{client.port}/mcp/"
    headers = {
        "Authorization": f"Bearer {client.bearer_token}",
        "X-MCP-Caller-ID": caller_id,
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
    }
    if client._session_id:
        headers["mcp-session-id"] = client._session_id

    req = {
        "jsonrpc": "2.0",
        "id": "re-init",
        "method": "initialize",
        "params": {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": client.client_name, "version": client.client_version},
        },
    }
    res = httpx.post(url, json=req, headers=headers, timeout=5.0)
    context.last_second_init_response = res


@then('the second Initialize request is rejected with error "{error}"')
def step_second_init_rejected(context: Context, error: str) -> None:
    res: httpx.Response = context.last_second_init_response
    text = res.text
    if res.status_code == 200:
        data = res.json()
        err_msg = json.dumps(data.get("error", ""))
    else:
        err_msg = text
    if error not in err_msg:
        raise AssertionError(f"Expected rejection mentioning {error!r}, got {res.status_code}: {err_msg}")


# --------------------------------------------------------------------- When (Atomic Swaps)


@when(
    'the projected ConfigMap is atomically swapped so policy "{policy_name}" '
    'grants blacklist default ENVELOPE on "{account}:{folder}"'
)
def step_swap_configmap_envelope(
    context: Context, policy_name: str, account: str, folder: str
) -> None:
    config_dir: Path = context.projected_config_dir
    new_ts = config_dir / f"..{time.time_ns()}_config"
    new_ts.mkdir()

    # Copy accounts.yaml and callers.yaml from current
    curr: Path = context.projected_current_config_ts
    shutil.copy(curr / "accounts.yaml", new_ts / "accounts.yaml")
    shutil.copy(curr / "callers.yaml", new_ts / "callers.yaml")

    pol_dir = new_ts / "policies"
    pol_dir.mkdir()
    policy_data = {
        "name": policy_name,
        "accounts": {
            account: [
                {
                    "path": folder,
                    "mode": "blacklist",
                    "default": "ENVELOPE",
                    "rules": [],
                }
            ]
        },
    }
    (pol_dir / f"{policy_name}.yaml").write_text(yaml.safe_dump(policy_data), encoding="utf-8")

    context.superseded_config_dirs.append(curr)
    _atomic_symlink_swap(config_dir, new_ts.name)
    context.projected_current_config_ts = new_ts


@when(
    'the projected ConfigMap is atomically swapped so policy "{policy_name}" '
    'grants blacklist default BODY on "{account}:{folder}"'
)
def step_swap_configmap_body(
    context: Context, policy_name: str, account: str, folder: str
) -> None:
    config_dir: Path = context.projected_config_dir
    new_ts = config_dir / f"..{time.time_ns()}_config"
    new_ts.mkdir()

    curr: Path = context.projected_current_config_ts
    shutil.copy(curr / "accounts.yaml", new_ts / "accounts.yaml")
    shutil.copy(curr / "callers.yaml", new_ts / "callers.yaml")

    pol_dir = new_ts / "policies"
    pol_dir.mkdir()
    policy_data = {
        "name": policy_name,
        "accounts": {
            account: [
                {
                    "path": folder,
                    "mode": "blacklist",
                    "default": "BODY",
                    "rules": [],
                }
            ]
        },
    }
    (pol_dir / f"{policy_name}.yaml").write_text(yaml.safe_dump(policy_data), encoding="utf-8")

    context.superseded_config_dirs.append(curr)
    _atomic_symlink_swap(config_dir, new_ts.name)
    context.projected_current_config_ts = new_ts


@when(
    'the projected ConfigMap is atomically swapped with malformed YAML in "{filename}"'
)
def step_swap_configmap_malformed(context: Context, filename: str) -> None:
    config_dir: Path = context.projected_config_dir
    new_ts = config_dir / f"..{time.time_ns()}_config"
    new_ts.mkdir()

    curr: Path = context.projected_current_config_ts
    shutil.copy(curr / "accounts.yaml", new_ts / "accounts.yaml")
    shutil.copy(curr / "callers.yaml", new_ts / "callers.yaml")

    pol_dir = new_ts / "policies"
    pol_dir.mkdir()
    (pol_dir / filename).write_text(":::MALFORMED YAML:::", encoding="utf-8")

    context.superseded_config_dirs.append(curr)
    _atomic_symlink_swap(config_dir, new_ts.name)
    context.projected_current_config_ts = new_ts


@when(
    'the projected ConfigMap is atomically swapped adding policy "{new_policy}" '
    '(blacklist default ENVELOPE on "{account}:{folder}") and pointing caller '
    '"{caller_id}" to "{new_policy}"'
)
def step_swap_configmap_add_policy(
    context: Context, new_policy: str, account: str, folder: str, caller_id: str
) -> None:
    config_dir: Path = context.projected_config_dir
    new_ts = config_dir / f"..{time.time_ns()}_config"
    new_ts.mkdir()

    curr: Path = context.projected_current_config_ts
    shutil.copy(curr / "accounts.yaml", new_ts / "accounts.yaml")

    # callers.yaml pointing to new policy
    callers_data = {
        "callers": [
            {
                "id": caller_id,
                "policy": new_policy,
                "auth": {
                    "type": "shared_token",
                    "token_secret_ref": f"secret://callers/{caller_id}/token",
                },
            }
        ]
    }
    (new_ts / "callers.yaml").write_text(yaml.safe_dump(callers_data), encoding="utf-8")

    pol_dir = new_ts / "policies"
    pol_dir.mkdir()
    # Keep old policies
    if (curr / "policies").is_dir():
        for p in (curr / "policies").glob("*.yaml"):
            shutil.copy(p, pol_dir / p.name)

    # Add new policy
    new_pol_data = {
        "name": new_policy,
        "accounts": {
            account: [
                {
                    "path": folder,
                    "mode": "blacklist",
                    "default": "ENVELOPE",
                    "rules": [],
                }
            ]
        },
    }
    (pol_dir / f"{new_policy}.yaml").write_text(yaml.safe_dump(new_pol_data), encoding="utf-8")

    context.superseded_config_dirs.append(curr)
    _atomic_symlink_swap(config_dir, new_ts.name)
    context.projected_current_config_ts = new_ts


@when(
    'the projected ConfigMap is atomically swapped removing "{filename}" while caller '
    '"{caller_id}" still references "{policy_name}"'
)
def step_swap_configmap_remove_policy(
    context: Context, filename: str, caller_id: str, policy_name: str
) -> None:
    config_dir: Path = context.projected_config_dir
    new_ts = config_dir / f"..{time.time_ns()}_config"
    new_ts.mkdir()

    curr: Path = context.projected_current_config_ts
    shutil.copy(curr / "accounts.yaml", new_ts / "accounts.yaml")
    shutil.copy(curr / "callers.yaml", new_ts / "callers.yaml")

    pol_dir = new_ts / "policies"
    pol_dir.mkdir()
    # Copy all policies EXCEPT filename
    if (curr / "policies").is_dir():
        for p in (curr / "policies").glob("*.yaml"):
            if p.name != filename:
                shutil.copy(p, pol_dir / p.name)

    context.superseded_config_dirs.append(curr)
    _atomic_symlink_swap(config_dir, new_ts.name)
    context.projected_current_config_ts = new_ts


@when("the superseded ConfigMap projection directory is deleted")
def step_delete_superseded_configmap(context: Context) -> None:
    for old_dir in getattr(context, "superseded_config_dirs", []):
        if old_dir.exists():
            shutil.rmtree(old_dir, ignore_errors=True)


@when(
    'the projected Secret volume is atomically swapped updating "{secret_rel_path}" '
    'to "{token_value}"'
)
def step_swap_secret_volume(context: Context, secret_rel_path: str, token_value: str) -> None:
    secrets_dir: Path = context.projected_secrets_dir
    new_ts = secrets_dir / f"..{time.time_ns()}_secrets"
    new_ts.mkdir()

    curr: Path = context.projected_current_secrets_ts
    # Copy existing
    for root, dirs, files in os.walk(curr):
        rel = Path(root).relative_to(curr)
        (new_ts / rel).mkdir(parents=True, exist_ok=True)
        for f in files:
            shutil.copy(Path(root) / f, new_ts / rel / f)

    # Overwrite updated file
    dest = new_ts / secret_rel_path
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(token_value, encoding="utf-8")

    _atomic_symlink_swap(secrets_dir, new_ts.name)
    context.projected_current_secrets_ts = new_ts


# --------------------------------------------------------------------- When (HTTP Probes)


@when(
    "an HTTP client makes GET /healthz against the server without an Authorization header"
)
def step_http_get_healthz_no_auth(context: Context) -> None:
    client: MCPHttpClient = context.mcp_http
    with httpx.Client() as h:
        res = h.get(f"http://{client.host}:{client.port}/healthz", timeout=5.0)
    context.last_http_response = res


@when(
    'an HTTP client makes GET /healthz against the server with bearer token "{token}"'
)
def step_http_get_healthz_bearer(context: Context, token: str) -> None:
    client: MCPHttpClient = context.mcp_http
    with httpx.Client() as h:
        res = h.get(
            f"http://{client.host}:{client.port}/healthz",
            headers={"Authorization": f"Bearer {token}"},
            timeout=5.0,
        )
    context.last_http_response = res


@when("an HTTP client makes GET /healthz against the server {count:d} times")
def step_http_get_healthz_n_times(context: Context, count: int) -> None:
    client: MCPHttpClient = context.mcp_http
    with httpx.Client() as h:
        for _ in range(count):
            res = h.get(f"http://{client.host}:{client.port}/healthz", timeout=5.0)
    context.last_http_response = res


@when("an HTTP client makes {method} {path} against the server")
def step_http_request(context: Context, method: str, path: str) -> None:
    client: MCPHttpClient = context.mcp_http
    with httpx.Client() as h:
        res = h.request(method, f"http://{client.host}:{client.port}{path}", timeout=5.0)
    context.last_http_response = res


# --------------------------------------------------------------------- Then (HTTP Probes)


@then("the HTTP response status code is {code:d}")
def step_http_status_code(context: Context, code: int) -> None:
    res: httpx.Response = context.last_http_response
    if res.status_code != code:
        raise AssertionError(f"Expected HTTP status {code}, got {res.status_code}: {res.text}")


@then("the HTTP response body is the JSON object:")
def step_http_response_body_json(context: Context) -> None:
    res: httpx.Response = context.last_http_response
    expected = json.loads(context.text or "{}")
    actual = res.json()
    if actual != expected:
        raise AssertionError(f"Expected JSON body {expected!r}, got {actual!r}")


@then("the HTTP response body has the exact keys {keys_raw}")
def step_http_body_exact_keys(context: Context, keys_raw: str) -> None:
    res: httpx.Response = context.last_http_response
    expected_keys = set(json.loads(keys_raw))
    actual_keys = set(res.json().keys())
    if actual_keys != expected_keys:
        raise AssertionError(f"Expected exact keys {expected_keys!r}, got {actual_keys!r}")


@then("the HTTP response text does NOT contain any of the literal strings:")
def step_http_text_no_literal_strings(context: Context) -> None:
    res: httpx.Response = context.last_http_response
    text = res.text
    if not text:
        raise AssertionError("HTTP response text is empty; positive control requires content to inspect")
    for row in context.table:
        forbidden = row["forbidden_string"]
        if forbidden in text:
            raise AssertionError(f"Forbidden string {forbidden!r} leaked in HTTP response: {text!r}")
