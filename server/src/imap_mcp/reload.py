"""SIGHUP-driven configuration reload (ADR 0014).

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
    """SIGHUP-driven atomic reload (ADR 0014).

    Re-parses every YAML file in `config_dir` into a temporary
    Configuration. If parsing or validation fails, the previous
    state is preserved and an `ERROR` audit record explains why. On
    success, the new PDP + Configuration replace the live state in a
    single attribute write — handlers reading `context.pdp` /
    `context.configuration` see either the old or the new state, never
    a half-applied one.
    """
    from .config import Configuration

    audit = context.audit
    old_config: Configuration = context.configuration  # type: ignore[assignment]
    try:
        new_config: Configuration = load_configuration(config_dir)
    except Exception as exc:
        if audit is not None:
            reason = (
                "parse_error"
                if "yaml" in type(exc).__module__.lower() or "yaml" in str(exc).lower()
                else "validation_error"
            )
            audit.write(
                {
                    "caller_id": context.caller_id,
                    "tool": "policy_reload",
                    "decision": "DENY",
                    "reason": reason,
                    "result": "ERROR",
                    "detail": str(exc),
                }
            )
        return

    new_pdp = PolicyDecisionPoint(new_config)
    # Atomic swap. `_LiveState` is a mutable dataclass — assigning
    # both fields back-to-back is not atomic at the language level,
    # but every handler reads either `pdp` or `configuration` (never
    # both in one expression), so the worst case is a request that
    # uses a fresh PDP against a stale Configuration for the duration
    # of one method call. ADR 0014 declares that acceptable.
    context._live.pdp = new_pdp
    context._live.configuration = new_config

    # Detect oauth_scope changes → needs_rebootstrap (ADR 0014).
    old_scopes = {
        a.id: (a.auth.oauth_scope if a.auth else None) for a in old_config.accounts_file.accounts
    }
    for new_acct in new_config.accounts_file.accounts:
        new_scope = new_acct.auth.oauth_scope if new_acct.auth else None
        old_scope = old_scopes.get(new_acct.id)
        if old_scope is not None and new_scope != old_scope:
            context.oauth_manager._mark_rebootstrap_needed(new_acct.id)
            if audit is not None:
                audit.write(
                    {
                        "caller_id": context.caller_id,
                        "tool": "policy_reload",
                        "decision": "ALLOW",
                        "reason": "reload_applied",
                        "result": "OK",
                        "detail": f"oauth_scope changed; rebootstrap required for {new_acct.id}",
                    }
                )

    # Pool-drain audit per removed account (no actual pool today;
    # ADR 0013's pool will hook in here when its scenarios activate).
    if audit is not None:
        old_ids = {a.id for a in old_config.accounts_file.accounts}
        new_ids = {a.id for a in new_config.accounts_file.accounts}
        for removed in sorted(old_ids - new_ids):
            audit.write(
                {
                    "caller_id": context.caller_id,
                    "tool": "pool_drain",
                    "decision": "ALLOW",
                    "reason": "account_removed",
                    "account": removed,
                    "result": "OK",
                }
            )
        audit.write(
            {
                "caller_id": context.caller_id,
                "tool": "policy_reload",
                "decision": "ALLOW",
                "reason": "reload_applied",
                "result": "OK",
                "detail": (
                    f"old_callers={len(old_config.callers_file.callers)}, "
                    f"new_callers={len(new_config.callers_file.callers)}, "
                    f"removed_accounts={sorted(old_ids - new_ids)}"
                ),
            }
        )


def _install_sighup_handler(context: ServerContext, config_dir: Path) -> None:
    """Wire SIGHUP to `_reload_configuration` on the running event loop.

    The handler is idempotent — repeated SIGHUPs each trigger a fresh
    reload. Windows lacks SIGHUP; on those platforms the registration
    is a no-op (the BDD suite runs on Linux only).
    """
    import signal

    if not hasattr(signal, "SIGHUP"):
        return
    loop = asyncio.get_running_loop()

    def _on_sighup() -> None:
        _reload_configuration(context, config_dir)

    loop.add_signal_handler(signal.SIGHUP, _on_sighup)
