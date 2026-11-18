"""Streamable HTTP MCP transport with bearer-token caller auth.

Adheres to:
- ADR 0048: Audit records with http:<ip>:<port> caller_addr
- ADR 0049: Single caller instance boundary and immutable session identity
- ADR 0050: Secret store backends
- ADR 0051: Loopback sidecar deployment and /healthz endpoint
"""

from __future__ import annotations

import asyncio
import hmac
from pathlib import Path
from typing import Any

import uvicorn
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from starlette.applications import Starlette
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Mount, Route

from ..context import _CURRENT_CALLER_ADDR, _CURRENT_CALLER_ID, _build_context
from ..dispatch import build_server
from ..imap_core import close_connection_pools
from ..reload import _install_sighup_handler
from ..secrets import SecretDecryptionFailed


async def run_http(config_dir: Path, host: str, port: int) -> None:
    context, configuration = _build_context(config_dir, default_caller_id="<http-no-default>")
    _install_sighup_handler(context, config_dir)
    # ADR 0049: callers.yaml must define exactly one caller
    if len(configuration.callers_file.callers) != 1:
        raise SystemExit("callers.yaml must define exactly one caller")

    single_caller = configuration.callers_file.callers[0]
    if single_caller.auth is not None and single_caller.auth.type == "stdio_trusted":
        raise SystemExit("stdio_trusted not permitted on non-stdio transport")

    if context.saga is not None:
        asyncio.create_task(context.saga.run_pending_recovery(single_caller.id))

    app_mcp = build_server(context)
    session_manager = StreamableHTTPSessionManager(app=app_mcp, json_response=True, stateless=True)

    def _client_addr(request: Request) -> str:
        if request.client:
            return f"http:{request.client.host}:{request.client.port}"
        return "http:127.0.0.1:0"

    def _audit_auth_failed(caller_id_claim: str | None, reason: str, addr: str) -> None:
        if context.audit is None:
            return
        top_reason = "identity_immutable" if reason == "identity_immutable" else "auth_failed"
        record: dict[str, Any] = {
            "phase": "single",
            "caller_id": caller_id_claim,
            "caller_addr": addr,
            "tool": "auth_failed",
            "decision": "DENY",
            "reason": top_reason,
            "auth_failure_reason": reason,
        }
        context.audit.write(record)

    def _resolve_token(secret_ref: str | None) -> tuple[str | None, str | None]:
        if secret_ref is None:
            return None, None
        try:
            return context.secret_store.get(secret_ref), None
        except SecretDecryptionFailed:
            return None, "secret_decryption_failed"
        except Exception:  # noqa: BLE001
            return None, None

    class BearerAuthMiddleware(BaseHTTPMiddleware):
        async def dispatch(self, request: Request, call_next: Any) -> Response:
            # Allow /healthz through without MCP bearer auth
            if request.url.path == "/healthz":
                return await call_next(request)  # type: ignore[no-any-return]

            if not request.url.path.rstrip("/").endswith("/mcp"):
                return await call_next(request)  # type: ignore[no-any-return]

            addr = _client_addr(request)
            caller_id_claim = request.headers.get("x-mcp-caller-id")
            authz = request.headers.get("authorization", "")
            scheme, _, token = authz.partition(" ")
            if scheme.lower() != "bearer" or not token:
                _audit_auth_failed(caller_id_claim, "no_bearer_token", addr)
                return JSONResponse({"error": "auth_failed"}, status_code=401)
            if not caller_id_claim:
                _audit_auth_failed(None, "no_caller_id", addr)
                return JSONResponse({"error": "no_caller_identity"}, status_code=401)

            caller = configuration.caller_by_id(caller_id_claim)
            if caller is None:
                # Check identity immutability (masquerading with another caller's token)
                expected_token, _ = _resolve_token(
                    single_caller.auth.token_secret_ref if single_caller.auth else None
                )
                if expected_token and hmac.compare_digest(token, expected_token):
                    _audit_auth_failed(caller_id_claim, "identity_immutable", addr)
                    return JSONResponse({"error": "identity_immutable"}, status_code=401)
                _audit_auth_failed(caller_id_claim, "unknown_caller_id", addr)
                return JSONResponse({"error": "unknown_caller_id"}, status_code=401)

            if caller.auth is None or caller.auth.type != "shared_token":
                _audit_auth_failed(caller_id_claim, "wrong_auth_type", addr)
                return JSONResponse({"error": "auth_failed"}, status_code=401)

            expected, lookup_error = _resolve_token(caller.auth.token_secret_ref)
            if lookup_error is not None:
                _audit_auth_failed(caller_id_claim, "auth_failed", addr)
                return JSONResponse({"error": "auth_failed"}, status_code=401)

            if expected is None or not hmac.compare_digest(token, expected):
                _audit_auth_failed(caller_id_claim, "wrong_token", addr)
                return JSONResponse({"error": "auth_failed"}, status_code=401)

            token_var = _CURRENT_CALLER_ID.set(caller_id_claim)
            addr_var = _CURRENT_CALLER_ADDR.set(addr)
            try:
                return await call_next(request)  # type: ignore[no-any-return]
            finally:
                _CURRENT_CALLER_ID.reset(token_var)
                _CURRENT_CALLER_ADDR.reset(addr_var)

    async def _handle_mcp(scope: Any, receive: Any, send: Any) -> None:
        await session_manager.handle_request(scope, receive, send)

    async def _healthz(request: Request) -> Response:
        """GET /healthz endpoint (ADR 0051). Returns 200 ok or 503 unavailable."""
        if context.audit is not None and context.audit.is_writable():
            return JSONResponse({"status": "ok"}, status_code=200)
        return JSONResponse({"status": "unavailable"}, status_code=503)

    async def _not_found(request: Request) -> Response:
        return Response(status_code=404)

    starlette_app = Starlette(
        routes=[
            Mount("/mcp", app=_handle_mcp),
            Route("/healthz", endpoint=_healthz, methods=["GET"]),
            Route(
                "/{path:path}",
                endpoint=_not_found,
                methods=["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"],
            ),
        ],
        middleware=[],
    )
    starlette_app.add_middleware(BearerAuthMiddleware)

    try:
        async with session_manager.run():
            config = uvicorn.Config(starlette_app, host=host, port=port, log_level="warning")
            await uvicorn.Server(config).serve()
    finally:
        await close_connection_pools()
        await context.oauth_manager.aclose()
