"""Steps for scenarios with several server instances sharing one audit
directory (audit_multi_instance.feature).

Each named instance is its own stdio server subprocess started from the
scenario's config directory. All instances read the same audit clock
file (`IMAP_MCP_FAKE_NOW_FILE`), so a scenario can move the clock of
already running servers. Records written by an instance are recognised
by the `caller_addr` field, which carries the server's pid.
"""

from __future__ import annotations

import hashlib
import json
import threading
from typing import Any

from behave import given, then, when
from behave.runner import Context

from support.mcp_client import MCPClient, MCPClientError, MCPRPCError

# -------------------------------------------------------------- instances


def _instances(context: Context) -> dict[str, MCPClient]:
    instances = getattr(context, "server_instances", None)
    if instances is None:
        instances = {}
        context.server_instances = instances
    return instances


def _instance(context: Context, name: str) -> MCPClient:
    client = _instances(context).get(name)
    if client is None:
        raise AssertionError(f"Server instance {name!r} is not running")
    return client


def _start_instance(context: Context, name: str) -> MCPClient:
    from features.steps.mcp_steps import _server_binary

    instances = _instances(context)
    if name in instances:
        return instances[name]
    builder = getattr(context, "policy_builder", None)
    if builder is not None:
        builder.write()
    extra = dict(getattr(context, "mcp_extra_env", None) or {})
    needs_test_mode = (
        getattr(context, "is_test_hooks", False)
        or "IMAP_MCP_FAKE_NOW_UTC" in extra
        or "IMAP_MCP_FAKE_NOW_FILE" in extra
        or extra.get("IMAP_MCP_TEST_MODE") == "1"
    )
    if needs_test_mode and not getattr(context, "without_test_mode", False):
        extra["IMAP_MCP_TEST_MODE"] = "1"
    else:
        extra.pop("IMAP_MCP_TEST_MODE", None)

    caller = next(iter(context.policy_builder.callers), None) if getattr(context, "policy_builder", None) else None
    caller_id = getattr(context, "mcp_caller_id", None) or (caller.id if caller else None)
    if not caller_id:
        raise AssertionError("No caller configured for audit multi instance")
    client = MCPClient(
        server_binary=_server_binary(context),
        config_dir=context.config_dir,
        caller_id=caller_id,
        extra_env=extra,
    )
    client.start()
    instances[name] = client
    pids = getattr(context, "instance_pids", None) or {}
    pids[name] = client._proc.pid
    context.instance_pids = pids
    return client


@given('server instance "{name}" is running')
@when('server instance "{name}" starts')
def step_server_instance_starts(context: Context, name: str) -> None:
    _start_instance(context, name)


@when('server instance "{name}" starts and calls list_accounts')
def step_server_instance_starts_and_calls_list_accounts(context: Context, name: str) -> None:
    client = _start_instance(context, name)
    outcomes = getattr(context, "instance_outcomes", None) or {}
    outcomes[name] = _call(client, "list_accounts")
    context.instance_outcomes = outcomes


@when('server instance "{name}" starts and calls list_folders with account "{account_id}"')
def step_server_instance_starts_and_calls_list_folders(
    context: Context, name: str, account_id: str
) -> None:
    client = _start_instance(context, name)
    outcomes = getattr(context, "instance_outcomes", None) or {}
    try:
        payload = client.call_tool("list_folders", {"account": account_id})
        is_err = payload.get("isError")
        content = payload.get("content") or [{}]
        outcomes[name] = (not is_err, str(content[0].get("text", payload)))
    except MCPRPCError as exc:
        outcomes[name] = (False, exc.message)
    context.instance_outcomes = outcomes


@when('server instance "{name}" stops cleanly')
@when('server instance "{name}" stops')
def step_server_instance_stops(context: Context, name: str) -> None:
    _instance(context, name).close()
    del _instances(context)[name]


def _call(client: MCPClient, tool: str) -> tuple[bool, str]:
    """Return (succeeded, detail). A JSON-RPC error or an isError
    payload is a failed call; detail carries the error text."""
    try:
        payload = client.call_tool(tool, {})
    except MCPRPCError as exc:
        return False, exc.message
    if payload.get("isError"):
        content = payload.get("content") or [{}]
        return False, str(content[0].get("text", payload))
    return True, json.dumps(payload)


from behave import use_step_matcher as _use_step_matcher

_use_step_matcher("re")

@given(r'instance "(?P<name>[^"]+)" calls (?P<tool>[A-Za-z0-9_]+)')
@when(r'instance "(?P<name>[^"]+)" calls (?P<tool>[A-Za-z0-9_]+)')
def step_instance_calls(context: Context, name: str, tool: str) -> None:
    outcomes = getattr(context, "instance_outcomes", None) or {}
    outcomes[name] = _call(_instance(context, name), tool)
    context.instance_outcomes = outcomes

_use_step_matcher("parse")


@when('instance "{name}" calls create_draft with account "{account}", folder "{folder}", fields:')
def step_instance_calls_create_draft(
    context: Context, name: str, account: str, folder: str
) -> None:
    client = _instance(context, name)
    fields = json.loads(context.text or "{}")
    args = {"account": account, "folder": folder, **fields}
    outcomes = getattr(context, "instance_outcomes", None) or {}
    try:
        payload = client.call_tool("create_draft", args)
        is_err = payload.get("isError")
        content = payload.get("content") or [{}]
        outcomes[name] = (not is_err, str(content[0].get("text", payload)))
    except MCPRPCError as exc:
        outcomes[name] = (False, exc.message)
    context.instance_outcomes = outcomes


@when('instances "{first}" and "{second}" each call list_accounts {count:d} times concurrently')
@when('instances "{first}" and "{second}" each call {tool} {count:d} times at the same time')
def step_instances_call_concurrently(
    context: Context, first: str, second: str, count: int, tool: str = "list_accounts"
) -> None:
    clients = {first: _instance(context, first), second: _instance(context, second)}
    failures: list[str] = []
    barrier = threading.Barrier(len(clients))

    def _run(name: str, client: MCPClient) -> None:
        barrier.wait()
        for _ in range(count):
            try:
                ok, detail = _call(client, tool)
            except MCPClientError as exc:
                ok, detail = False, str(exc)
            if not ok:
                failures.append(f"{name}: {detail}")

    threads = [threading.Thread(target=_run, args=item) for item in clients.items()]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=60)
        if thread.is_alive():
            raise AssertionError("Worker thread timed out after 60s")
    if failures:
        raise AssertionError(
            f"{len(failures)} concurrent call(s) failed, first: {failures[0]}"
        )


@then('the call by instance "{name}" succeeds')
def step_instance_call_succeeds(context: Context, name: str) -> None:
    ok, detail = context.instance_outcomes[name]
    if not ok:
        stderr = _instance(context, name).stderr_text[-2000:]
        raise AssertionError(
            f"Call by instance {name!r} failed: {detail}\nServer stderr (tail):\n{stderr}"
        )


@then('the call by instance "{name}" fails with an error containing "{needle}"')
def step_instance_call_fails_with(context: Context, name: str, needle: str) -> None:
    ok, detail = context.instance_outcomes[name]
    if ok:
        raise AssertionError(f"Call by instance {name!r} succeeded: {detail}")
    if needle not in detail:
        raise AssertionError(
            f"Error of instance {name!r} does not contain {needle!r}: {detail!r}"
        )


# ------------------------------------------------------------- audit trail


def _lines(context: Context, filename: str) -> list[bytes]:
    path = context.audit_dir / filename
    if not path.exists():
        present = sorted(p.name for p in context.audit_dir.iterdir())
        raise AssertionError(f"File {filename!r} does not exist; present: {present}")
    return [line for line in path.read_bytes().splitlines(keepends=True) if line.strip()]


def _records(context: Context, filename: str) -> list[dict[str, Any]]:
    return [json.loads(line) for line in _lines(context, filename)]


@then('file "{filename}" contains exactly {count:d} record with tool "{tool}"')
@then('file "{filename}" contains exactly {count:d} records with tool "{tool}"')
def step_file_has_exact_count(
    context: Context, filename: str, count: int, tool: str
) -> None:
    records = _records(context, filename)
    actual = sum(1 for r in records if r.get("tool") == tool)
    if actual != count:
        raise AssertionError(
            f"{filename} holds {actual} record(s) with tool {tool!r}, expected {count}; "
            f"tools in file: {[r.get('tool') for r in records]}"
        )


@given('the exact bytes of audit file "{filename}" are remembered')
def step_remember_exact_bytes(context: Context, filename: str) -> None:
    remembered = getattr(context, "remembered_audit_files", None) or {}
    remembered[filename] = (context.audit_dir / filename).read_bytes()
    context.remembered_audit_files = remembered


@then('the audit file "{filename}" equals the remembered bytes')
def step_audit_file_equals_remembered(context: Context, filename: str) -> None:
    before = context.remembered_audit_files[filename]
    after = (context.audit_dir / filename).read_bytes()
    if after != before:
        added = after.removeprefix(before)
        raise AssertionError(
            f"{filename} changed ({len(before)} -> {len(after)} bytes); "
            f"appended/new content:\n{added.decode('utf-8', 'replace')}"
        )


@then('file "{filename}" contains a record with tool "{tool}" and step "{step}"')
def step_file_contains_record_tool_step(context: Context, filename: str, tool: str, step: str) -> None:
    records = _records(context, filename)
    if not any(r.get("tool") == tool and r.get("step") == step for r in records):
        raise AssertionError(
            f"No record with tool {tool!r} and step {step!r} in {filename}; present: "
            f"{[(r.get('tool'), r.get('step')) for r in records]}"
        )


@then('file "{filename}" contains a record with tool "{tool}"')
def step_file_contains_record_with_tool(context: Context, filename: str, tool: str) -> None:
    records = _records(context, filename)
    if not any(r.get("tool") == tool for r in records):
        raise AssertionError(f"No record with tool {tool!r} in {filename}; present: {[r.get('tool') for r in records]}")


@then('file "{filename}" contains {count:d} record with tool "{tool}"')
@then('file "{filename}" contains {count:d} records with tool "{tool}"')
def step_file_contains_count_records_tool(context: Context, filename: str, count: int, tool: str) -> None:
    records = _records(context, filename)
    actual = sum(1 for r in records if r.get("tool") == tool)
    if actual != count:
        raise AssertionError(
            f"{filename} holds {actual} record(s) with tool {tool!r}, expected {count}; "
            f"tools in file: {[r.get('tool') for r in records]}"
        )


@then('all records of file "{filename}" form a valid hash chain with consecutive seq numbers starting at 0')
def step_all_records_form_valid_hash_chain(context: Context, filename: str) -> None:
    lines = _lines(context, filename)
    records = _records(context, filename)
    if not records:
        raise AssertionError(f"Audit file {filename} contains 0 records, expected at least 1")

    genesis_zero = "sha256:" + "0" * 64
    valid_genesis_hashes = {genesis_zero}

    all_files = sorted(context.audit_dir.glob("*.jsonl"))
    current_path = context.audit_dir / filename
    if current_path in all_files:
        idx = all_files.index(current_path)
        if idx > 0:
            prev_file = all_files[idx - 1]
            prev_lines = [l for l in prev_file.read_bytes().splitlines(keepends=True) if l.strip()]
            if prev_lines:
                prev_final_hash = "sha256:" + hashlib.sha256(prev_lines[-1]).hexdigest()
                valid_genesis_hashes.add(prev_final_hash)

    for i, (line, rec) in enumerate(zip(lines, records)):
        actual_seq = rec.get("seq")
        if actual_seq != i:
            raise AssertionError(
                f"Record at index {i} in {filename} has seq={actual_seq}, expected {i}"
            )

        actual_prev = rec.get("prev_hash")
        if i == 0:
            if actual_prev not in valid_genesis_hashes:
                raise AssertionError(
                    f"Genesis record at index 0 in {filename} has prev_hash={actual_prev!r}, "
                    f"expected one of {sorted(valid_genesis_hashes)}"
                )
        else:
            expected_prev = "sha256:" + hashlib.sha256(lines[i - 1]).hexdigest()
            if actual_prev != expected_prev:
                raise AssertionError(
                    f"Record at index {i} in {filename} has prev_hash={actual_prev!r}, "
                    f"expected {expected_prev!r} (hash of previous line)"
                )
