"""OAuth token lifecycle management."""

import asyncio
import os
import pathlib
import time
from datetime import datetime, timezone
from typing import Any

import httpx
import logging

import json

from ..config import Account, Configuration
from ..secrets import SecretStore
from ..test_hooks import TestHooks

logger = logging.getLogger(__name__)


class OAuthManager:
    def __init__(
        self,
        config: Configuration,
        secret_store: SecretStore,
        test_hooks: TestHooks | None = None,
    ):
        self.config = config
        self.secret_store = secret_store
        self._test_hooks = test_hooks or TestHooks()
        self._tokens: dict[str, dict[str, Any]] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self._needs_rebootstrap: dict[str, bool] = {}
        self._http_client: httpx.AsyncClient = httpx.AsyncClient()

    async def aclose(self) -> None:
        """Release the shared httpx client. Called from the server's
        shutdown path; safe to call more than once."""
        try:
            await self._http_client.aclose()
        except Exception:
            pass

    def is_rebootstrap_needed(self, account_id: str) -> bool:
        return bool(self._needs_rebootstrap.get(account_id))

    def _mark_rebootstrap_needed(self, account_id: str) -> None:
        self._needs_rebootstrap[account_id] = True

    async def get_access_token(self, account: Account) -> str:
        if account.id not in self._locks:
            self._locks[account.id] = asyncio.Lock()

        async with self._locks[account.id]:
            cached = self._tokens.get(account.id)
            now = time.time()
            if cached and cached.get("expires_at", 0) > now + 10:
                return cached["access_token"]
            return await self._refresh_token(account)

    async def _refresh_token(self, account: Account) -> str:
        if not account.auth or account.auth.type != "xoauth2":
            raise ValueError("Not an OAuth account")
        secret_ref = account.auth.secret_ref
        if not secret_ref:
            raise ValueError("No secret_ref for OAuth account")

        self._check_test_injection(account.id)

        refresh_token = self.secret_store.get(secret_ref)
        try:
            access_token, expires_in = await self._request_access_token(
                refresh_token, provider=account.provider
            )
        except _RefreshHTTPError as exc:
            self._log_audit(account.id, "DENY", exc.error)
            if exc.error == "invalid_grant":
                self._mark_rebootstrap_needed(account.id)
            raise

        if self._test_hooks.oauth_token_lifetime_override is not None:
            expires_in = self._test_hooks.oauth_token_lifetime_override

        self._tokens[account.id] = {
            "access_token": access_token,
            "expires_at": time.time() + expires_in,
        }
        self._persist_access_token_if_configured(account, access_token)
        self._log_audit(account.id, "ALLOW", "OK")
        return access_token

    def _check_test_injection(self, account_id: str) -> None:
        """Test-only error injection (``TestHooks.oauth_inject_error``).

        Raises with the same audit + rebootstrap side effects the
        production refresh path produces for a server-side error.
        """
        injected_error = self._test_hooks.oauth_inject_error
        if not injected_error:
            return
        self._log_audit(account_id, "DENY", injected_error)
        if injected_error == "invalid_grant":
            self._mark_rebootstrap_needed(account_id)
        raise RuntimeError(f"OAuth refresh failed: {injected_error}")

    async def _request_access_token(
        self, refresh_token: str | None, *, provider: str
    ) -> tuple[str, int]:
        """POST against the token endpoint and parse the response.

        Raises ``RuntimeError`` with the server's ``error`` field text
        on non-200. On ``invalid_grant`` the caller is responsible for
        flipping ``needs_rebootstrap`` — that mapping lives in the
        orchestrator (see :meth:`_refresh_token`) because the same
        rule applies to test-injection errors that never hit the wire."""
        token_uri = "https://oauth2.googleapis.com/token"
        if provider == "google-mock":
            token_uri = "http://127.0.0.1:19080/default/token"
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
            # The caller knows the account_id; route the audit + the
            # rebootstrap flag through there. Here we only signal that
            # the refresh failed with a structured reason.
            raise _RefreshHTTPError(err)
        data = resp.json()
        access_token = data["access_token"]
        expires_in = data.get("expires_in", 3600)
        return access_token, expires_in

    def _persist_access_token_if_configured(
        self, account: Account, access_token: str
    ) -> None:
        """Write the access token to the configured secret-store backend
        when the account opts into ``token_cache: persist_all``. No-op
        for ``memory_only`` or for backends other than ``file_dir``."""
        if account.token_cache != "persist_all":
            return
        if not account.auth or not account.auth.secret_ref:
            return
        store_cfg = self.config.accounts_file.secret_store
        if store_cfg is None or store_cfg.backend != "file_dir" or not store_cfg.path:
            return
        access_ref = account.auth.secret_ref.replace("refresh_token", "access_token")
        rel_path = access_ref[len("secret://"):].lstrip("/")
        target = pathlib.Path(store_cfg.path) / rel_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(access_token, encoding="utf-8")

    def _log_audit(self, account_id: str, decision: str, reason_or_result: str):
        audit_dir = (
            self.config.accounts_file.audit.directory
            if self.config.accounts_file.audit
            else None
        )
        if not audit_dir:
            return
        today = datetime.now(timezone.utc).date().isoformat()
        path = pathlib.Path(audit_dir) / f"{today}.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "tool": "token_refresh",
            "decision": decision,
            "account": account_id,
        }
        if decision == "ALLOW":
            entry["result"] = reason_or_result
        else:
            entry["reason"] = reason_or_result
        with open(path, "a") as f:
            f.write(json.dumps(entry) + "\n")


class _RefreshHTTPError(RuntimeError):
    """Internal: the token endpoint returned non-200. Carries the
    server's structured ``error`` field for the orchestrator to map to
    audit + rebootstrap state."""

    def __init__(self, error: str) -> None:
        super().__init__(f"OAuth refresh failed: {error}")
        self.error = error
