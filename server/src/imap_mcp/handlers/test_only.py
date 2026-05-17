"""Test-only tool handlers, gated by TestHooks.test_mode (ADR 0023).

These are not listed in tool discovery and never reach a production
caller. They exist so the BDD harness can drive deterministic
recovery and audit-rotation scenarios.
"""

from __future__ import annotations

from typing import Any, NotRequired, TypedDict, TYPE_CHECKING

from mcp.shared.exceptions import McpError
from mcp.types import ErrorData

if TYPE_CHECKING:
    from ..context import ServerContext


class TestRecoveryResponse(TypedDict, total=False):
    processed: int
    passes: NotRequired[int]
    reason: NotRequired[str]


class TestAuditRotationResponse(TypedDict, total=False):
    reason: NotRequired[str]
    # AuditWriter.rotate() may return arbitrary summary fields; we widen
    # via total=False rather than enumerate them since this surface is
    # test-only and changes with the audit module.


async def handle_test_run_recovery(
    context: "ServerContext", arguments: dict[str, Any]
) -> TestRecoveryResponse:
    """Test-only: run N recovery passes. Not listed in tool discovery.

    Guarded by ``TestHooks.test_mode``. The BDD harness uses this to
    exercise retry-limit scenarios deterministically.
    """
    if not context.test_hooks.test_mode:
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
    """Test-only: trigger AuditWriter.rotate() once. ``audit._now_utc``
    reads ``TestHooks.fake_now_utc`` to advance the clock.

    Guarded by ``TestHooks.test_mode``. ADR 0023 documents the
    test-only control surface.
    """
    if not context.test_hooks.test_mode:
        raise McpError(ErrorData(code=-32601, message="Unknown tool: '_test_run_audit_rotation'"))
    _ = arguments
    if context.audit is None:
        return {"reason": "audit_not_configured"}
    summary = context.audit.rotate()
    return summary
