"""Run the server subprocess *to completion* to capture startup errors.

Load-time validation scenarios (ADR 0014, the policy_reload feature,
and the whitelist/blacklist loader-rejection cases) describe a server
that refuses to start with a specific error message. This helper
starts the server, sends it an IMAP-free MCP `initialize` on stdin to
force it to walk the config-load path, and returns whatever non-zero
exit + stderr combination resulted.

Kept separate from MCPClient because that one is tuned for the happy
path (start, serve, exchange tool calls). Mixing the two in one class
would hide the intent.
"""

from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class BootstrapResult:
    exit_code: int
    stderr: str
    stdout: str


def try_bootstrap_server(
    server_binary: Path,
    config_dir: Path,
    caller_id: str,
    timeout: float = 5.0,
) -> BootstrapResult:
    """Attempt to run the server once; return the observed outcome.

    A clean-start server will stay alive until stdin EOF or the MCP
    initialize + shutdown handshake finishes; we short-circuit by
    sending a single invalid-enough line on stdin (an empty line),
    which the stdio transport tolerates as "no request yet", then
    close stdin. If the server was going to fail at config load it
    will have done so before even looking at stdin.
    """
    env = dict(os.environ)
    env["IMAP_MCP_CONFIG_DIR"] = str(config_dir)
    env["IMAP_MCP_CALLER_ID"] = caller_id

    proc = subprocess.Popen(
        [str(server_binary), "--transport", "stdio"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
    )
    try:
        # Send an MCP initialize so the server goes through the full
        # "can I serve?" code path; if config-load would fail, it
        # fails here regardless.
        request = (
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": "bootstrap-probe",
                    "method": "initialize",
                    "params": {
                        "protocolVersion": "2024-11-05",
                        "capabilities": {},
                        "clientInfo": {"name": "bdd-bootstrap", "version": "0.1"},
                    },
                }
            ).encode("utf-8")
            + b"\n"
        )
        stdout, stderr = proc.communicate(input=request, timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        stdout, stderr = proc.communicate()
    return BootstrapResult(
        exit_code=proc.returncode if proc.returncode is not None else -1,
        stderr=stderr.decode("utf-8", errors="replace"),
        stdout=stdout.decode("utf-8", errors="replace"),
    )
