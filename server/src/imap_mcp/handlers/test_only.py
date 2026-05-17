"""Test-only tool handlers, gated by IMAP_MCP_TEST_MODE (ADR 0023).

These are not listed in tool discovery and never reach a production
caller. They exist so the BDD harness can drive deterministic
recovery and audit-rotation scenarios. Phase D will replace the
direct env-var reads with injected `TestHooks`.
"""

from __future__ import annotations

import os
from typing import Any, TYPE_CHECKING

from mcp.shared.exceptions import McpError
from mcp.types import ErrorData

if TYPE_CHECKING:
    from ..context import ServerContext


async def handle_test_run_recovery(
    context: "ServerContext", arguments: dict[str, Any]
) -> dict[str, Any]:
    """Test-only: run N recovery passes. Not listed in tool discovery.

    Guarded by `IMAP_MCP_TEST_MODE`. The BDD harness uses this to
    exercise retry-limit scenarios deterministically.
    """
    if os.environ.get("IMAP_MCP_TEST_MODE") != "1":
        raise McpError(ErrorData(code=-32601, message="Unknown tool: '_test_run_recovery'"))
    if context.saga is None:
        return {"processed": 0, "reason": "saga_not_configured"}
    passes = int(arguments.get("passes", 1))
    total = 0
    for _ in range(passes):
        total += await context.saga.run_pending_recovery()
    return {"processed": total, "passes": passes}


async def handle_test_run_audit_rotation(
    context: "ServerContext", arguments: dict[str, Any]
) -> dict[str, Any]:
    """Test-only: trigger AuditWriter.rotate() once. Reads
    `IMAP_MCP_FAKE_NOW_UTC` from the env to advance the clock.

    Guarded by `IMAP_MCP_TEST_MODE`. ADR 0023 documents the
    test-only control surface.
    """
    if os.environ.get("IMAP_MCP_TEST_MODE") != "1":
        raise McpError(ErrorData(code=-32601, message="Unknown tool: '_test_run_audit_rotation'"))
    _ = arguments
    if context.audit is None:
        return {"reason": "audit_not_configured"}
    summary = context.audit.rotate()
    return summary
