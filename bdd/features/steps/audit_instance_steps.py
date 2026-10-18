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
import os
import stat
import threading
from pathlib import Path
from typing import Any

from behave import given, then, when
from behave.runner import Context

from support.mcp_client import MCPClient, MCPClientError, MCPRPCError


# ------------------------------------------------------------------ clock


def _clock_file(context: Context) -> Path:
    return context.scratch_dir / "audit-clock"


@given('the audit clock reads "{timestamp}"')
@given('the audit clock advances to "{timestamp}"')
@when('the audit clock advances to "{timestamp}"')
def step_audit_clock_set(context: Context, timestamp: str) -> None:
    clock = _clock_file(context)
    clock.write_text(timestamp + "\n", encoding="utf-8")
    extra = getattr(context, "mcp_extra_env", None) or {}
    extra["IMAP_MCP_FAKE_NOW_FILE"] = str(clock)
    context.mcp_extra_env = extra


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


@given('server instance "{name}" is running')
@when('server instance "{name}" starts')
def step_server_instance_starts(context: Context, name: str) -> None:
    from features.steps.mcp_steps import _server_binary

    instances = _instances(context)
    if name in instances:
        raise AssertionError(f"Server instance {name!r} is already running")
    builder = getattr(context, "policy_builder", None)
    if builder is not None:
        builder.write()
    extra = dict(getattr(context, "mcp_extra_env", None) or {})
    extra.setdefault("IMAP_MCP_TEST_MODE", "1")
    client = MCPClient(
        server_binary=_server_binary(context),
        config_dir=context.config_dir,
        caller_id="invoice-agent",
        extra_env=extra,
    )
    client.start()
    instances[name] = client
    pids = getattr(context, "instance_pids", None) or {}
    pids[name] = client._proc.pid
    context.instance_pids = pids


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


@given('instance "{name}" has called {tool}')
def step_instance_has_called(context: Context, name: str, tool: str) -> None:
    ok, detail = _call(_instance(context, name), tool)
    if not ok:
        raise AssertionError(f"Instance {name!r} call {tool} failed: {detail}")


@when('instance "{name}" calls {tool}')
def step_instance_calls(context: Context, name: str, tool: str) -> None:
    outcomes = getattr(context, "instance_outcomes", None) or {}
    outcomes[name] = _call(_instance(context, name), tool)
    context.instance_outcomes = outcomes


@when('instances "{first}" and "{second}" each call {tool} {count:d} times at the same time')
def step_instances_call_concurrently(
    context: Context, first: str, second: str, tool: str, count: int
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


@then(
    'file "{filename}" contains a record with tool "{tool}" written by instance "{name}"'
)
def step_file_has_record_by_instance(
    context: Context, filename: str, tool: str, name: str
) -> None:
    caller_addr = f"stdio:pid={context.instance_pids[name]}"
    records = _records(context, filename)
    if not any(
        r.get("tool") == tool and r.get("caller_addr") == caller_addr for r in records
    ):
        raise AssertionError(
            f"No {tool!r} record with caller_addr {caller_addr!r} in {filename}; "
            f"records: {[(r.get('tool'), r.get('caller_addr')) for r in records]}"
        )


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


@then(
    'the records of file "{filename}" form one hash chain with consecutive '
    "seq numbers starting at 0"
)
def step_file_forms_one_chain(context: Context, filename: str) -> None:
    lines = _lines(context, filename)
    if not lines:
        raise AssertionError(f"{filename} is empty")
    previous: bytes | None = None
    for index, line in enumerate(lines):
        record = json.loads(line)
        if "seq" not in record or "prev_hash" not in record:
            raise AssertionError(
                f"{filename} line {index + 1} is not a chain record: {record!r}"
            )
        if record["seq"] != index:
            raise AssertionError(
                f"{filename} line {index + 1} has seq={record['seq']!r}, expected {index}; "
                f"seq sequence: {[json.loads(l).get('seq') for l in lines]}"
            )
        if previous is not None:
            expected = "sha256:" + hashlib.sha256(previous).hexdigest()
            if record["prev_hash"] != expected:
                raise AssertionError(
                    f"{filename} line {index + 1} (seq {record['seq']}) breaks the chain: "
                    f"prev_hash={record['prev_hash']!r}, expected {expected!r}"
                )
        previous = line


@given('the content of audit file "{filename}" is remembered')
def step_remember_audit_file(context: Context, filename: str) -> None:
    remembered = getattr(context, "remembered_audit_files", None) or {}
    remembered[filename] = (context.audit_dir / filename).read_bytes()
    context.remembered_audit_files = remembered


@then('the audit file "{filename}" is byte-for-byte unchanged')
def step_audit_file_unchanged(context: Context, filename: str) -> None:
    before = context.remembered_audit_files[filename]
    after = (context.audit_dir / filename).read_bytes()
    if after != before:
        added = after[len(before):] if after.startswith(before) else after
        raise AssertionError(
            f"{filename} changed ({len(before)} -> {len(after)} bytes); "
            f"appended/new content:\n{added.decode('utf-8', 'replace')}"
        )


@given("the audit trail has been made unwritable")
def step_audit_trail_unwritable(context: Context) -> None:
    """Strip write permission from the audit directory and every file
    in it. Permissions are restored on cleanup so the scratch dir can
    be removed."""
    directory = context.audit_dir
    saved: list[tuple[Path, int]] = []
    for path in [directory, *directory.iterdir()]:
        mode = stat.S_IMODE(path.stat().st_mode)
        saved.append((path, mode))
        os.chmod(path, mode & ~0o222)

    def _restore() -> None:
        for path, mode in saved:
            try:
                os.chmod(path, mode)
            except OSError:
                pass

    context.add_cleanup(_restore)
