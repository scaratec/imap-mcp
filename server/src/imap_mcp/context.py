"""ServerContext and live-state plumbing.

Owns the request-scoped caller ContextVar, the SIGHUP-swappable
`_LiveState`, and the `ServerContext` value that handlers receive.
`_build_context` assembles a context from a config directory and is
shared by `runtime.stdio.run_stdio` and `runtime.http.run_http`.
"""

from __future__ import annotations

import contextvars
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, TYPE_CHECKING

from .audit import AuditWriter
from .config import load_configuration
from .policy import PolicyDecisionPoint
from .saga import SagaManager
from .secrets import build_secret_store
from .test_hooks import TestHooks, set_global_hooks
from .wal import WAL

if TYPE_CHECKING:
    pass


# Per-request override for the caller identity. The HTTP transport
# (ADR 0015 + LIM-0007 paydown) sets this in the bearer-auth middleware
# so that each request runs against the caller derived from its
# Authorization header. The stdio transport leaves it unset and falls
# back to the static `default_caller_id` resolved at startup.
_CURRENT_CALLER_ID: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "imap_mcp_current_caller_id", default=None
)


@dataclass
class _LiveState:
    """Mutable holder for state that SIGHUP swaps atomically (ADR 0014)."""

    pdp: PolicyDecisionPoint
    configuration: "object"  # Configuration; intentionally untyped here
    oauth_manager: "object | None" = None
    folder_aliases: dict[str, dict[str, str]] | None = None


@dataclass(frozen=True)
class ServerContext:
    default_caller_id: str
    _live: _LiveState
    secret_store: "object"  # SecretStore protocol
    audit: "AuditWriter | None" = None
    saga: "SagaManager | None" = None
    test_hooks: TestHooks = field(default_factory=TestHooks)
    attachment_sink_directory: "Path | None" = None

    @property
    def caller_id(self) -> str:
        """Caller identity for the in-flight request.

        On HTTP transport the bearer-auth middleware sets a per-request
        ContextVar; on stdio it remains unset and the constructor-time
        default applies.
        """
        override = _CURRENT_CALLER_ID.get()
        return override if override is not None else self.default_caller_id

    @property
    def pdp(self) -> PolicyDecisionPoint:
        """Live PDP. Replaced atomically by SIGHUP reload."""
        return self._live.pdp

    @property
    def configuration(self):  # type: ignore[no-untyped-def]
        """Live configuration. Replaced atomically by SIGHUP reload."""
        return self._live.configuration

    @property
    def oauth_manager(self):  # type: ignore[no-untyped-def]
        if self._live.oauth_manager is None:
            raise RuntimeError("OAuthManager not initialized")
        return self._live.oauth_manager

    def account_by_id(self, account_id: str) -> "object | None":
        from .config import Configuration

        config: Configuration = self.configuration  # type: ignore[assignment]
        for account in config.accounts_file.accounts:
            if account.id == account_id:
                return account
        return None


def _package_version() -> str:
    from importlib.metadata import version

    try:
        return version("sc-imap-mcp")
    except Exception:
        pass
    import re
    from pathlib import Path

    pyproject = Path(__file__).resolve().parents[2] / "pyproject.toml"
    if pyproject.is_file():
        m = re.search(r'^version\s*=\s*"([^"]+)"', pyproject.read_text(), re.MULTILINE)
        if m:
            return m.group(1)
    return "0.0.0-dev"


def _build_context(config_dir: Path, default_caller_id: str) -> tuple[ServerContext, object]:
    """Load configuration and assemble a ServerContext.

    Shared by `run_stdio` and `run_http`. The transport-specific code
    around it is the only thing that differs.
    """
    from .config import Configuration

    test_hooks = TestHooks.from_environment()
    set_global_hooks(test_hooks)

    configuration: Configuration = load_configuration(config_dir)
    pdp = PolicyDecisionPoint(configuration)
    store_cfg = configuration.accounts_file.secret_store
    if store_cfg is None:
        raise SystemExit(
            "accounts.yaml must declare `secret_store:` before the server "
            "can resolve account credentials."
        )
    secret_store = build_secret_store(
        store_cfg.backend,
        Path(store_cfg.path) if store_cfg.path else None,
        recipient=store_cfg.recipient,
        gnupghome=Path(store_cfg.gnupghome) if store_cfg.gnupghome else None,
    )
    audit_cfg = configuration.accounts_file.audit
    audit_writer: AuditWriter | None = None
    if audit_cfg is not None and audit_cfg.directory:
        audit_writer = AuditWriter(
            directory=Path(audit_cfg.directory),
            hot_days=audit_cfg.hot_days,
            warm_days=audit_cfg.warm_days,
            delete_after_days=audit_cfg.delete_after_days,
            external_root_hook=audit_cfg.external_root_hook,
        )
    wal_cfg = configuration.accounts_file.wal
    saga_mgr: SagaManager | None = None
    if wal_cfg is not None and wal_cfg.path:
        wal = WAL(path=Path(wal_cfg.path))
        retry_limit_env = os.environ.get("IMAP_MCP_RETRY_LIMIT")
        retry_limit = int(retry_limit_env) if retry_limit_env else 3
        saga_mgr = SagaManager(
            wal=wal, audit_emitter=audit_writer, retry_limit=retry_limit, test_hooks=test_hooks
        )

    from .auth.oauth_manager import OAuthManager

    oauth_manager = OAuthManager(configuration, secret_store, test_hooks=test_hooks)

    sink_cfg = configuration.accounts_file.attachment_sink
    sink_dir = Path(sink_cfg.directory) if sink_cfg is not None and sink_cfg.directory else None

    live = _LiveState(pdp=pdp, configuration=configuration, oauth_manager=oauth_manager)
    context = ServerContext(
        default_caller_id=default_caller_id,
        _live=live,
        secret_store=secret_store,
        audit=audit_writer,
        saga=saga_mgr,
        test_hooks=test_hooks,
        attachment_sink_directory=sink_dir,
    )
    if saga_mgr is not None:

        async def _resolver(account_id: str) -> tuple[Any, str]:
            from .handlers._common import _password_for

            return await _password_for(context, account_id)

        saga_mgr.account_resolver = _resolver
    return context, configuration
