"""Subprocess wrappers around the imap-mcp server.

The BDD harness talks to the server exclusively through these clients.
They speak the MCP Initialize / list_tools / tools/call handshakes as
JSON-RPC 2.0 framed over the chosen transport.
"""

from __future__ import annotations

import io
import json
import os
import queue
import socket
import subprocess
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx


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
    unset_env: tuple[str, ...] = ()
    client_name: str = "imap-mcp-bdd"
    client_version: str = "0.1.0"
    last_response_text_bytes: int = 0
    server_info: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self._proc: subprocess.Popen[bytes] | None = None
        self._stdin: io.BufferedWriter | None = None
        self._stdout_queue: queue.Queue[bytes | None] = queue.Queue()
        self._unhandled_responses: dict[str, dict[str, Any]] = {}
        self._stdout_thread: threading.Thread | None = None
        self._stderr_log: list[bytes] = []
        self._stderr_thread: threading.Thread | None = None
        self._initialized = False

    def start(self) -> None:
        if self._proc is not None:
            raise MCPClientError("MCPClient already started")

        env = dict(os.environ)
        env["IMAP_MCP_CONFIG_DIR"] = str(self.config_dir)
        env["IMAP_MCP_CALLER_ID"] = self.caller_id
        for name in self.unset_env:
            env.pop(name, None)
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
        if self._proc.stdin is None or self._proc.stdout is None:
            raise MCPClientError("Process stdin/stdout not available")
        self._stdin = self._proc.stdin

        self._stdout_thread = threading.Thread(
            target=self._drain_stdout, args=(self._proc.stdout,), daemon=True
        )
        self._stdout_thread.start()

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

    def close_stdin(self) -> None:
        if self._stdin is None:
            raise MCPClientError("Server stdin not available")
        self._stdin.close()
        self._stdin = None

    def send_signal(self, sig: int) -> None:
        if self._proc is None:
            raise MCPClientError("Server process not started")
        self._proc.send_signal(sig)

    def poll_exit(self) -> int | None:
        if self._proc is None:
            raise MCPClientError("Server process not started")
        return self._proc.poll()

    def list_tools(self) -> list[dict[str, Any]]:
        result = self._rpc("tools/list", {})
        return list(result.get("tools", []))

    def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        result = self._rpc(
            "tools/call",
            {"name": name, "arguments": arguments},
        )
        contents = result.get("content", [])
        if contents and isinstance(contents, list) and isinstance(contents[0], dict) and "text" in contents[0]:
            self.last_response_text_bytes = len(contents[0]["text"].encode("utf-8"))
        else:
            self.last_response_text_bytes = 0
        return result

    def call_tools_concurrently(
        self, calls: list[tuple[str, dict[str, Any]]], timeout: float = 60.0
    ) -> list[dict[str, Any]]:
        request_ids = [str(uuid.uuid4()) for _ in calls]
        for request_id, (name, arguments) in zip(request_ids, calls):
            self._send(
                {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "method": "tools/call",
                    "params": {"name": name, "arguments": arguments},
                }
            )
        pending = set(request_ids)
        responses: dict[str, dict[str, Any]] = {}
        for req_id in list(pending):
            if req_id in self._unhandled_responses:
                responses[req_id] = self._unhandled_responses.pop(req_id)
                pending.discard(req_id)

        deadline = time.monotonic() + timeout
        while pending:
            remaining = max(0.01, deadline - time.monotonic())
            try:
                line = self._stdout_queue.get(timeout=remaining)
            except queue.Empty:
                raise MCPClientError(
                    f"Timed out waiting for {len(pending)} of {len(calls)} responses. Stderr:\n{self.stderr_text}"
                ) from None
            if line is None:
                raise MCPClientError(
                    f"Server closed stdout with {len(pending)} responses outstanding. "
                    f"Stderr:\n{self.stderr_text}"
                )
            line = line.strip()
            if not line:
                continue
            message = json.loads(line.decode("utf-8"))
            msg_id = message.get("id")
            if msg_id in pending:
                pending.discard(msg_id)
                responses[msg_id] = message
            elif msg_id:
                self._unhandled_responses[msg_id] = message

        results: list[dict[str, Any]] = []
        for request_id in request_ids:
            response = responses[request_id]
            if "error" in response:
                err = response["error"]
                raise MCPRPCError(err["code"], err.get("message", ""), err.get("data"))
            results.append(response.get("result", {}))
        return results

    def raw_call(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        return self._rpc(method, params)

    def _initialize(self) -> None:
        result = self._rpc(
            "initialize",
            {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": self.client_name, "version": self.client_version},
            },
        )
        self.server_info = dict(result.get("serverInfo", {}))
        instructions = result.get("instructions") or ""
        marker = "imap-mcp surface metadata: "
        if marker in instructions:
            tail = instructions.split(marker, 1)[1].strip()
            try:
                metadata = json.loads(tail)
            except json.JSONDecodeError:
                metadata = {}
            if isinstance(metadata, dict):
                self.server_info["metadata"] = metadata
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
        if request_id in self._unhandled_responses:
            return self._unhandled_responses.pop(request_id)

        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            remaining = max(0.01, deadline - time.monotonic())
            try:
                line = self._stdout_queue.get(timeout=remaining)
            except queue.Empty:
                break
            if line is None:
                stderr = self.stderr_text
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
                raise MCPClientError(f"Non-JSON line on stdout: {line!r} ({exc})") from exc

            msg_id = message.get("id")
            if msg_id == request_id:
                return message
            if msg_id is not None:
                self._unhandled_responses[msg_id] = message

        stderr = self.stderr_text
        raise MCPClientError(f"Timed out after {timeout}s waiting for response to {request_id}. Stderr:\n{stderr}")

    def _drain_stdout(self, stream: io.BufferedReader) -> None:
        while True:
            line = stream.readline()
            if not line:
                self._stdout_queue.put(None)
                break
            self._stdout_queue.put(line)

    def _drain_stderr(self, stream: io.BufferedReader) -> None:
        for chunk in iter(lambda: stream.read(4096), b""):
            if not chunk:
                break
            self._stderr_log.append(chunk)

    @property
    def stderr_text(self) -> str:
        return b"".join(self._stderr_log).decode("utf-8", "replace")


def _pick_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


@dataclass
class MCPHttpClient:
    """Streamable HTTP variant of `MCPClient`."""

    server_binary: Path
    config_dir: Path
    extra_env: dict[str, str] | None = None
    host: str = "127.0.0.1"
    port: int = 0
    bearer_token: str | None = None
    caller_id: str | None = None
    client_name: str = "imap-mcp-bdd-http"
    client_version: str = "0.1.0"
    last_response_text_bytes: int = 0
    server_info: dict[str, Any] = field(default_factory=dict)
    _proc: subprocess.Popen[bytes] | None = field(default=None, init=False)
    _stderr_log: list[bytes] = field(default_factory=list, init=False)
    _stderr_thread: threading.Thread | None = field(default=None, init=False)
    _initialized: bool = field(default=False, init=False)
    _session_id: str | None = field(default=None, init=False)

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
        if self._proc.stdout is None or self._proc.stderr is None:
            raise MCPClientError("Process stdout/stderr not available")
        self._stderr_thread = threading.Thread(
            target=self._drain_stderr,
            args=(self._proc.stderr,),
            daemon=True,
        )
        self._stderr_thread.start()
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

    def initialize(
        self, caller_id: str, bearer_token: str
    ) -> dict[str, Any]:
        self.caller_id = caller_id
        self.bearer_token = bearer_token
        result = self._rpc(
            "initialize",
            {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": self.client_name, "version": self.client_version},
            },
        )
        self.server_info = dict(result.get("serverInfo", {}))
        self._initialized = True
        return result

    def list_tools(self) -> list[dict[str, Any]]:
        result = self._rpc("tools/list", {})
        return list(result.get("tools", []))

    def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        result = self._rpc("tools/call", {"name": name, "arguments": arguments})
        contents = result.get("content", [])
        if contents and isinstance(contents, list) and isinstance(contents[0], dict) and "text" in contents[0]:
            self.last_response_text_bytes = len(contents[0]["text"].encode("utf-8"))
        else:
            self.last_response_text_bytes = 0
        return result

    def raw_call(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        return self._rpc(method, params)

    def raw_http_request(
        self,
        method: str,
        path: str,
        headers: dict[str, str] | None = None,
        content: bytes | None = None,
        json_data: Any = None,
        timeout: float = 10.0,
    ) -> httpx.Response:
        url = f"http://{self.host}:{self.port}" + (path if path.startswith("/") else f"/{path}")
        req_headers = dict(headers or {})
        if self._session_id and "Mcp-Session-Id" not in req_headers:
            req_headers["Mcp-Session-Id"] = self._session_id
        if self.bearer_token and "Authorization" not in req_headers:
            req_headers["Authorization"] = f"Bearer {self.bearer_token}"
        if self.caller_id and "X-MCP-Caller-Id" not in req_headers:
            req_headers["X-MCP-Caller-Id"] = self.caller_id
        return httpx.request(
            method,
            url,
            headers=req_headers,
            content=content,
            json=json_data,
            timeout=timeout,
        )

    @property
    def url(self) -> str:
        return f"http://{self.host}:{self.port}/mcp/"

    def _rpc(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
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

        for hdr_name, hdr_val in response.headers.items():
            if hdr_name.lower() == "mcp-session-id":
                self._session_id = hdr_val
                break

        ctype = response.headers.get("content-type", "")
        if "text/event-stream" in ctype:
            payload = self._parse_sse(response.text, request_id)
        else:
            try:
                payload = response.json()
            except json.JSONDecodeError as exc:
                raise MCPClientError(
                    f"Non-JSON response: {response.text!r} ({exc})"
                ) from exc

        if isinstance(payload, list):
            payload = next(
                (m for m in payload if m.get("id") == request_id), payload[0]
            )
        if "error" in payload:
            err = payload["error"]
            raise MCPRPCError(err["code"], err.get("message", ""), err.get("data"))
        return payload.get("result", {})

    def _parse_sse(self, text: str, request_id: str) -> dict[str, Any]:
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
