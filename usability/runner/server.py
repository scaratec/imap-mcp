"""The imap-mcp server under test: binary, version and tool list."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

from . import REPO_ROOT

SERVER_BINARY = Path(
    os.environ.get("IMAP_MCP_SERVER_BINARY", REPO_ROOT / "server" / ".venv" / "bin" / "imap-mcp")
)

_BDD = REPO_ROOT / "bdd"
if str(_BDD) not in sys.path:
    sys.path.insert(0, str(_BDD))


@dataclass(frozen=True)
class ServerInfo:
    binary: Path
    version: str | None
    tool_set_version: str | None
    tools: list[str]


def server_info() -> ServerInfo:
    """Package version and the tool list, from a server started without IMAP."""
    from support.mcp_client import MCPClient

    if not SERVER_BINARY.exists():
        raise FileNotFoundError(f"server binary not found: {SERVER_BINARY}")
    python = SERVER_BINARY.parent / "python"
    version = None
    if python.exists():
        out = subprocess.run(
            [str(python), "-c", "import importlib.metadata as m; print(m.version('sc-imap-mcp'))"],
            capture_output=True, text=True, timeout=30,
        )
        version = out.stdout.strip() or None
    with tempfile.TemporaryDirectory(prefix="imap-mcp-usability-probe-") as tmp:
        config = Path(tmp)
        (config / "policies").mkdir()
        (config / "accounts.yaml").write_text(
            "accounts: []\nsecret_store: {backend: file_dir, path: " + json.dumps(str(config)) + "}\n"
            "audit: {directory: " + json.dumps(str(config / "audit")) + "}\n"
            "wal: {path: " + json.dumps(str(config / "wal.db")) + "}\n",
            encoding="utf-8",
        )
        (config / "callers.yaml").write_text(
            "callers:\n  - {id: probe, policy: probe, auth: {type: stdio_trusted}}\n", encoding="utf-8"
        )
        (config / "policies" / "probe.yaml").write_text("name: probe\naccounts: {}\n", encoding="utf-8")
        client = MCPClient(SERVER_BINARY, config, "probe", client_name="usability-runner-probe")
        client.start()
        try:
            tools = sorted(t["name"] for t in client.list_tools())
            tool_set = None
            try:
                info = client.call_tool("tool_surface_info", {})
                text = info.get("content", [{}])[0].get("text", "{}")
                tool_set = json.loads(text).get("tool_set_version")
            except Exception:  # noqa: BLE001 - optional
                tool_set = None
        finally:
            client.close()
    return ServerInfo(SERVER_BINARY, version, tool_set, tools)
