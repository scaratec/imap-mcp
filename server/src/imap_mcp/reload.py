"""SIGHUP-driven configuration reload (ADR 0014, ADR 0049, ADR 0055, ADR 0059).

Splits two concerns: `_reload_configuration` re-parses YAML and
atomically swaps the live PDP + Configuration; `_install_sighup_handler`
wires SIGHUP to it on the running event loop.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from .config import load_configuration
from .context import ServerContext
from .policy import PolicyDecisionPoint


def _reload_configuration(context: ServerContext, config_dir: Path) -> None:
    from .config import Configuration

    audit = context.audit
    old_config: Configuration = context.configuration
    try:
        new_config: Configuration = load_configuration(config_dir)
    except Exception as exc:  # noqa: BLE001 - keep old config on any error
        if audit is not None:
            audit.write(
                {
                    "phase": "single",
                    "caller_id": context.caller_id,
                    "tool": "policy_reload",
                    "decision": "DENY",
                    "reason": "policy_reload_rejected",
                    "result": "ERROR",
                    "detail": str(exc),
                }
            )
        return

    new_pdp = PolicyDecisionPoint(new_config)
    context._live.pdp = new_pdp
    context._live.configuration = new_config
    if hasattr(context, "oauth_manager") and context.oauth_manager is not None:
        context.oauth_manager.config = new_config

    # Detect oauth_scope changes -> needs_rebootstrap (ADR 0049 §3, ADR 0059)
    old_scopes = {
        a.id: (a.auth.oauth_scope if a.auth else None) for a in old_config.accounts_file.accounts
    }
    for new_acct in new_config.accounts_file.accounts:
        new_scope = new_acct.auth.oauth_scope if new_acct.auth else None
        old_scope = old_scopes.get(new_acct.id)
        if old_scope is not None and new_scope != old_scope:
            context.oauth_manager.mark_rebootstrap_needed(new_acct.id)

    # Drain pools for removed or changed accounts (ADR 0055)
    from .imap_core import drain_connection_pool

    new_accounts = {a.id: a for a in new_config.accounts_file.accounts}
    for old_acct in old_config.accounts_file.accounts:
        changed = new_accounts.get(old_acct.id)
        if changed is None or (
            (changed.host, changed.port, changed.user, changed.auth)
            != (old_acct.host, old_acct.port, old_acct.user, old_acct.auth)
        ):
            drain_connection_pool(old_acct.id)

    if audit is not None:
        old_ids = {a.id for a in old_config.accounts_file.accounts}
        new_ids = {a.id for a in new_config.accounts_file.accounts}
        for removed in sorted(old_ids - new_ids):
            audit.write(
                {
                    "phase": "single",
                    "caller_id": context.caller_id,
                    "tool": "pool_drain",
                    "decision": "ALLOW",
                    "reason": "pool_drain",
                    "account": removed,
                    "result": "OK",
                }
            )
        audit.write(
            {
                "phase": "single",
                "caller_id": context.caller_id,
                "tool": "policy_reload",
                "decision": "ALLOW",
                "reason": "policy_reload_ok",
                "result": "OK",
            }
        )


def _install_sighup_handler(context: ServerContext, config_dir: Path) -> None:
    import signal

    loop = asyncio.get_running_loop()
    try:
        loop.add_signal_handler(signal.SIGHUP, lambda: _reload_configuration(context, config_dir))
    except (NotImplementedError, RuntimeError):
        pass
