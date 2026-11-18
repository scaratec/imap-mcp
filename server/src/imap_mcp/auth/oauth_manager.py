"""OAuth token lifecycle management (ADR 0050, ADR 0059, ADR 0064).

Manages runtime OAuth2 access tokens via memory_only / persist_all caching,
proactive renewal at 80% of token lifetime with connection-pool drain,
single reactive refresh-and-retry on stale cached XOAUTH2 rejection,
and durable account_health persistence in wal.db across restarts and SIGHUP reloads.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import pathlib
import time
from datetime import datetime
from typing import TYPE_CHECKING, Any

import httpx

from ..audit import AuditWriter
from ..config import Account, Configuration
from ..secrets import SecretStore
from ..test_hooks import TestHooks

if TYPE_CHECKING:
    from ..wal import WAL

logger = logging.getLogger(__name__)


class NeedsRebootstrapError(RuntimeError):
    """Account credentials require human re-bootstrap."""


class OAuthManager:
    def __init__(
        self,
        config: Configuration,
        secret_store: SecretStore,
        wal: WAL | None = None,
        test_hooks: TestHooks | None = None,
        audit: AuditWriter | None = None,
    ):
        self.config = config
        self.secret_store = secret_store
        self.wal = wal
        self._test_hooks = test_hooks or TestHooks()
        self._audit = audit
        self._tokens: dict[str, dict[str, Any]] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self._needs_rebootstrap: dict[str, bool] = {}
        self._http_client: httpx.AsyncClient = httpx.AsyncClient()

    async def aclose(self) -> None:
        try:
            await self._http_client.aclose()
        except Exception:  # noqa: BLE001, S110
            pass

    def _refresh_token_sha256(self, account: Account) -> str | None:
        if not account.auth or not account.auth.secret_ref:
            return None
        try:
            val = self.secret_store.get(account.auth.secret_ref)
            if val is not None:
                return hashlib.sha256(val.encode("utf-8")).hexdigest()
        except Exception:  # noqa: BLE001
            return None
        return None

    def is_rebootstrap_needed(self, account_id: str) -> bool:
        if self.wal is not None:
            health = self.wal.get_account_health(account_id)
            if health is None:
                self._needs_rebootstrap[account_id] = False
                return False
            state, stored_sha = health
            if state == "needs_rebootstrap":
                account = next(
                    (a for a in self.config.accounts_file.accounts if a.id == account_id), None
                )
                if account is not None:
                    current_sha = self._refresh_token_sha256(account)
                    if stored_sha is not None and current_sha is not None:
                        if stored_sha == current_sha:
                            self._needs_rebootstrap[account_id] = True
                            return True
                        # Secret rotated! Clear durable state
                        self.wal.clear_account_health(account_id)
                        self._needs_rebootstrap[account_id] = False
                        return False
                self._needs_rebootstrap[account_id] = True
                return True
            return False
        return bool(self._needs_rebootstrap.get(account_id, False))

    def mark_rebootstrap_needed(self, account_id: str) -> None:
        self._needs_rebootstrap[account_id] = True
        account = next((a for a in self.config.accounts_file.accounts if a.id == account_id), None)
        secret_sha = self._refresh_token_sha256(account) if account else None
        if self.wal is not None:
            self.wal.set_account_health(account_id, "needs_rebootstrap", secret_sha)
        self._log_audit(account_id, "DENY", "token_refresh_failed")

    def _mark_rebootstrap_needed(self, account_id: str) -> None:
        self.mark_rebootstrap_needed(account_id)

    def _now(self) -> float:
        if self._test_hooks.fake_now_file:
            try:
                raw = (
                    pathlib.Path(self._test_hooks.fake_now_file).read_text(encoding="utf-8").strip()
                )
                if raw.endswith("Z"):
                    raw = raw[:-1] + "+00:00"
                return datetime.fromisoformat(raw).timestamp()
            except (OSError, ValueError):
                pass
        if self._test_hooks.fake_now_utc:
            try:
                raw = self._test_hooks.fake_now_utc.strip()
                if raw.endswith("Z"):
                    raw = raw[:-1] + "+00:00"
                return datetime.fromisoformat(raw).timestamp()
            except ValueError:
                pass
        return time.time()

    def invalidate_cached_token(self, account_id: str) -> None:
        self._tokens.pop(account_id, None)

    async def get_access_token(self, account: Account, *, force_refresh: bool = False) -> str:
        if self.is_rebootstrap_needed(account.id):
            raise NeedsRebootstrapError(f"Account {account.id!r} requires OAuth rebootstrap")

        if account.id not in self._locks:
            self._locks[account.id] = asyncio.Lock()

        async with self._locks[account.id]:
            cached = self._tokens.get(account.id)
            now = self._now()
            if not force_refresh:
                if not cached and account.token_cache == "persist_all":
                    persisted = self._read_persisted_access_token(account)
                    if persisted:
                        token, mtime = persisted
                        cached = {
                            "access_token": token,
                            "issued_at": mtime,
                            "expires_in": 3600,
                            "expires_at": mtime + 3600,
                        }
                        self._tokens[account.id] = cached
                if cached:
                    issued_at = cached.get("issued_at", 0)
                    expires_in = cached.get("expires_in", 3600)
                    # Proactive 80% lifetime renewal (ADR 0064)
                    if (
                        now < issued_at + (0.8 * expires_in)
                        and cached.get("expires_at", 0) > now + 10
                    ):
                        return str(cached["access_token"])
            return await self._refresh_token(account)

    async def _refresh_token(self, account: Account) -> str:
        if not account.auth or account.auth.type != "xoauth2":
            raise ValueError("Not an OAuth account")
        secret_ref = account.auth.secret_ref
        if not secret_ref:
            raise ValueError("No secret_ref for OAuth account")

        self._check_test_injection(account.id)

        try:
            refresh_token = self.secret_store.get(secret_ref)
        except Exception as exc:
            raise RuntimeError(f"Failed to read secret {secret_ref!r}: {exc}") from exc

        try:
            access_token, expires_in = await self._request_access_token(
                refresh_token, provider=account.provider
            )
        except _RefreshHTTPError as exc:
            if exc.error == "invalid_grant":
                self.mark_rebootstrap_needed(account.id)
                raise NeedsRebootstrapError(
                    f"Account {account.id!r} requires human re-bootstrap: {exc}"
                ) from exc
            self._log_audit(account.id, "DENY", "token_refresh_failed")
            raise

        if self._test_hooks.oauth_token_lifetime_override is not None:
            expires_in = self._test_hooks.oauth_token_lifetime_override

        now = self._now()
        self._tokens[account.id] = {
            "access_token": access_token,
            "issued_at": now,
            "expires_in": expires_in,
            "expires_at": now + expires_in,
        }

        # ADR 0055, ADR 0064: Drain pool on token refresh
        from ..imap_core import drain_connection_pool

        drain_connection_pool(account.id)
        if self._audit is not None:
            self._audit.write(
                {
                    "tool": "pool_drain",
                    "decision": "ALLOW",
                    "reason": "pool_drain",
                    "account": account.id,
                    "result": "OK",
                }
            )

        self._persist_access_token_if_configured(account, access_token)
        self._log_audit(account.id, "ALLOW", "token_refresh_ok")
        return access_token

    def _check_test_injection(self, account_id: str) -> None:
        injected_error = self._test_hooks.oauth_inject_error
        if not injected_error:
            return
        self._log_audit(account_id, "DENY", "token_refresh_failed")
        if injected_error == "invalid_grant":
            self.mark_rebootstrap_needed(account_id)
            raise NeedsRebootstrapError(f"Account {account_id!r} requires human re-bootstrap")
        raise RuntimeError(f"OAuth refresh failed: {injected_error}")

    async def _request_access_token(
        self, refresh_token: str | None, *, provider: str
    ) -> tuple[str, int]:
        token_uri = os.environ.get("IMAP_MCP_OAUTH_TOKEN_URI")
        if not token_uri:
            if provider == "google-mock":
                token_uri = "http://127.0.0.1:19080/default/token"
            else:
                token_uri = "https://oauth2.googleapis.com/token"
        client_id = os.environ.get("IMAP_MCP_OAUTH_CLIENT_ID", "test-client-id")
        client_secret = os.environ.get("IMAP_MCP_OAUTH_CLIENT_SECRET", "")
        resp = await self._http_client.post(
            token_uri,
            data={
                "grant_type": "refresh_token",
                "client_id": client_id,
                "client_secret": client_secret,
                "refresh_token": refresh_token,
            },
        )
        if resp.status_code != 200:
            err = resp.json().get("error", "unknown_error")
            raise _RefreshHTTPError(err)
        data = resp.json()
        access_token = data["access_token"]
        expires_in = data.get("expires_in", 3600)
        return access_token, expires_in

    def _persist_access_token_if_configured(self, account: Account, access_token: str) -> None:
        if account.token_cache != "persist_all":
            return
        if not account.auth or not account.auth.secret_ref:
            return
        store_cfg = self.config.accounts_file.secret_store
        if store_cfg is None or store_cfg.backend != "file_dir" or not store_cfg.path:
            return
        access_ref = account.auth.secret_ref.replace("refresh_token", "access_token")
        rel_path = access_ref[len("secret://") :].lstrip("/")
        target = pathlib.Path(store_cfg.path) / rel_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(access_token + "\n", encoding="utf-8")
        target.chmod(0o600)

    def _read_persisted_access_token(self, account: Account) -> tuple[str, float] | None:
        if account.token_cache != "persist_all":
            return None
        if not account.auth or not account.auth.secret_ref:
            return None
        store_cfg = self.config.accounts_file.secret_store
        if store_cfg is None or store_cfg.backend != "file_dir" or not store_cfg.path:
            return None
        access_ref = account.auth.secret_ref.replace("refresh_token", "access_token")
        rel_path = access_ref[len("secret://") :].lstrip("/")
        target = pathlib.Path(store_cfg.path) / rel_path
        if not target.is_file():
            return None
        token = target.read_text(encoding="utf-8").strip()
        if not token:
            return None
        mtime = target.stat().st_mtime
        return token, mtime

    def _log_audit(self, account_id: str, decision: str, reason_code: str) -> None:
        if self._audit is None:
            return
        entry = {
            "tool": "token_refresh",
            "decision": decision,
            "reason": reason_code,
            "account": account_id,
            "result": "OK" if decision == "ALLOW" else "ERROR",
        }
        self._audit.write(entry)


class _RefreshHTTPError(RuntimeError):
    def __init__(self, error: str) -> None:
        super().__init__(f"OAuth refresh failed: {error}")
        self.error = error
