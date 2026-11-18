"""Deterministic OAuth2 HTTP mock and control proxy for BDD scenarios.

RFC 6749 and RFC 7636 compliant OAuth2 control layer. Can run as a transparent
controller proxying upstream navikt/mock-oauth2-server while strictly enforcing
PKCE S256 verification, dynamic token lifetimes, token error priming, token
exchange tracking, and token revocation / bearer validation.
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Self
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

import httpx

logger = logging.getLogger(__name__)


def _s256_challenge(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")


class OAuthMockState:
    """Thread-safe state for OAuth mock controlling."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.reset()

    def reset(self) -> None:
        with self._lock:
            self.primed_errors: list[dict[str, Any]] = []
            self.custom_lifetime: int | None = None
            self.codes: dict[str, dict[str, str]] = {}
            self.issued_tokens: dict[str, dict[str, Any]] = {}
            self.revoked_tokens: set[str] = set()
            self.refresh_tokens: dict[str, dict[str, Any]] = {}
            self.revoked_refresh_tokens: set[str] = set()
            self.token_requests: list[dict[str, Any]] = []

    def set_token_lifetime(self, expires_in_seconds: int) -> None:
        with self._lock:
            self.custom_lifetime = expires_in_seconds

    def prime_token_error(
        self,
        error: str,
        description: str | None = None,
        grant_type: str | None = None,
        once: bool = True,
    ) -> None:
        with self._lock:
            self.primed_errors.append(
                {
                    "error": error,
                    "description": description,
                    "grant_type": grant_type,
                    "once": once,
                }
            )

    def check_primed_error(self, grant_type: str) -> dict[str, Any] | None:
        with self._lock:
            for idx, err in enumerate(self.primed_errors):
                if err["grant_type"] is None or err["grant_type"] == grant_type:
                    if err["once"]:
                        return self.primed_errors.pop(idx)
                    return err
            return None

    def store_code(
        self,
        code: str,
        challenge: str | None,
        method: str | None,
        client_id: str | None,
        redirect_uri: str | None,
        scope: str | None,
    ) -> None:
        with self._lock:
            self.codes[code] = {
                "challenge": challenge or "",
                "method": method or "plain",
                "client_id": client_id or "",
                "redirect_uri": redirect_uri or "",
                "scope": scope or "",
            }

    def get_code(self, code: str) -> dict[str, str] | None:
        with self._lock:
            return self.codes.get(code)

    def consume_code(self, code: str) -> dict[str, str] | None:
        with self._lock:
            return self.codes.pop(code, None)

    def register_refresh_token(
        self, refresh_token: str, scope: str = "https://mail.google.com/"
    ) -> None:
        with self._lock:
            self.refresh_tokens[refresh_token] = {
                "scope": scope,
                "created_at": time.time(),
            }
            self.revoked_refresh_tokens.discard(refresh_token)

    def revoke_refresh_token(self, refresh_token: str) -> None:
        with self._lock:
            self.revoked_refresh_tokens.add(refresh_token)

    def validate_refresh_token(self, refresh_token: str) -> bool:
        with self._lock:
            return (
                refresh_token in self.refresh_tokens
                and refresh_token not in self.revoked_refresh_tokens
            )

    def record_token_request(self, form_data: dict[str, str]) -> None:
        with self._lock:
            self.token_requests.append(dict(form_data))

    def token_exchange_count(self) -> int:
        with self._lock:
            return len(self.token_requests)

    def record_issued_token(self, access_token: str, lifetime: int) -> None:
        with self._lock:
            now = time.time()
            self.issued_tokens[access_token] = {
                "issued_at": now,
                "expires_at": now + lifetime,
                "lifetime": lifetime,
            }

    def revoke_token(self, access_token: str) -> None:
        with self._lock:
            self.revoked_tokens.add(access_token)

    def validate_bearer(self, access_token: str) -> bool:
        with self._lock:
            if access_token in self.revoked_tokens:
                return False
            rec = self.issued_tokens.get(access_token)
            if rec is None:
                return False
            if time.time() >= rec["expires_at"]:
                return False
        return True


class OAuthProxyHandler(BaseHTTPRequestHandler):
    """HTTP request handler for the OAuth mock control proxy."""

    server: MockOAuthServer  # type hint

    def do_HEAD(self) -> None:
        self._handle_proxy()

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/__admin/token_exchange_count":
            count = self.server.state.token_exchange_count()
            self._send_json(200, {"count": count})
            return
        self._handle_proxy()

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path.startswith("/__admin/"):
            self._handle_admin(parsed.path)
            return

        if parsed.path.endswith("/token"):
            self._handle_token_exchange()
            return

        self._handle_proxy()

    def _handle_admin(self, path: str) -> None:
        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length) if content_length > 0 else b"{}"
        data = json.loads(body.decode("utf-8")) if body else {}

        if path == "/__admin/reset":
            self.server.state.reset()
            self._send_json(200, {"status": "ok"})
        elif path == "/__admin/prime_token_error":
            self.server.state.prime_token_error(
                error=data["error"],
                description=data.get("description"),
                grant_type=data.get("grant_type"),
                once=data.get("once", True),
            )
            self._send_json(200, {"status": "ok"})
        elif path == "/__admin/set_token_lifetime":
            self.server.state.set_token_lifetime(int(data["expires_in"]))
            self._send_json(200, {"status": "ok"})
        elif path == "/__admin/register_refresh_token":
            self.server.state.register_refresh_token(
                data["refresh_token"],
                scope=data.get("scope", "https://mail.google.com/"),
            )
            self._send_json(200, {"status": "ok"})
        elif path == "/__admin/revoke_refresh_token":
            self.server.state.revoke_refresh_token(data["refresh_token"])
            self._send_json(200, {"status": "ok"})
        elif path == "/__admin/validate_bearer":
            valid = self.server.state.validate_bearer(data["access_token"])
            self._send_json(200, {"valid": valid})
        elif path == "/__admin/revoke_token":
            self.server.state.revoke_token(data["access_token"])
            self._send_json(200, {"status": "ok"})
        else:
            self._send_json(404, {"error": "unknown_admin_path"})

    def _handle_token_exchange(self) -> None:
        content_length = int(self.headers.get("Content-Length", 0))
        raw_body = self.rfile.read(content_length)
        parsed_body = parse_qs(raw_body.decode("utf-8", errors="replace"))
        form_data = {k: v[0] for k, v in parsed_body.items()}

        self.server.state.record_token_request(form_data)
        grant_type = form_data.get("grant_type", "")

        # 1. Check primed error
        primed = self.server.state.check_primed_error(grant_type)
        if primed:
            resp: dict[str, Any] = {"error": primed["error"]}
            if primed["description"]:
                resp["error_description"] = primed["description"]
            self._send_json(400, resp)
            return

        # 2. Check authorization_code grant
        if grant_type == "authorization_code":
            code = form_data.get("code", "")
            code_verifier = form_data.get("code_verifier", "")
            client_id = form_data.get("client_id", "")
            redirect_uri = form_data.get("redirect_uri", "")

            code_info = self.server.state.get_code(code)
            if not code_info:
                self._send_json(
                    400,
                    {
                        "error": "invalid_grant",
                        "error_description": "Unknown or expired authorization code",
                    },
                )
                return

            if code_info.get("client_id") and client_id and code_info["client_id"] != client_id:
                self._send_json(
                    400,
                    {
                        "error": "invalid_grant",
                        "error_description": "client_id mismatch",
                    },
                )
                return

            if (
                code_info.get("redirect_uri")
                and redirect_uri
                and code_info["redirect_uri"] != redirect_uri
            ):
                self._send_json(
                    400,
                    {
                        "error": "invalid_grant",
                        "error_description": "redirect_uri mismatch",
                    },
                )
                return

            expected_challenge = code_info.get("challenge")
            method = code_info.get("method", "plain")

            if expected_challenge:
                if method == "S256":
                    if not code_verifier or _s256_challenge(code_verifier) != expected_challenge:
                        self._send_json(
                            400,
                            {
                                "error": "invalid_grant",
                                "error_description": "PKCE verification failed",
                            },
                        )
                        return
                elif method == "plain" and code_verifier != expected_challenge:
                    self._send_json(
                        400,
                        {
                            "error": "invalid_grant",
                            "error_description": "PKCE verification failed",
                        },
                    )
                    return

            self.server.state.consume_code(code)

            # Try upstream first
            upstream_url = self.server.upstream_url.rstrip("/") + self.path
            headers = {
                k: v
                for k, v in self.headers.items()
                if k.lower() not in ("host", "content-length")
            }
            headers["Host"] = urlparse(self.server.upstream_url).netloc

            res = None
            try:
                with httpx.Client(follow_redirects=False, timeout=1.0) as client:
                    res = client.request(
                        method=self.command,
                        url=upstream_url,
                        headers=headers,
                        content=raw_body,
                    )
            except (httpx.HTTPError, OSError):
                res = None

            if res is not None and res.status_code == 200:
                try:
                    data = res.json()
                except (json.JSONDecodeError, ValueError):
                    data = None

                if isinstance(data, dict):
                    lifetime = self.server.state.custom_lifetime or data.get("expires_in", 3600)
                    data["expires_in"] = lifetime
                    if "refresh_token" not in data:
                        data["refresh_token"] = f"mock-refresh-token-{int(time.time() * 1000)}"
                    if "access_token" in data:
                        self.server.state.record_issued_token(data["access_token"], lifetime)
                    self.server.state.register_refresh_token(
                        data["refresh_token"],
                        scope=data.get("scope", "https://mail.google.com/"),
                    )
                    self._send_json(200, data)
                    return

            # Native token generation (standalone or upstream unavailable)
            lifetime = self.server.state.custom_lifetime or 3600
            now_ms = int(time.time() * 1000)
            access_token = f"mock-access-token-{now_ms}"
            refresh_token = f"mock-refresh-token-{now_ms}"
            scope = form_data.get("scope") or code_info.get("scope") or "https://mail.google.com/"
            self.server.state.record_issued_token(access_token, lifetime)
            self.server.state.register_refresh_token(refresh_token, scope=scope)
            self._send_json(
                200,
                {
                    "access_token": access_token,
                    "token_type": "Bearer",
                    "expires_in": lifetime,
                    "refresh_token": refresh_token,
                    "scope": scope,
                },
            )
            return

        # 3. Check refresh_token grant
        if grant_type == "refresh_token":
            rt = form_data.get("refresh_token", "")
            if not self.server.state.validate_refresh_token(rt):
                self._send_json(
                    400,
                    {
                        "error": "invalid_grant",
                        "error_description": "Invalid, unknown or revoked refresh token",
                    },
                )
                return

            lifetime = self.server.state.custom_lifetime or 3600
            now_ms = int(time.time() * 1000)
            new_access = f"mock-access-token-{now_ms}"
            self.server.state.record_issued_token(new_access, lifetime)
            rec = self.server.state.refresh_tokens.get(rt) or {}
            scope = form_data.get("scope") or rec.get("scope") or "https://mail.google.com/"
            self._send_json(
                200,
                {
                    "access_token": new_access,
                    "token_type": "Bearer",
                    "expires_in": lifetime,
                    "scope": scope,
                },
            )
            return

        self._send_json(
            400,
            {
                "error": "unsupported_grant_type",
                "error_description": f"Unsupported grant_type: {grant_type}",
            },
        )

    def _handle_proxy(self) -> None:
        upstream_base = self.server.upstream_url.rstrip("/")
        upstream_url = upstream_base + self.path

        if "/authorize?" in upstream_url or upstream_url.endswith("/authorize"):
            parsed_u = urlparse(upstream_url)
            q_dict = parse_qs(parsed_u.query)
            scopes = q_dict.get("scope", [""])[0].split()
            if "openid" not in scopes:
                scopes.insert(0, "openid")
                q_dict["scope"] = [" ".join(scopes)]
                new_query = urlencode(
                    {k: v[0] if len(v) == 1 else v for k, v in q_dict.items()},
                    doseq=True,
                )
                upstream_url = urlunparse(parsed_u._replace(query=new_query))
        headers = {k: v for k, v in self.headers.items() if k.lower() not in ("host", "content-length")}
        headers["Host"] = urlparse(self.server.upstream_url).netloc

        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length) if content_length > 0 else None

        res = None
        try:
            with httpx.Client(follow_redirects=False, timeout=1.0) as client:
                res = client.request(
                    method=self.command,
                    url=upstream_url,
                    headers=headers,
                    content=body,
                )
        except (httpx.HTTPError, OSError):
            res = None

        if res is None:
            # Standalone fallback when upstream is not reachable
            if self.path.endswith("/authorize") or "/authorize?" in self.path:
                import uuid

                parsed_query = parse_qs(urlparse(self.path).query)
                challenge = parsed_query.get("code_challenge", [None])[0]
                method = parsed_query.get("code_challenge_method", ["plain"])[0]
                client_id = parsed_query.get("client_id", [None])[0]
                redirect_uri = parsed_query.get("redirect_uri", [None])[0]
                scope = parsed_query.get("scope", [None])[0]
                state = parsed_query.get("state", [None])[0]

                code = f"mock-auth-code-{uuid.uuid4().hex}"
                self.server.state.store_code(
                    code=code,
                    challenge=challenge,
                    method=method,
                    client_id=client_id,
                    redirect_uri=redirect_uri,
                    scope=scope,
                )
                if redirect_uri:
                    sep = "&" if "?" in redirect_uri else "?"
                    loc = f"{redirect_uri}{sep}code={code}"
                    if state:
                        loc += f"&state={state}"
                    self.send_response(302)
                    self.send_header("Location", loc)
                    self.end_headers()
                    return

            if self.path.endswith("/.well-known/openid-configuration"):
                issuer_prefix = self.path.split("/.well-known")[0]
                base = f"http://{self.server.host}:{self.server.port}{issuer_prefix}"
                self._send_json(
                    200,
                    {
                        "issuer": base,
                        "authorization_endpoint": f"{base}/authorize",
                        "token_endpoint": f"{base}/token",
                        "jwks_uri": f"{base}/jwks",
                        "response_types_supported": ["code"],
                        "grant_types_supported": ["authorization_code", "refresh_token"],
                    },
                )
                return

            self._send_json(502, {"error": "upstream_error", "error_description": "Upstream unreachable"})
            return

        # Intercept authorize redirects to capture PKCE code challenge
        if self.path.endswith("/authorize") or "/authorize?" in self.path:
            parsed_query = parse_qs(urlparse(self.path).query)
            challenge = parsed_query.get("code_challenge", [None])[0]
            method = parsed_query.get("code_challenge_method", ["plain"])[0]
            client_id = parsed_query.get("client_id", [None])[0]
            redirect_uri = parsed_query.get("redirect_uri", [None])[0]
            scope = parsed_query.get("scope", [None])[0]

            location = res.headers.get("Location")
            if location and "code=" in location:
                loc_query = parse_qs(urlparse(location).query)
                code = loc_query.get("code", [""])[0]
                if code:
                    self.server.state.store_code(
                        code=code,
                        challenge=challenge,
                        method=method,
                        client_id=client_id,
                        redirect_uri=redirect_uri,
                        scope=scope,
                    )

        # Rewrite openid-configuration URLs so clients use this proxy
        if self.path.endswith("/.well-known/openid-configuration"):
            try:
                conf = res.json()
                if isinstance(conf, dict):
                    proxy_base = f"http://{self.server.host}:{self.server.port}"
                    rewritten = {}
                    for k, v in conf.items():
                        if isinstance(v, str) and v.startswith(upstream_base):
                            rewritten[k] = v.replace(upstream_base, proxy_base)
                        else:
                            rewritten[k] = v
                    self._send_json(200, rewritten)
                    return
            except (json.JSONDecodeError, ValueError) as exc:
                logger.debug("Failed to rewrite openid-configuration: %s", exc)

        # Rewrite Location header in redirects if pointing to upstream
        res_headers = dict(res.headers)
        if "location" in res_headers:
            loc = res_headers["location"]
            if loc.startswith(upstream_base):
                proxy_base = f"http://{self.server.host}:{self.server.port}"
                res_headers["location"] = loc.replace(upstream_base, proxy_base)

        self._send_raw(res.status_code, res_headers, res.content)

    def _send_json(self, status: int, data: dict[str, Any]) -> None:
        payload = json.dumps(data).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json;charset=UTF-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def _send_raw(self, status: int, headers: Any, content: bytes) -> None:
        self.send_response(status)
        for k, v in headers.items():
            if k.lower() in ("content-length", "transfer-encoding", "content-encoding"):
                continue
            self.send_header(k, v)
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)

    def log_message(self, format: str, *args: Any) -> None:
        # Suppress noisy standard request logging
        pass


class MockOAuthServer(ThreadingHTTPServer):
    """The local HTTP mock and control server."""

    def __init__(
        self,
        upstream_url: str = "http://127.0.0.1:19080",
        host: str = "127.0.0.1",
        port: int = 19085,
    ) -> None:
        self.upstream_url = upstream_url.rstrip("/")
        self.host = host
        self.state = OAuthMockState()
        super().__init__((host, port), OAuthProxyHandler)
        self.actual_port = self.server_address[1]
        self._thread: threading.Thread | None = None

    @property
    def port(self) -> int:
        return self.actual_port

    @property
    def base_url(self) -> str:
        return f"http://{self.host}:{self.port}"

    def start(self) -> MockOAuthServer:
        if self._thread is not None:
            return self
        self._thread = threading.Thread(target=self.serve_forever, daemon=True)
        self._thread.start()
        # Verify server is listening
        for _ in range(50):
            try:
                with httpx.Client() as client:
                    r = client.get(f"{self.base_url}/__admin/token_exchange_count", timeout=0.2)
                    if r.status_code == 200:
                        break
            except (httpx.HTTPError, OSError):
                time.sleep(0.02)
        return self

    def stop(self) -> None:
        if self._thread is None:
            return
        self.shutdown()
        self.server_close()
        self._thread.join(timeout=3.0)
        self._thread = None

    def __enter__(self) -> Self:
        self.start()
        return self

    def __exit__(self, *args: object) -> None:
        self.stop()
