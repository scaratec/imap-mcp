"""The imap-mcp server under test: binary, version and tool list."""

from __future__ import annotations

import json
import os
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

    with tempfile.TemporaryDirectory(prefix="imap-mcp-usability-probe-") as tmp:
        config = Path(tmp)
        (config / "policies").mkdir()
        (config / "accounts.yaml").write_text(
            "accounts: []\nsecret_store: {backend: file_dir, path: " + json.dumps(str(config)) + ", acknowledge_plaintext: true}\n"
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
            version = None
            # ADR 0038 / R-15: Read package and tool set versions via tool_surface_info or initialize server_info
            try:
                info = client.call_tool("tool_surface_info", {})
                text = info.get("content", [{}])[0].get("text", "{}")
                payload = json.loads(text)
                tool_set = payload.get("tool_set_version")
                version = payload.get("package_version")
            except Exception:  # noqa: BLE001 - optional
                tool_set = None
            if not version and hasattr(client, "server_info") and isinstance(client.server_info, dict):
                version = client.server_info.get("version")
        finally:
            client.close()
    return ServerInfo(SERVER_BINARY, version, tool_set, tools)
