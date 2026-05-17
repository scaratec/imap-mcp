"""Centralised test-hook configuration (ADR 0023).

The previous scatter of ``os.environ.get("IMAP_MCP_TEST_*")`` reads
across runtime code is replaced by a single ``TestHooks`` dataclass
that is populated once at startup. Modules that have access to a
``ServerContext`` read ``context.test_hooks``; deeply-nested helpers
that do not receive a context (``audit._now_utc``, ``saga._maybe_*``)
read the process-global singleton via ``get_global_hooks()``.

Production deployments leave every field at its default. The BDD
harness sets ``IMAP_MCP_TEST_*`` env vars before spawning the server
process; ``TestHooks.from_environment()`` reads them once on startup.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field, fields
from datetime import datetime
from typing import Optional


@dataclass(frozen=True)
class TestHooks:
    """All test-only configuration in one place. Frozen so callers
    cannot mutate the live hooks; ``set_global_hooks`` replaces the
    process-global reference atomically when tests need to override.
    """

    test_mode: bool = False
    """Gates discovery of the ``_test_run_*`` tools (ADR 0023).
    Bound to ``IMAP_MCP_TEST_MODE=1``."""

    fake_now_utc: Optional[str] = None
    """ISO-8601 UTC timestamp that ``audit._now_utc`` returns instead
    of the real wall clock. Bound to ``IMAP_MCP_FAKE_NOW_UTC``."""

    oauth_inject_error: Optional[str] = None
    """If set, the OAuth refresh flow raises with this error code
    before talking to the token endpoint. Bound to
    ``IMAP_MCP_TEST_OAUTH_INJECT_ERROR``."""

    oauth_token_lifetime_override: Optional[int] = None
    """Seconds. Overrides ``expires_in`` returned by the token
    endpoint. Bound to ``IMAP_MCP_TEST_TOKEN_LIFETIME``."""

    tamper_pkce: bool = False
    """If true, the PKCE challenge is computed over a fixed wrong
    string so the server rejects the bootstrap. Bound to
    ``IMAP_MCP_TEST_TAMPER_PKCE``."""

    append_timeout_override: Optional[int] = None
    """Seconds. Overrides the default 60s append timeout. Bound to
    ``IMAP_MCP_APPEND_TIMEOUT``."""

    saga_crash_at: Optional[str] = None
    """Saga step name. When the named step is about to execute, the
    saga process terminates with ``os._exit(1)``. Bound to
    ``IMAP_MCP_CRASH_AT``."""

    saga_pause_at: Optional[str] = None
    """Saga step name. When the named step is about to execute, the
    saga writes a marker file and polls for a ``.resume`` sibling
    before continuing. Bound to ``IMAP_MCP_SAGA_PAUSE_AT``."""

    saga_pause_marker: Optional[str] = None
    """Path to the marker file used together with ``saga_pause_at``.
    Bound to ``IMAP_MCP_SAGA_PAUSE_MARKER``."""

    @classmethod
    def from_environment(cls) -> "TestHooks":
        """Read every IMAP_MCP_TEST_* env var once at startup."""

        def _int_or_none(value: str | None) -> int | None:
            return int(value) if value else None

        return cls(
            test_mode=os.environ.get("IMAP_MCP_TEST_MODE") == "1",
            fake_now_utc=os.environ.get("IMAP_MCP_FAKE_NOW_UTC") or None,
            oauth_inject_error=os.environ.get("IMAP_MCP_TEST_OAUTH_INJECT_ERROR") or None,
            oauth_token_lifetime_override=_int_or_none(
                os.environ.get("IMAP_MCP_TEST_TOKEN_LIFETIME")
            ),
            tamper_pkce=bool(os.environ.get("IMAP_MCP_TEST_TAMPER_PKCE")),
            append_timeout_override=_int_or_none(os.environ.get("IMAP_MCP_APPEND_TIMEOUT")),
            saga_crash_at=os.environ.get("IMAP_MCP_CRASH_AT") or None,
            saga_pause_at=os.environ.get("IMAP_MCP_SAGA_PAUSE_AT") or None,
            saga_pause_marker=os.environ.get("IMAP_MCP_SAGA_PAUSE_MARKER") or None,
        )


# Process-global singleton. Set once at server startup by
# context._build_context(); deeply-nested helpers (audit, saga,
# imap_core) read it instead of reaching into os.environ themselves.
_GLOBAL_HOOKS: TestHooks = TestHooks()


def get_global_hooks() -> TestHooks:
    """Return the active process-global TestHooks instance.

    Defaults to an all-disabled TestHooks() until the server startup
    path calls set_global_hooks().
    """
    return _GLOBAL_HOOKS


def set_global_hooks(hooks: TestHooks) -> None:
    """Replace the process-global TestHooks. Called once from
    ``_build_context`` after parsing the environment. Tests that
    bypass the normal startup path may call this directly to scope
    behaviour for a single scenario.
    """
    global _GLOBAL_HOOKS
    _GLOBAL_HOOKS = hooks
