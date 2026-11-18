"""Unit tests for closed test mode startup gate (ADR 0058)."""

from __future__ import annotations

import os
import subprocess
import sys

import pytest


@pytest.mark.parametrize(
    "var_name",
    [
        "IMAP_MCP_CRASH_AT",
        "IMAP_MCP_FAKE_NOW_UTC",
        "IMAP_MCP_FAKE_NOW_FILE",
        "IMAP_MCP_SAGA_PAUSE_MARKER",
        "IMAP_MCP_SAGA_RESUME_FILE",
        "IMAP_MCP_TEST_INJECT_INTERNAL_ERROR",
    ],
)
def test_server_startup_fails_if_test_hook_set_without_test_mode(
    var_name: str,
) -> None:
    env = dict(os.environ)
    env.pop("IMAP_MCP_TEST_MODE", None)
    env[var_name] = "test-value"

    code = (
        "from imap_mcp.test_hooks import enforce_test_mode_startup_gate; "
        "enforce_test_mode_startup_gate()"
    )
    result = subprocess.run(
        [sys.executable, "-c", code], env=env, capture_output=True, text=True, check=False
    )
    assert result.returncode != 0
    assert "FATAL" in result.stderr
    assert "IMAP_MCP_TEST_MODE" in result.stderr
    assert var_name in result.stderr
