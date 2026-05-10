"""Subprocess wrappers around the imap-mcp server.

The BDD harness talks to the server exclusively through these clients.
They speak the MCP Initialize / list_tools / tools/call handshakes as
JSON-RPC 2.0 framed over the chosen transport.

Two transports are supported:

- `MCPClient` — stdio. Caller identity is the `IMAP_MCP_CALLER_ID` env
  var (stdio_trusted, ADR 0015).
- `MCPHttpClient` — Streamable HTTP. Caller identity comes from the
  `X-MCP-Caller-Id` header; the bearer token from
  `Authorization: Bearer <token>` (LIM-0007 paydown, ADR 0023).

No Python-level import of the server package occurs in either client.
The server is started as `SERVER_BINARY` (path configurable via
IMAP_MCP_SERVER_BINARY env var), and communication is byte-level. This
is deliberate: the BDD suite must exercise the same surface any other
MCP client would.
"""

from __future__ import annotations

import io
import json
import os
import socket
import subprocess
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


class MCPRPCError(Exception):
    """Raised when the server returns a JSON-RPC error object."""

    def __init__(self, code: int, message: str, data: Any = None) -> None:
        super().__init__(f"JSON-RPC error {code}: {message}")
        self.code = code
        self.message = message
        self.data = data


class MCPClientError(Exception):
    """Raised for transport-level failures."""


@dataclass
class MCPClient:
    """Manages a single stdio-subprocess of the server and speaks MCP."""

    server_binary: Path
    config_dir: Path
    caller_id: str
    extra_env: dict[str, str] | None = None

    def __post_init__(self) -> None:
        self._proc: subprocess.Popen[bytes] | None = None
        self._stdin: io.BufferedWriter | None = None
        self._stdout: io.BufferedReader | None = None
        self._stderr_log: list[bytes] = []
        self._stderr_thread: threading.Thread | None = None
        self._initialized = False

    # --------------------------------------------------------- lifecycle

    def start(self) -> None:
        if self._proc is not None:
            raise MCPClientError("MCPClient already started")

        env = dict(os.environ)
        env["IMAP_MCP_CONFIG_DIR"] = str(self.config_dir)
        env["IMAP_MCP_CALLER_ID"] = self.caller_id
        if self.extra_env:
            env.update(self.extra_env)

        self._proc = subprocess.Popen(
            [str(self.server_binary), "--transport", "stdio"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
            bufsize=0,
        )
        assert self._proc.stdin is not None and self._proc.stdout is not None
        self._stdin = self._proc.stdin
        self._stdout = self._proc.stdout

        self._stderr_thread = threading.Thread(
            target=self._drain_stderr, args=(self._proc.stderr,), daemon=True
        )
        self._stderr_thread.start()

        self._initialize()

    def close(self, timeout: float = 3.0) -> None:
        if self._proc is None:
            return
        try:
            self._proc.terminate()
            try:
                self._proc.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                self._proc.kill()
                self._proc.wait(timeout=timeout)
        finally:
            self._proc = None
            self._stdin = None
            self._stdout = None

    # --------------------------------------------------------- MCP API

    def list_tools(self) -> list[dict[str, Any]]:
        result = self._rpc("tools/list", {})
        return list(result.get("tools", []))

    def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """Invoke a tool and return its result payload.

        Tool call errors surface as MCPRPCError so step code can assert
        on codes. Payload-level DENY/ALLOW outcomes return normally.
        """
        return self._rpc(
            "tools/call",
            {"name": name, "arguments": arguments},
        )

    def raw_call(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        """Escape hatch for calling arbitrary JSON-RPC methods.

        Used by non_goal_rejection.feature to probe nonexistent tool
        names and by tests that need to inspect JSON-RPC error codes.
        """
        return self._rpc(method, params)

    # --------------------------------------------------------- internals

    def _initialize(self) -> None:
        result = self._rpc(
            "initialize",
            {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "imap-mcp-bdd", "version": "0.1.0"},
            },
        )
        self.server_info = result.get("serverInfo", {})
        self._notify("notifications/initialized", {})
        self._initialized = True

    def _rpc(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        request_id = str(uuid.uuid4())
        request = {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": method,
            "params": params,
        }
        self._send(request)
        response = self._read_matching(request_id)
        if "error" in response:
            err = response["error"]
            raise MCPRPCError(err["code"], err.get("message", ""), err.get("data"))
        return response.get("result", {})

    def _notify(self, method: str, params: dict[str, Any]) -> None:
        notification = {"jsonrpc": "2.0", "method": method, "params": params}
        self._send(notification)

    def _send(self, payload: dict[str, Any]) -> None:
        if self._stdin is None:
            raise MCPClientError("Server stdin not available")
        line = json.dumps(payload, separators=(",", ":")).encode("utf-8") + b"\n"
        self._stdin.write(line)
        self._stdin.flush()

    def _read_matching(self, request_id: str, timeout: float = 10.0) -> dict[str, Any]:
        deadline = time.monotonic() + timeout
        if self._stdout is None:
            raise MCPClientError("Server stdout not available")
        while time.monotonic() < deadline:
            line = self._stdout.readline()
            if not line:
                stderr = b"".join(self._stderr_log).decode("utf-8", "replace")
                raise MCPClientError(
                    f"Server closed stdout before responding to {request_id}. "
                    f"Stderr:\n{stderr}"
                )
            line = line.strip()
            if not line:
                continue
            try:
                message = json.loads(line.decode("utf-8"))
            except json.JSONDecodeError as exc:
                raise MCPClientError(f"Non-JSON line on stdout: {line!r} ({exc})")
            if message.get("id") == request_id:
                return message
        raise MCPClientError(f"Timed out waiting for response to {request_id}")

    def _drain_stderr(self, stream: io.BufferedReader) -> None:
        for chunk in iter(lambda: stream.read(4096), b""):
            if not chunk:
                break
            self._stderr_log.append(chunk)

    @property
    def stderr_text(self) -> str:
        return b"".join(self._stderr_log).decode("utf-8", "replace")


def _pick_free_port() -> int:
    """Reserve and release an ephemeral TCP port. Standard test pattern;
    a transient race can occur if a concurrent process grabs the port
    in the half-second before the server binds, but in CI it's fine."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


@dataclass
class MCPHttpClient:
    """Streamable HTTP variant of `MCPClient`.

    The server is launched as a subprocess with `--transport http`
    on a randomly chosen free port, then driven via JSON-RPC POST
    to `/mcp/`. The bearer token + caller_id are sent on every
    request as HTTP headers.

    Initialize is a normal JSON-RPC method; failures surface as
    HTTP 401 (auth path) or as a JSON-RPC error (protocol path).
    """

    server_binary: Path
    config_dir: Path
    extra_env: dict[str, str] | None = None
    host: str = "127.0.0.1"
    port: int = 0
    bearer_token: str | None = None
    caller_id: str | None = None
    _proc: subprocess.Popen[bytes] | None = field(default=None, init=False)
    _stderr_log: list[bytes] = field(default_factory=list, init=False)
    _stderr_thread: threading.Thread | None = field(default=None, init=False)
    _initialized: bool = field(default=False, init=False)
    _session_id: str | None = field(default=None, init=False)

    # ---------------------------------------------------------- lifecycle

    def start_server(self, port: int | None = None) -> None:
        if self._proc is not None:
            raise MCPClientError("server already started")
        self.port = port or _pick_free_port()
        env = dict(os.environ)
        env["IMAP_MCP_CONFIG_DIR"] = str(self.config_dir)
        if self.extra_env:
            env.update(self.extra_env)
        self._proc = subprocess.Popen(
            [
                str(self.server_binary),
                "--transport", "http",
                "--host", self.host,
                "--port", str(self.port),
            ],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
            bufsize=0,
        )
        self._stderr_thread = threading.Thread(
            target=self._drain_stderr,
            args=(self._proc.stderr,),
            daemon=True,
        )
        self._stderr_thread.start()
        # Drain stdout in the background too so a noisy server doesn't
        # deadlock on a full pipe.
        threading.Thread(
            target=self._drain_stderr,
            args=(self._proc.stdout,),
            daemon=True,
        ).start()
        self._wait_for_listening(timeout=5.0)

    def _wait_for_listening(self, timeout: float) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self._proc and self._proc.poll() is not None:
                stderr = b"".join(self._stderr_log).decode("utf-8", "replace")
                raise MCPClientError(
                    f"Server exited before listening (rc="
                    f"{self._proc.returncode}). Stderr:\n{stderr}"
                )
            try:
                with socket.create_connection((self.host, self.port), timeout=0.5):
                    return
            except OSError:
                time.sleep(0.1)
        raise MCPClientError(
            f"Server did not start listening on {self.host}:{self.port} "
            f"within {timeout}s"
        )

    def close(self, timeout: float = 3.0) -> None:
        if self._proc is None:
            return
        try:
            self._proc.terminate()
            try:
                self._proc.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                self._proc.kill()
                self._proc.wait(timeout=timeout)
        finally:
            self._proc = None

    # ------------------------------------------------------------- MCP API

    def initialize(
        self, caller_id: str, bearer_token: str
    ) -> dict[str, Any]:
        """Perform the MCP Initialize handshake. Returns the result on
        success; raises MCPClientError on transport-level failure or
        MCPRPCError on JSON-RPC error."""
        self.caller_id = caller_id
        self.bearer_token = bearer_token
        result = self._rpc(
            "initialize",
            {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "imap-mcp-bdd-http", "version": "0.1.0"},
            },
        )
        # The SDK returns the session id in a header; with stateless=True
        # there is none, but we record it for completeness.
        self._initialized = True
        return result

    def list_tools(self) -> list[dict[str, Any]]:
        result = self._rpc("tools/list", {})
        return list(result.get("tools", []))

    def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        return self._rpc("tools/call", {"name": name, "arguments": arguments})

    def raw_call(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        return self._rpc(method, params)

    # -------------------------------------------------------- internals

    @property
    def url(self) -> str:
        return f"http://{self.host}:{self.port}/mcp/"

    def _rpc(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        import httpx

        request_id = str(uuid.uuid4())
        body = {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": method,
            "params": params,
        }
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }
        if self.bearer_token:
            headers["Authorization"] = f"Bearer {self.bearer_token}"
        if self.caller_id is not None:
            headers["X-MCP-Caller-Id"] = self.caller_id
        if self._session_id is not None:
            headers["Mcp-Session-Id"] = self._session_id

        try:
            response = httpx.post(self.url, json=body, headers=headers, timeout=10.0)
        except httpx.HTTPError as exc:
            raise MCPClientError(f"HTTP transport error: {exc}") from exc

        if response.status_code == 401:
            try:
                err = response.json()
            except json.JSONDecodeError:
                err = {"error": "auth_failed"}
            raise MCPRPCError(
                code=-32001,
                message=str(err.get("error") or "auth_failed"),
                data=err,
            )
        if response.status_code == 404:
            raise MCPRPCError(
                code=-32601,
                message=f"HTTP 404 for {self.url}",
            )
        if response.status_code >= 400:
            raise MCPClientError(
                f"HTTP {response.status_code} from server: {response.text!r}"
            )

        # Capture session id on first stateful response (no-op when
        # the server runs stateless=True).
        if "mcp-session-id" in response.headers and self._session_id is None:
            self._session_id = response.headers["mcp-session-id"]

        # Streamable HTTP returns either application/json or
        # text/event-stream; with json_response=True on the server it
        # is the former, but be lenient.
        ctype = response.headers.get("content-type", "")
        if "text/event-stream" in ctype:
            payload = self._parse_sse(response.text, request_id)
        else:
            try:
                payload = response.json()
            except json.JSONDecodeError as exc:
                raise MCPClientError(
                    f"Non-JSON response: {response.text!r} ({exc})"
                )

        if isinstance(payload, list):
            payload = next(
                (m for m in payload if m.get("id") == request_id), payload[0]
            )
        if "error" in payload:
            err = payload["error"]
            raise MCPRPCError(err["code"], err.get("message", ""), err.get("data"))
        return payload.get("result", {})

    def _parse_sse(self, text: str, request_id: str) -> dict[str, Any]:
        """Pick the JSON-RPC message matching `request_id` out of an
        SSE-framed response."""
        for block in text.split("\n\n"):
            for line in block.splitlines():
                if not line.startswith("data:"):
                    continue
                data = line[len("data:"):].strip()
                if not data:
                    continue
                try:
                    msg = json.loads(data)
                except json.JSONDecodeError:
                    continue
                if msg.get("id") == request_id:
                    return msg
        raise MCPClientError(
            f"No SSE message matched request_id={request_id!r}: {text!r}"
        )

    def _drain_stderr(self, stream: io.BufferedReader) -> None:
        for chunk in iter(lambda: stream.read(4096), b""):
            if not chunk:
                break
            self._stderr_log.append(chunk)

    @property
    def stderr_text(self) -> str:
        return b"".join(self._stderr_log).decode("utf-8", "replace")
