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
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

from behave.model import Scenario
from behave.runner import Context

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

_MOCK_OAUTH_SRC = BDD_ROOT / "mock-oauth" / "src"
if str(_MOCK_OAUTH_SRC) not in sys.path:
    sys.path.insert(0, str(_MOCK_OAUTH_SRC))

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
    # --build: a changed Dovecot config or user file must reach the
    # image; with an unchanged context the layer cache makes it a no-op.
    _compose("up", "-d", "--build")
    _wait_for_imap_ready()
    _wait_for_oauth_ready()

    # Start the in-process MockOAuth control proxy.
    from mock_oauth.client import MockOAuthClient
    from mock_oauth.server import MockOAuthServer

    mock_oauth_server = MockOAuthServer(
        upstream_url="http://127.0.0.1:19080", host="127.0.0.1", port=19085
    )
    mock_oauth_server.start()
    context.mock_oauth_server = mock_oauth_server
    context.mock_oauth_client = MockOAuthClient(
        base_url="http://127.0.0.1:19085", server=mock_oauth_server
    )

    # Start the in-process Gmail mock IMAP server.
    from mock_gmail.server import start_gmail_mock
    from mock_gmail.state import GmailState

    gmail_state = GmailState()

    def _run_gmail_mock(state: GmailState, port: int) -> None:
        import asyncio

        async def _serve() -> None:
            server, _ = await start_gmail_mock(state, port=port)
            async with server:
                await server.serve_forever()

        asyncio.run(_serve())

    gmail_thread = threading.Thread(
        target=_run_gmail_mock, args=(gmail_state, 13143), daemon=True
    )
    gmail_thread.start()
    _wait_for_port("mock-gmail", "127.0.0.1", 13143)
    gmail_state.token_validator = context.mock_oauth_client.validate_bearer
    context.gmail_state = gmail_state

    context.imap_instances = IMAP_INSTANCES
    context.bdd_root = BDD_ROOT


def after_all(context: Context) -> None:
    """Tear the fixtures down completely."""
    oauth_server = getattr(context, "mock_oauth_server", None)
    if oauth_server is not None:
        oauth_server.stop()
        context.mock_oauth_server = None
    _compose("down", "-v", check=False)


def before_scenario(context: Context, scenario: Scenario) -> None:
    """Reset state so every scenario starts from a clean slate."""
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
        oauth_client = getattr(context, "mock_oauth_client", None)
        if oauth_client is not None:
            gmail_state.token_validator = oauth_client.validate_bearer

    oauth_client = getattr(context, "mock_oauth_client", None)
    if oauth_client is not None:
        oauth_client.reset()

    context.mcp: MCPClient | None = None
    context.mcp_http = None

    context.last_response = None
    context.last_rpc_error = None
    context.response_history = []
    context.captured_responses = []
    context.remembered_values = {}
    context.mcp_extra_env = {}
    context.without_test_mode = False
    context.is_test_hooks = "test_hooks" in scenario.effective_tags
    context.server_instances = {}
    context.imap_proxy_procs = {}
    context.imap_proxy_ports = {}
    context.imap_proxy_log_paths = {}

    context.attachment_sink_dir = None
    context.attachment_sink_state = "unset"


def after_scenario(context: Context, scenario: Scenario) -> None:
    """Terminate the MCP server and remove scratch state."""
    imap = getattr(context, "imap", None)
    if imap is not None:
        imap.close_all()
    clients = getattr(context, "mcp_clients", None) or {}
    for c in list(clients.values()):
        c.close()
    context.mcp_clients = {}
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
        except (OSError, subprocess.SubprocessError):
            try:
                proxy_proc.kill()
            except (OSError, subprocess.SubprocessError):
                pass
    if hasattr(context, "imap_proxy_procs"):
        context.imap_proxy_procs = {}
    if hasattr(context, "imap_proxy_ports"):
        context.imap_proxy_ports = {}
    for instance in list((getattr(context, "server_instances", None) or {}).values()):
        instance.close()
    collector = getattr(context, "trace_collector", None)
    if collector is not None:
        collector.stop()
        context.trace_collector = None
    scratch = getattr(context, "scratch_dir", None)
    if scratch is not None and scratch.exists():
        # A scenario may have stripped write permission from parts of
        # the scratch tree (audit_multi_instance.feature).
        for root, dirs, _files in os.walk(scratch):
            for name in dirs:
                try:
                    os.chmod(os.path.join(root, name), 0o700)
                except OSError:
                    pass
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
    proc = subprocess.run(cmd, cwd=DOCKER_DIR, check=False, capture_output=True)
    if check and proc.returncode != 0:
        stderr = proc.stderr.decode("utf-8", errors="replace")
        stdout = proc.stdout.decode("utf-8", errors="replace")
        raise RuntimeError(
            f"docker compose {' '.join(args)} failed (rc={proc.returncode}):\n{stderr}\n{stdout}"
        )


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
            except (TimeoutError, OSError):
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
        except (TimeoutError, OSError):
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
        except (TimeoutError, urllib.error.URLError, ConnectionError):
            pass
        time.sleep(0.5)
