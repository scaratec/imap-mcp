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
from typing import TYPE_CHECKING

from .audit import AuditWriter
from .config import Account, Configuration, load_configuration
from .imap_core import configure_connection_pools, set_mutf7_audit_callback
from .policy import PolicyDecisionPoint
from .pool import PoolSettings
from .saga import SagaManager
from .secrets import build_secret_store, resolve_provenance_hmac_key
from .test_hooks import TestHooks, set_global_hooks
from .wal import WAL

if TYPE_CHECKING:
    from .auth.oauth_manager import OAuthManager
    from .secrets import SecretStore

_CURRENT_CALLER_ID: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "imap_mcp_current_caller_id", default=None
)
_CURRENT_CALLER_ADDR: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "imap_mcp_current_caller_addr", default=None
)
_CURRENT_REQUEST_ID: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "imap_mcp_current_request_id", default=None
)


def current_request_id() -> str | None:
    return _CURRENT_REQUEST_ID.get()


def set_current_request_id(req_id: str | None) -> contextvars.Token[str | None]:
    return _CURRENT_REQUEST_ID.set(req_id)


@dataclass
class _LiveState:
    pdp: PolicyDecisionPoint
    configuration: Configuration
    oauth_manager: OAuthManager | None = None


@dataclass(frozen=True)
class ServerContext:
    default_caller_id: str
    _live: _LiveState
    secret_store: SecretStore
    provenance_hmac_key: bytes
    audit: AuditWriter | None = None
    saga: SagaManager | None = None
    test_hooks: TestHooks = field(default_factory=TestHooks)
    attachment_sink_directory: Path | None = None
    session_uidvalidity: dict[tuple[str, str], int] = field(default_factory=dict)

    @property
    def caller_id(self) -> str:
        override = _CURRENT_CALLER_ID.get()
        return override if override is not None else self.default_caller_id

    @property
    def caller_addr(self) -> str:
        override = _CURRENT_CALLER_ADDR.get()
        return override if override is not None else f"stdio:pid={os.getpid()}"

    @property
    def pdp(self) -> PolicyDecisionPoint:
        return self._live.pdp

    @property
    def configuration(self) -> Configuration:
        return self._live.configuration

    @property
    def oauth_manager(self) -> OAuthManager:
        if self._live.oauth_manager is None:
            raise RuntimeError("OAuthManager not initialized")
        return self._live.oauth_manager

    @property
    def request_id(self) -> str | None:
        return _CURRENT_REQUEST_ID.get()

    def account_by_id(self, account_id: str) -> Account | None:
        for account in self.configuration.accounts_file.accounts:
            if account.id == account_id:
                return account
        return None


def _package_version() -> str:
    import re
    from pathlib import Path

    pyproject = Path(__file__).resolve().parents[2] / "pyproject.toml"
    if pyproject.is_file():
        m = re.search(r'^version\s*=\s*"([^"]+)"', pyproject.read_text(), re.MULTILINE)
        if m:
            return m.group(1)
    from importlib.metadata import PackageNotFoundError, version

    try:
        return version("sc-imap-mcp")
    except PackageNotFoundError:
        pass
    return "3.0.0"


def _build_context(config_dir: Path, default_caller_id: str) -> tuple[ServerContext, Configuration]:
    test_hooks = TestHooks.from_environment()
    set_global_hooks(test_hooks)
    try:
        pool_settings = PoolSettings.from_environment()
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    configure_connection_pools(pool_settings)

    try:
        configuration = load_configuration(config_dir)
    except (ValueError, FileNotFoundError) as exc:
        raise SystemExit(str(exc)) from exc
    pdp = PolicyDecisionPoint(configuration)
    store_cfg = configuration.accounts_file.secret_store
    if store_cfg is None:
        raise SystemExit(
            "accounts.yaml must declare `secret_store:` before the server can resolve account credentials."
        )
    secret_store = build_secret_store(
        store_cfg.backend,
        Path(store_cfg.path) if store_cfg.path else None,
        recipient=store_cfg.recipient,
        gnupghome=Path(store_cfg.gnupghome) if store_cfg.gnupghome else None,
        acknowledge_plaintext=store_cfg.acknowledge_plaintext,
        passphrase=store_cfg.passphrase,
    )

    provenance_key = resolve_provenance_hmac_key(secret_store)

    audit_cfg = configuration.accounts_file.audit
    audit_writer: AuditWriter | None = None
    if audit_cfg is not None and audit_cfg.directory:
        audit_writer = AuditWriter(
            directory=Path(audit_cfg.directory),
            hot_days=audit_cfg.hot_days,
            warm_days=audit_cfg.warm_days,
            delete_after_days=audit_cfg.delete_after_days,
            external_root_hook=audit_cfg.external_root_hook,
            hmac_key=provenance_key,
        )

        def _on_mutf7_fallback() -> None:
            if audit_writer is not None:
                audit_writer.write(
                    {
                        "caller_id": default_caller_id,
                        "tool": "mailbox_name_decode",
                        "decision": "ALLOW",
                        "reason": "mutf7_decode_fallback",
                        "result": "OK",
                    }
                )

        set_mutf7_audit_callback(_on_mutf7_fallback)

    wal_cfg = configuration.accounts_file.wal
    wal: WAL | None = None
    saga_mgr: SagaManager | None = None
    if wal_cfg is not None and wal_cfg.path:
        wal = WAL(path=Path(wal_cfg.path))
        retry_limit_env = os.environ.get("IMAP_MCP_RETRY_LIMIT")
        retry_limit = int(retry_limit_env) if retry_limit_env else 3
        saga_mgr = SagaManager(
            wal=wal, audit_emitter=audit_writer, retry_limit=retry_limit, test_hooks=test_hooks
        )

    from .auth.oauth_manager import OAuthManager
    from .imap_core import set_oauth_token_refresher

    oauth_manager = OAuthManager(
        configuration, secret_store, wal=wal, test_hooks=test_hooks, audit=audit_writer
    )
    set_oauth_token_refresher(lambda acc: oauth_manager.get_access_token(acc, force_refresh=True))

    sink_cfg = configuration.accounts_file.attachment_sink
    sink_dir = Path(sink_cfg.directory) if sink_cfg is not None and sink_cfg.directory else None

    live = _LiveState(pdp=pdp, configuration=configuration, oauth_manager=oauth_manager)
    context = ServerContext(
        default_caller_id=default_caller_id,
        _live=live,
        secret_store=secret_store,
        provenance_hmac_key=provenance_key,
        audit=audit_writer,
        saga=saga_mgr,
        test_hooks=test_hooks,
        attachment_sink_directory=sink_dir,
    )

    if saga_mgr is not None:

        async def _resolve_account_for_saga(acc_id: str) -> tuple[Account, str]:
            from .handlers._common import _password_for

            return await _password_for(context, acc_id)

        async def _resolve_folder_for_saga(acc_id: str, fld: str) -> str:
            from .handlers._common import _resolve_imap_folder

            return await _resolve_imap_folder(context, acc_id, fld)

        def _check_saga_permission(
            caller: str, src_a: str, src_f: str, dst_a: str, dst_f: str
        ) -> bool:
            src_dec = context.pdp.decide_folder_access(caller, src_a, src_f)
            if (
                not src_dec.allowed
                or not src_dec.folder_policy
                or not src_dec.folder_policy.move_out
            ):
                return False
            dst_dec = context.pdp.decide_folder_access(caller, dst_a, dst_f)
            return bool(
                dst_dec.allowed and dst_dec.folder_policy and dst_dec.folder_policy.accept_incoming
            )

        saga_mgr.account_resolver = _resolve_account_for_saga
        saga_mgr.folder_resolver = _resolve_folder_for_saga
        saga_mgr.permission_checker = _check_saga_permission
    if saga_mgr is not None:

        async def _resolver(account_id: str) -> tuple[Account, str]:
            from .handlers._common import _password_for

            return await _password_for(context, account_id)

        async def _folder_resolver(account_id: str, canonical_path: str) -> str:
            from .handlers._common import _resolve_imap_folder

            return await _resolve_imap_folder(context, account_id, canonical_path)

        saga_mgr.account_resolver = _resolver
        saga_mgr.folder_resolver = _folder_resolver

        def _check_perms(
            caller_id: str, src_acc: str, src_f: str, dst_acc: str, dst_f: str
        ) -> bool:
            src_dec = context.pdp.decide_folder_access(caller_id, src_acc, src_f)
            dst_dec = context.pdp.decide_folder_access(caller_id, dst_acc, dst_f)
            if not src_dec.allowed or not dst_dec.allowed:
                return False
            if not src_dec.folder_policy or not src_dec.folder_policy.move_out:
                return False
            return bool(dst_dec.folder_policy and dst_dec.folder_policy.accept_incoming)

        saga_mgr.permission_checker = _check_perms
    return context, configuration
