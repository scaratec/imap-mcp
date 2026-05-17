"""stdio MCP transport.

`run_stdio` wires the JSON-RPC over stdio plumbing from the MCP SDK
to the dispatcher. The two `_*_from_env_or_exit` helpers resolve
startup config from the environment and are also re-used by the HTTP
entry point via `__main__.py`.
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Any

from mcp.server.stdio import stdio_server

from ..context import ServerContext, _build_context
from ..dispatch import build_server
from ..reload import _install_sighup_handler


async def run_stdio(config_dir: Path, caller_id: str | None) -> None:
    # ADR-0015: caller validation runs on EVERY stdio start. The two
    # invalid cases — None or a value not present in callers.yaml —
    # must surface as a structured JSON-RPC error during the
    # Initialize handshake, NOT as a SystemExit before the MCP loop
    # starts. Otherwise the orchestrator sees a broken pipe.
    context, configuration = _build_context(
        config_dir, default_caller_id=caller_id or "<no-caller>"
    )

    auth_failure_reason: str | None = None
    if not caller_id:
        auth_failure_reason = "no_caller_identity"
    elif configuration.caller_by_id(caller_id) is None:
        auth_failure_reason = "unknown_caller_id"

    if auth_failure_reason is not None:
        try:
            await _stdio_deny_initialize(context, caller_id, auth_failure_reason)
        finally:
            await context.oauth_manager.aclose()
        return

    _install_sighup_handler(context, config_dir)
    app = build_server(context)
    try:
        async with stdio_server() as (read_stream, write_stream):
            await app.run(
                read_stream,
                write_stream,
                app.create_initialization_options(),
            )
    finally:
        await context.oauth_manager.aclose()


async def _stdio_deny_initialize(
    context: ServerContext, caller_id: str | None, reason: str
) -> None:
    """Read one JSON-RPC line from stdin, write a JSON-RPC error
    response keyed to the request id, and exit cleanly. The audit
    record uses `tool=auth_failed` with `auth_failure_reason=reason`.

    The MCP client's `initialize()` reads the response, raises an
    `MCPRPCError` with the message matching `reason`, then sees EOF
    when the server exits."""
    import json as _json
    import sys as _sys

    # Audit the failure first so the BDD harness's assertions can read
    # the JSONL record after the server exits.
    if context.audit is not None:
        record: dict[str, Any] = {
            "caller_id": caller_id,
            "caller_addr": f"stdio:pid={os.getpid()}",
            "tool": "auth_failed",
            "decision": "DENY",
            "reason": "auth_failed",
            "auth_failure_reason": reason,
        }
        context.audit.write(record)

    loop = asyncio.get_running_loop()
    line = await loop.run_in_executor(None, _sys.stdin.readline)
    request_id: Any = None
    if line.strip():
        try:
            request = _json.loads(line)
            request_id = request.get("id")
        except _json.JSONDecodeError:
            request_id = None

    error_payload = {
        "jsonrpc": "2.0",
        "id": request_id,
        "error": {"code": -32001, "message": reason},
    }
    _sys.stdout.write(_json.dumps(error_payload, separators=(",", ":")) + "\n")
    _sys.stdout.flush()


def _caller_id_from_env_or_exit() -> str:
    caller_id = os.environ.get("IMAP_MCP_CALLER_ID")
    if not caller_id:
        raise SystemExit(
            "IMAP_MCP_CALLER_ID is not set. The stdio_trusted auth type "
            "requires the orchestrator to supply the caller identity via "
            "argv or environment (ADR 0015)."
        )
    return caller_id


def _config_dir_from_env_or_exit() -> Path:
    raw = os.environ.get("IMAP_MCP_CONFIG_DIR")
    if not raw:
        raise SystemExit(
            "IMAP_MCP_CONFIG_DIR is not set. The server requires a path to "
            "the config tree (accounts.yaml, callers.yaml, policies/*.yaml)."
        )
    path = Path(raw)
    if not path.is_dir():
        raise SystemExit(f"IMAP_MCP_CONFIG_DIR does not point at a directory: {path}")
    return path
