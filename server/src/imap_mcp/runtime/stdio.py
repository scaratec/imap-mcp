"""stdio MCP transport.

Adheres to:
- ADR 0015, ADR 0049: Caller validation and single-caller instance boundary
- ADR 0048: Hash-chained audit logging
- ADR 0058: Closed test-mode control boundary
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Any

from mcp.server.stdio import stdio_server

from ..context import ServerContext, _build_context
from ..dispatch import build_server
from ..imap_core import close_connection_pools
from ..reload import _install_sighup_handler
from ..tracing import install_sigterm_flush


async def run_stdio(config_dir: Path, caller_id: str | None) -> None:
    context, configuration = _build_context(
        config_dir, default_caller_id=caller_id or "<no-caller>"
    )

    if len(configuration.callers_file.callers) != 1:
        raise SystemExit("callers.yaml must define exactly one caller")

    single_caller = configuration.callers_file.callers[0]
    auth_failure_reason: str | None = None
    if not caller_id:
        auth_failure_reason = "no_caller_identity"
    elif caller_id != single_caller.id:
        auth_failure_reason = "unknown_caller_id"

    if auth_failure_reason is not None:
        try:
            await _stdio_deny_initialize(context, caller_id, auth_failure_reason)
        finally:
            await context.oauth_manager.aclose()
        return

    _install_sighup_handler(context, config_dir)
    install_sigterm_flush()
    if context.saga is not None:
        asyncio.create_task(context.saga.run_pending_recovery(single_caller.id))
    app = build_server(context)
    try:
        async with stdio_server() as (read_stream, write_stream):
            await app.run(
                read_stream,
                write_stream,
                app.create_initialization_options(),
            )
    finally:
        await close_connection_pools()
        await context.oauth_manager.aclose()


async def _stdio_deny_initialize(
    context: ServerContext, caller_id: str | None, reason: str
) -> None:
    import json as _json
    import sys as _sys

    if context.audit is not None:
        record: dict[str, Any] = {
            "phase": "single",
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
        "error": {"code": -32001, "message": "auth_failed", "data": reason},
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
        return Path(".")
    return Path(os.path.expanduser(raw))
