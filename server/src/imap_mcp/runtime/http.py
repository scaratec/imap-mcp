"""Streamable HTTP MCP transport with bearer-token caller auth (ADR 0015)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..context import _CURRENT_CALLER_ID, _build_context
from ..dispatch import build_server
from ..reload import _install_sighup_handler


async def run_http(config_dir: Path, host: str, port: int) -> None:
    """Serve MCP over Streamable HTTP with bearer-token caller auth.

    Bearer token + caller_id arrive as HTTP headers
    (`Authorization: Bearer <token>` and `X-MCP-Caller-Id: <id>`). The
    bearer-auth middleware resolves the caller against `callers.yaml`,
    validates the token via the configured `secret_store`, and either
    sets the per-request caller in `_CURRENT_CALLER_ID` or rejects the
    request with HTTP 401 + an `auth_failed` audit record. Non-MCP
    routes return HTTP 404 (ADR 0018, non_goal_rejection.feature).
    """
    import hmac
    import uvicorn
    from starlette.applications import Starlette
    from starlette.middleware.base import BaseHTTPMiddleware
    from starlette.responses import JSONResponse, Response
    from starlette.routing import Mount

    from mcp.server.streamable_http_manager import StreamableHTTPSessionManager

    # `default_caller_id` is unused on HTTP — every request supplies its
    # own. We still need a non-empty placeholder because the dataclass
    # frozen-default guard expects a string.
    context, configuration = _build_context(config_dir, default_caller_id="<http-no-default>")
    _install_sighup_handler(context, config_dir)

    # ADR 0015 invariant: stdio_trusted callers cannot authenticate
    # over HTTP. The orchestrator-trust assumption that justifies
    # stdio_trusted does not survive a network boundary — there is no
    # orchestrator on the other end of an HTTP socket. Any
    # stdio_trusted caller in the configured set therefore makes the
    # config invalid for HTTP, fatal at startup.
    stdio_trusted = [
        c.id for c in configuration.callers_file.callers if c.auth.type == "stdio_trusted"
    ]
    if stdio_trusted:
        names = ", ".join(f'"{c}"' for c in stdio_trusted)
        raise SystemExit(f'caller {names} as "stdio_trusted not permitted on non-stdio transport"')

    app_mcp = build_server(context)
    session_manager = StreamableHTTPSessionManager(app=app_mcp, json_response=True, stateless=True)

    def _audit_auth_failed(caller_id_claim: str | None, reason: str, addr: str) -> None:
        if context.audit is None:
            return
        # `secret_decryption_failed` is a configuration-class failure
        # (the secret store could not decrypt the configured value),
        # not a wrong-credential failure. The audit `reason` field
        # surfaces it as a distinct category so operators can tell
        # them apart at a glance.
        top_reason = (
            "secret_decryption_failed" if reason == "secret_decryption_failed" else "auth_failed"
        )
        record: dict[str, Any] = {
            "caller_id": caller_id_claim,
            "caller_addr": addr,
            "tool": "auth_failed",
            "decision": "DENY",
            "reason": top_reason,
            "auth_failure_reason": reason,
        }
        context.audit.write(record)

    from ..secrets import SecretDecryptionFailed

    def _resolve_token(secret_ref: str | None) -> tuple[str | None, str | None]:
        """Return `(value, error_reason)`. `error_reason` is non-None
        only on a recoverable lookup failure that the audit layer
        should distinguish from a plain "missing secret"."""
        if secret_ref is None:
            return None, None
        try:
            return context.secret_store.get(secret_ref), None  # type: ignore[attr-defined]
        except SecretDecryptionFailed:
            return None, "secret_decryption_failed"
        except Exception:
            return None, None

    class BearerAuthMiddleware(BaseHTTPMiddleware):
        async def dispatch(self, request, call_next):  # type: ignore[no-untyped-def]
            # Only the MCP route requires authentication. Anything else
            # is a non-route and returns 404 unconditionally below.
            if not request.url.path.rstrip("/").endswith("/mcp"):
                return await call_next(request)
            addr = request.client.host if request.client else "http:?"
            caller_id_claim = request.headers.get("x-mcp-caller-id")
            authz = request.headers.get("authorization", "")
            scheme, _, token = authz.partition(" ")
            if scheme.lower() != "bearer" or not token:
                _audit_auth_failed(caller_id_claim, "no_bearer_token", f"http:{addr}")
                return JSONResponse({"error": "auth_failed"}, status_code=401)
            if not caller_id_claim:
                _audit_auth_failed(None, "no_caller_id", f"http:{addr}")
                return JSONResponse({"error": "no_caller_identity"}, status_code=401)
            caller = configuration.caller_by_id(caller_id_claim)
            if caller is None:
                # ADR-0015 identity-immutability: if the bearer
                # matches a configured caller's token, the client is
                # masquerading as someone else. Surface that as
                # `identity_immutable` rather than the generic
                # `unknown_caller_id`. Constant-time scan over all
                # callers.
                masquerade = False
                for other in configuration.callers_file.callers:
                    if other.auth.type != "shared_token":
                        continue
                    other_expected, _ = _resolve_token(other.auth.token_secret_ref)
                    if other_expected is not None and hmac.compare_digest(token, other_expected):
                        masquerade = True
                if masquerade:
                    _audit_auth_failed(caller_id_claim, "identity_immutable", f"http:{addr}")
                    return JSONResponse({"error": "identity_immutable"}, status_code=401)
                _audit_auth_failed(caller_id_claim, "unknown_caller_id", f"http:{addr}")
                return JSONResponse({"error": "unknown_caller_id"}, status_code=401)
            if caller.auth.type != "shared_token":
                _audit_auth_failed(caller_id_claim, "wrong_auth_type", f"http:{addr}")
                return JSONResponse({"error": "auth_failed"}, status_code=401)
            expected, lookup_error = _resolve_token(caller.auth.token_secret_ref)
            if lookup_error is not None:
                _audit_auth_failed(caller_id_claim, lookup_error, f"http:{addr}")
                return JSONResponse({"error": "auth_failed"}, status_code=401)
            if expected is None or not hmac.compare_digest(token, expected):
                # Identity-immutability check (ADR-0015): if the token
                # actually matches a DIFFERENT configured caller, the
                # client is trying to "switch" identity while keeping
                # an existing bearer. That is a distinct error class
                # — call it `identity_immutable`. Run the loop fully
                # for constant-time comparison.
                token_matches_other = False
                for other in configuration.callers_file.callers:
                    if other.id == caller_id_claim or other.auth.type != "shared_token":
                        continue
                    other_expected, _ = _resolve_token(other.auth.token_secret_ref)
                    if other_expected is not None and hmac.compare_digest(token, other_expected):
                        token_matches_other = True
                if token_matches_other:
                    _audit_auth_failed(caller_id_claim, "identity_immutable", f"http:{addr}")
                    return JSONResponse({"error": "identity_immutable"}, status_code=401)
                _audit_auth_failed(caller_id_claim, "wrong_token", f"http:{addr}")
                return JSONResponse({"error": "auth_failed"}, status_code=401)
            token_var = _CURRENT_CALLER_ID.set(caller_id_claim)
            try:
                return await call_next(request)
            finally:
                _CURRENT_CALLER_ID.reset(token_var)

    async def _handle_mcp(scope, receive, send):  # type: ignore[no-untyped-def]
        await session_manager.handle_request(scope, receive, send)

    async def _not_found(request):  # type: ignore[no-untyped-def]
        return Response(status_code=404)

    from starlette.routing import Route

    starlette_app = Starlette(
        routes=[
            Mount("/mcp", app=_handle_mcp),
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
        await context.oauth_manager.aclose()
