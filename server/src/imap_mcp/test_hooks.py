"""Centralised test-hook configuration and closed boundary (ADR 0023, ADR 0058).

Restricts all test-only fault, clock, pause, and recovery hooks to a closed
inventory that is completely inert without IMAP_MCP_TEST_MODE=1 and causes
fatal server startup refusal if any test-hook environment variable is set
when IMAP_MCP_TEST_MODE != "1".
"""

from __future__ import annotations

import logging
import os
import sys
from dataclasses import dataclass

_log = logging.getLogger(__name__)

TEST_HOOK_ENV_VARS = (
    "IMAP_MCP_CRASH_AT",
    "IMAP_MCP_FAKE_NOW_UTC",
    "IMAP_MCP_FAKE_NOW_FILE",
    "IMAP_MCP_SAGA_PAUSE_MARKER",
    "IMAP_MCP_SAGA_RESUME_FILE",
    "IMAP_MCP_TEST_INJECT_INTERNAL_ERROR",
)


def enforce_test_mode_startup_gate() -> None:
    """Enforce fatal startup refusal if any test hook is set without IMAP_MCP_TEST_MODE=1."""
    test_mode = os.environ.get("IMAP_MCP_TEST_MODE") == "1"
    if test_mode:
        return
    for var in TEST_HOOK_ENV_VARS:
        val = os.environ.get(var)
        if val is not None and val.strip() != "":
            msg = (
                f"FATAL: Test hook environment variable {var!r} is set ({val!r}), "
                f"but IMAP_MCP_TEST_MODE != '1'. Refusing server startup (ADR 0058)."
            )
            _log.critical(msg)
            print(msg, file=sys.stderr)
            sys.exit(1)


@dataclass(frozen=True)
class TestHooks:
    test_mode: bool = False
    fake_now_utc: str | None = None
    fake_now_file: str | None = None
    saga_crash_at: str | None = None
    saga_pause_marker: str | None = None
    saga_resume_file: str | None = None
    test_inject_internal_error: str | None = None

    # Secondary test helpers retained for BDD/mocks under active test_mode
    oauth_inject_error: str | None = None
    oauth_token_lifetime_override: int | None = None
    tamper_pkce: bool = False
    append_timeout_override: int | None = None
    saga_pause_at: str | None = None

    @classmethod
    def from_environment(cls) -> TestHooks:
        enforce_test_mode_startup_gate()
        test_mode = os.environ.get("IMAP_MCP_TEST_MODE") == "1"
        if not test_mode:
            return TestHooks(test_mode=False)

        def _int_or_none(value: str | None) -> int | None:
            return int(value) if value else None

        return TestHooks(
            test_mode=True,
            fake_now_utc=os.environ.get("IMAP_MCP_FAKE_NOW_UTC") or None,
            fake_now_file=os.environ.get("IMAP_MCP_FAKE_NOW_FILE") or None,
            saga_crash_at=os.environ.get("IMAP_MCP_CRASH_AT") or None,
            saga_pause_marker=os.environ.get("IMAP_MCP_SAGA_PAUSE_MARKER") or None,
            saga_resume_file=os.environ.get("IMAP_MCP_SAGA_RESUME_FILE") or None,
            test_inject_internal_error=os.environ.get("IMAP_MCP_TEST_INJECT_INTERNAL_ERROR")
            or None,
            oauth_inject_error=os.environ.get("IMAP_MCP_TEST_OAUTH_INJECT_ERROR") or None,
            oauth_token_lifetime_override=_int_or_none(
                os.environ.get("IMAP_MCP_TEST_TOKEN_LIFETIME")
            ),
            tamper_pkce=bool(os.environ.get("IMAP_MCP_TEST_TAMPER_PKCE")),
            append_timeout_override=_int_or_none(os.environ.get("IMAP_MCP_APPEND_TIMEOUT")),
            saga_pause_at=os.environ.get("IMAP_MCP_SAGA_PAUSE_AT") or None,
        )


_GLOBAL_HOOKS: TestHooks = TestHooks()


def get_global_hooks() -> TestHooks:
    return _GLOBAL_HOOKS


def set_global_hooks(hooks: TestHooks) -> None:
    global _GLOBAL_HOOKS
    _GLOBAL_HOOKS = hooks
