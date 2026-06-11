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
import threading

import urllib.request
import urllib.error

# This file lives at bdd/features/environment.py; the `bdd/` dir holds
# the `support/` package that the steps import from.
BDD_ROOT = Path(__file__).resolve().parent.parent
if str(BDD_ROOT) not in sys.path:
    sys.path.insert(0, str(BDD_ROOT))

# The mock-gmail package lives outside the BDD venv; add its src dir
# to sys.path so `from mock_gmail.server import ...` works.
_MOCK_GMAIL_SRC = BDD_ROOT / "mock-gmail" / "src"
if str(_MOCK_GMAIL_SRC) not in sys.path:
    sys.path.insert(0, str(_MOCK_GMAIL_SRC))

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

# Host-port mapping per docker-compose.yml (plus in-process mock-gmail).
IMAP_INSTANCES: dict[str, tuple[str, int]] = {
    "imap-a": ("127.0.0.1", 11143),
    "imap-b": ("127.0.0.1", 12143),
    "mock-gmail": ("127.0.0.1", 13143),
}

READINESS_TIMEOUT_SECONDS = 30


def before_all(context: Context) -> None:
    """Start the shared dovecot fixture once per suite."""
    _compose("down", "-v", check=False)
    _compose("up", "-d")
    _wait_for_imap_ready()
    _wait_for_oauth_ready()

    # Start the in-process Gmail mock IMAP server.
    from mock_gmail.server import start_gmail_mock
    from mock_gmail.state import GmailState

    gmail_state = GmailState()

    def _run_gmail_mock(state: GmailState, port: int) -> None:
        import asyncio

        async def _serve() -> None:
            server, actual_port = await start_gmail_mock(state, port=port)
            async with server:
                await server.serve_forever()

        asyncio.run(_serve())

    gmail_thread = threading.Thread(
        target=_run_gmail_mock, args=(gmail_state, 13143), daemon=True
    )
    gmail_thread.start()
    _wait_for_port("mock-gmail", "127.0.0.1", 13143)
    context.gmail_state = gmail_state

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

    gmail_state = getattr(context, "gmail_state", None)
    if gmail_state is not None:
        gmail_state.reset()

    context.mcp: MCPClient | None = None  # step files create it lazily

    # Wipe per-scenario response state. The list grows on every tool
    # call via _store_result so that multi-call assertions
    # ("both responses report …") can compare the last two entries.
    context.last_response = None
    context.last_rpc_error = None
    context.response_history = []

    # Attachment-sink scenario state (ADR 0028). The sink directory is
    # outside scratch_dir so a `chmod 0o500` does not also lock down
    # the rest of the scenario's writable scratch.
    context.attachment_sink_dir = None
    context.attachment_sink_state = "unset"  # unset | configured | broken


def after_scenario(context: Context, scenario: Scenario) -> None:
    """Terminate the MCP server and remove scratch state."""
    mcp = getattr(context, "mcp", None)
    if mcp is not None:
        mcp.close()
        context.mcp = None
    mcp_http = getattr(context, "mcp_http", None)
    if mcp_http is not None:
        mcp_http.close()
        context.mcp_http = None
    procs = getattr(context, "imap_proxy_procs", None) or {}
    for proxy_proc in list(procs.values()):
        try:
            proxy_proc.terminate()
            proxy_proc.wait(timeout=2)
        except Exception:
            try:
                proxy_proc.kill()
            except Exception:
                pass
    if hasattr(context, "imap_proxy_procs"):
        context.imap_proxy_procs = {}
    if hasattr(context, "imap_proxy_ports"):
        context.imap_proxy_ports = {}
    scratch = getattr(context, "scratch_dir", None)
    if scratch is not None and scratch.exists():
        shutil.rmtree(scratch, ignore_errors=True)
    sink = getattr(context, "attachment_sink_dir", None)
    if sink is not None and Path(sink).exists():
        # Restore writability in case a scenario chmod'd the dir
        # read-only so rmtree can descend into it.
        try:
            Path(sink).chmod(0o755)
        except OSError:
            pass
        shutil.rmtree(sink, ignore_errors=True)


# --------------------------------------------------------------------- helpers


def _compose(*args: str, check: bool = True) -> None:
    """Invoke `docker compose` from the BDD docker directory."""
    cmd = ["docker", "compose", *args]
    subprocess.run(cmd, cwd=DOCKER_DIR, check=check, capture_output=True)


# Instances started by docker compose (not in-process mocks).
_DOCKER_IMAP_INSTANCES = {"imap-a", "imap-b"}


def _wait_for_imap_ready() -> None:
    """Block until both dovecot instances accept IMAP LOGIN."""
    deadline = time.monotonic() + READINESS_TIMEOUT_SECONDS
    for name, (host, port) in IMAP_INSTANCES.items():
        if name not in _DOCKER_IMAP_INSTANCES:
            continue
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

def _wait_for_port(name: str, host: str, port: int) -> None:
    """Block until a TCP connection to (host, port) succeeds."""
    deadline = time.monotonic() + READINESS_TIMEOUT_SECONDS
    while True:
        if time.monotonic() > deadline:
            raise RuntimeError(
                f"{name} at {host}:{port} not ready within "
                f"{READINESS_TIMEOUT_SECONDS}s"
            )
        try:
            with socket.create_connection((host, port), timeout=1.0):
                break
        except (OSError, socket.timeout):
            pass
        time.sleep(0.3)


def _wait_for_oauth_ready() -> None:
    """Block until the mock-oauth2-server discovery endpoint answers."""
    deadline = time.monotonic() + READINESS_TIMEOUT_SECONDS
    url = "http://127.0.0.1:19080/default/.well-known/openid-configuration"
    while True:
        if time.monotonic() > deadline:
            raise RuntimeError(
                f"OAuth mock at {url} not ready within {READINESS_TIMEOUT_SECONDS}s"
            )
        try:
            req = urllib.request.Request(url)
            with urllib.request.urlopen(req, timeout=1.0) as response:
                if response.status == 200:
                    break
        except (urllib.error.URLError, socket.timeout, ConnectionError):
            pass
        time.sleep(0.5)
