"""Stdio-subprocess wrapper around the imap-mcp server.

The BDD harness talks to the server exclusively through this client.
It speaks the MCP Initialize / list_tools / tools/call handshakes as
JSON-RPC 2.0 framed over line-delimited stdio, the way the MCP stdio
transport expects.

No Python-level import of the server package occurs. The server is
started as `SERVER_BINARY` (path configurable via IMAP_MCP_SERVER_BINARY
env var), and communication is byte-level. This is deliberate: the BDD
suite must exercise the same surface any other MCP client would.
"""

from __future__ import annotations

import io
import json
import os
import subprocess
import threading
import time
import uuid
from dataclasses import dataclass
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
        self._rpc(
            "initialize",
            {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "imap-mcp-bdd", "version": "0.1.0"},
            },
        )
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
