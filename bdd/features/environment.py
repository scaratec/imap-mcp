"""Behave lifecycle hooks for the imap-mcp BDD suite.

Orchestrates the shared test fixtures (two dovecot IMAP instances via
docker compose) and the per-scenario state reset (fresh IMAP mailboxes,
fresh config dir, fresh audit dir, fresh WAL).

This file is deliberately thin glue. Fachlogik lives in feature files
and never here (BDD-Guidelines §1.3, §5.1).
"""

from __future__ import annotations

import os
import shutil
import socket
import subprocess
import tempfile
import time
from pathlib import Path

from behave.model import Feature, Scenario
from behave.runner import Context

import sys

# This file lives at bdd/features/environment.py; the `bdd/` dir holds
# the `support/` package that the steps import from.
BDD_ROOT = Path(__file__).resolve().parent.parent
if str(BDD_ROOT) not in sys.path:
    sys.path.insert(0, str(BDD_ROOT))

from support.imap_fixture import IMAPFixture
from support.mcp_client import MCPClient

DOCKER_DIR = BDD_ROOT / "docker"

# Server binary is located outside this project so that no Python-level
# dependency on ../server/ ever slips into the harness. The binary lives
# in server/.venv/bin/imap-mcp after `pip install -e .` in server/.
SERVER_BINARY = Path(
    os.environ.get(
        "IMAP_MCP_SERVER_BINARY",
        BDD_ROOT.parent / "server" / ".venv" / "bin" / "imap-mcp",
    )
).resolve()

# Host-port mapping per docker-compose.yml.
IMAP_INSTANCES: dict[str, tuple[str, int]] = {
    "imap-a": ("127.0.0.1", 11143),
    "imap-b": ("127.0.0.1", 12143),
}

READINESS_TIMEOUT_SECONDS = 30


def before_all(context: Context) -> None:
    """Start the shared dovecot fixture once per suite."""
    _compose("down", "-v", check=False)
    _compose("up", "-d")
    _wait_for_imap_ready()
    context.imap_instances = IMAP_INSTANCES
    context.bdd_root = BDD_ROOT


def after_all(context: Context) -> None:
    """Tear the dovecot fixture down completely."""
    _compose("down", "-v", check=False)


def before_scenario(context: Context, scenario: Scenario) -> None:
    """Reset state so every scenario starts from a clean slate.

    Steps performed:
      1. Wipe and re-create a scratch directory for this scenario
         (config, secrets, wal, audit all live here).
      2. Wipe every test user's mailbox on both dovecot instances.
      3. Leave the server process not-yet-started. A step file will
         start it once the scenario's server configuration is known.
    """
    context.scratch_dir = Path(tempfile.mkdtemp(prefix="imap-mcp-bdd-"))
    context.config_dir = context.scratch_dir / "config"
    context.secrets_dir = context.scratch_dir / "secrets"
    context.audit_dir = context.scratch_dir / "audit"
    context.wal_path = context.scratch_dir / "wal.db"
    context.config_dir.mkdir()
    context.secrets_dir.mkdir()
    context.audit_dir.mkdir()

    context.imap = IMAPFixture(IMAP_INSTANCES)
    context.imap.reset_all_users()

    context.mcp: MCPClient | None = None  # step files create it lazily


def after_scenario(context: Context, scenario: Scenario) -> None:
    """Terminate the MCP server and remove scratch state."""
    mcp = getattr(context, "mcp", None)
    if mcp is not None:
        mcp.close()
        context.mcp = None
    scratch = getattr(context, "scratch_dir", None)
    if scratch is not None and scratch.exists():
        shutil.rmtree(scratch, ignore_errors=True)


# --------------------------------------------------------------------- helpers


def _compose(*args: str, check: bool = True) -> None:
    """Invoke `docker compose` from the BDD docker directory."""
    cmd = ["docker", "compose", *args]
    subprocess.run(cmd, cwd=DOCKER_DIR, check=check, capture_output=True)


def _wait_for_imap_ready() -> None:
    """Block until both dovecot instances accept IMAP LOGIN."""
    deadline = time.monotonic() + READINESS_TIMEOUT_SECONDS
    for name, (host, port) in IMAP_INSTANCES.items():
        while True:
            if time.monotonic() > deadline:
                raise RuntimeError(
                    f"Dovecot instance {name} at {host}:{port} not ready within "
                    f"{READINESS_TIMEOUT_SECONDS}s"
                )
            try:
                with socket.create_connection((host, port), timeout=1.0) as sock:
                    banner = sock.recv(128)
                if b"OK" in banner:
                    break
            except (OSError, socket.timeout):
                pass
            time.sleep(0.5)
