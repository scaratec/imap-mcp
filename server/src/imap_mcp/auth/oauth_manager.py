"""OAuth token lifecycle management."""

import asyncio
import time
import httpx
import logging
from typing import Any
from ..config import Account, Configuration
from ..secrets import SecretStore

logger = logging.getLogger(__name__)


class OAuthManager:
    def __init__(self, config: Configuration, secret_store: SecretStore):
        self.config = config
        self.secret_store = secret_store
        self._tokens: dict[str, dict[str, Any]] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    async def get_access_token(self, account: Account) -> str:
        if account.id not in self._locks:
            self._locks[account.id] = asyncio.Lock()

        async with self._locks[account.id]:
            cached = self._tokens.get(account.id)
            now = time.time()
            if cached and cached.get("expires_at", 0) > now + 10:
                return cached["access_token"]

            # Need to refresh
            return await self._refresh_token(account)

    async def _refresh_token(self, account: Account) -> str:
        if not account.auth or account.auth.type != "xoauth2":
            raise ValueError("Not an OAuth account")

        secret_ref = account.auth.secret_ref
        if not secret_ref:
            raise ValueError("No secret_ref for OAuth account")

        refresh_token = self.secret_store.get(secret_ref)
        if not refresh_token:
            # Check if there is an access_token in the secret store if persist_all is on
            # But normally we just use the refresh_token
            pass

        # Exchange token
        token_uri = "https://oauth2.googleapis.com/token"
        if account.provider == "google-mock":
            token_uri = "http://127.0.0.1:19080/default/token"

        import os

        client_id = os.environ.get("IMAP_MCP_OAUTH_CLIENT_ID", "test-client-id")
        client_secret = os.environ.get("IMAP_MCP_OAUTH_CLIENT_SECRET", "")

        # Test error injection
        injected_error = os.environ.get("IMAP_MCP_TEST_OAUTH_INJECT_ERROR")
        if injected_error:
            # We must log an audit entry and raise
            self._log_audit(account.id, "DENY", injected_error)
            if injected_error == "invalid_grant":
                if not hasattr(self, "_needs_rebootstrap"):
                    self._needs_rebootstrap = {}
                self._needs_rebootstrap[account.id] = True
            raise RuntimeError(f"OAuth refresh failed: {injected_error}")

        async with httpx.AsyncClient() as client:
            resp = await client.post(
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
                self._log_audit(account.id, "DENY", err)
                if err == "invalid_grant":
                    if not hasattr(self, "_needs_rebootstrap"):
                        self._needs_rebootstrap = {}
                    self._needs_rebootstrap[account.id] = True
                raise RuntimeError(f"OAuth refresh failed: {err}")

            data = resp.json()
            access_token = data["access_token"]
            expires_in = data.get("expires_in", 3600)

            # Allow test override
            if "IMAP_MCP_TEST_TOKEN_LIFETIME" in os.environ:
                expires_in = int(os.environ["IMAP_MCP_TEST_TOKEN_LIFETIME"])

            self._tokens[account.id] = {
                "access_token": access_token,
                "expires_at": time.time() + expires_in,
            }

            if account.token_cache == "persist_all":
                access_ref = secret_ref.replace("refresh_token", "access_token")
                # Need to use the raw path to write, but SecretStore in server doesn't have a write method
                # We can write directly to the backend if it's file_dir
                store_cfg = self.config.accounts_file.secret_store
                if store_cfg and store_cfg.backend == "file_dir" and store_cfg.path:
                    import pathlib

                    rel_path = access_ref[len("secret://") :].lstrip("/")
                    target = pathlib.Path(store_cfg.path) / rel_path
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_text(access_token, encoding="utf-8")

            self._log_audit(account.id, "ALLOW", "OK")
            return access_token

    def _log_audit(self, account_id: str, decision: str, reason_or_result: str):
        from datetime import datetime, timezone
        import json

        audit_dir = (
            self.config.accounts_file.audit.directory if self.config.accounts_file.audit else None
        )
        if not audit_dir:
            return

        import pathlib

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
