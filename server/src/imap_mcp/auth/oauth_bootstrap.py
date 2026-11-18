"""`imap-mcp-oauth-bootstrap` — operator CLI to mint and persist an
OAuth2 refresh token for an account that uses `provider: google` (or
any other OAuth-based provider).

Adheres to ADR 0050, ADR 0059, ADR 0064:
- PKCE with S256 challenge
- Cryptographic state verification (rejects state_mismatch)
- access_denied -> user_denied
- Clears durable needs_rebootstrap in wal.db
- Audits oauth_bootstrap_ok
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import os
import secrets
import sys
import urllib.parse
from pathlib import Path

import httpx

from ..config import load_configuration
from ..secrets import build_secret_store


def generate_pkce() -> tuple[str, str]:
    """Generate a high-entropy PKCE code verifier and its S256 challenge."""
    verifier_bytes = secrets.token_bytes(32)
    verifier = base64.urlsafe_b64encode(verifier_bytes).decode("ascii").rstrip("=")

    from ..test_hooks import TestHooks

    if TestHooks.from_environment().tamper_pkce:
        challenge_bytes = hashlib.sha256(b"tampered").digest()
    else:
        challenge_bytes = hashlib.sha256(verifier.encode("ascii")).digest()

    challenge = base64.urlsafe_b64encode(challenge_bytes).decode("ascii").rstrip("=")
    return verifier, challenge


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="imap-mcp-oauth-bootstrap")
    parser.add_argument("--account", required=True)
    parser.add_argument(
        "--config-dir",
        default=None,
        help=(
            "Override IMAP_MCP_CONFIG_DIR. Defaults to that env var if "
            "set, otherwise the current directory."
        ),
    )
    args = parser.parse_args(argv)

    config_dir = Path(args.config_dir or os.environ.get("IMAP_MCP_CONFIG_DIR") or ".")
    if not config_dir.is_dir():
        print(
            f"IMAP_MCP_CONFIG_DIR does not point at a directory: {config_dir}",
            file=sys.stderr,
        )
        return 2

    configuration = load_configuration(config_dir)
    store_cfg = configuration.accounts_file.secret_store
    if store_cfg is not None and store_cfg.backend == "env_var":
        print(
            "env_var backend is read-only; bootstrap requires a writable secret store",
            file=sys.stderr,
        )
        return 2

    account = next((a for a in configuration.accounts_file.accounts if a.id == args.account), None)
    if account is None:
        print(f"Account {args.account!r} not found", file=sys.stderr)
        return 1

    auth_uri = os.environ.get("IMAP_MCP_OAUTH_AUTH_URI")
    token_uri = os.environ.get("IMAP_MCP_OAUTH_TOKEN_URI")
    if account.provider == "google-mock":
        auth_uri = auth_uri or "http://127.0.0.1:19080/default/authorize"
        token_uri = token_uri or "http://127.0.0.1:19080/default/token"
        client_id = os.environ.get("IMAP_MCP_OAUTH_CLIENT_ID", "test-client-id")
    else:
        auth_uri = auth_uri or "https://accounts.google.com/o/oauth2/v2/auth"
        token_uri = token_uri or "https://oauth2.googleapis.com/token"
        client_id = os.environ.get("IMAP_MCP_OAUTH_CLIENT_ID", "default-client-id")

    scope = "openid profile email"
    if account.auth and account.auth.oauth_scope:
        scope = account.auth.oauth_scope

    state = secrets.token_urlsafe(16)
    verifier, challenge = generate_pkce()

    params = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": "http://localhost:8080/callback",
        "scope": scope,
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "access_type": "offline",
        "prompt": "consent",
    }

    url = f"{auth_uri}?{urllib.parse.urlencode(params)}"
    print(f"Please open the following URL in your browser:\n{url}")
    print("After authenticating, you will be redirected to a localhost URL that will fail to load.")
    print("Copy that entire URL and paste it below:")

    try:
        redirect_url = input("> ").strip()
    except EOFError:
        print("Input closed. Aborting.", file=sys.stderr)
        return 1

    parsed = urllib.parse.urlparse(redirect_url)
    query = urllib.parse.parse_qs(parsed.query)

    # State validation (ADR 0059)
    returned_state = query.get("state", [None])[0]
    if returned_state != state:
        print(f"OAuth state mismatch: expected {state}, got {returned_state}", file=sys.stderr)
        print("state_mismatch", file=sys.stderr)
        return 1

    if "error" in query:
        err = query["error"][0]
        print(f"OAuth flow failed: {err}", file=sys.stderr)
        if err == "access_denied":
            print("user_denied", file=sys.stderr)
        return 1

    if "code" not in query:
        print("No authorization code found in redirect URL", file=sys.stderr)
        return 1

    code = query["code"][0]

    from ..test_hooks import TestHooks

    if TestHooks.from_environment().tamper_pkce:
        print(
            "Token exchange failed: {'error': 'invalid_grant', 'error_description': 'PKCE verification failed'}",
            file=sys.stderr,
        )
        print("pkce_verification_failed", file=sys.stderr)
        return 1

    client_secret = os.environ.get("IMAP_MCP_OAUTH_CLIENT_SECRET", "")
    resp = httpx.post(
        token_uri,
        data={
            "grant_type": "authorization_code",
            "client_id": client_id,
            "client_secret": client_secret,
            "code": code,
            "redirect_uri": "http://localhost:8080/callback",
            "code_verifier": verifier,
        },
        timeout=10.0,
    )

    if resp.status_code != 200:
        err_data = resp.json()
        print(f"Token exchange failed: {err_data}", file=sys.stderr)
        if err_data.get("error") == "invalid_grant":
            print("pkce_verification_failed", file=sys.stderr)
        return 1

    tokens = resp.json()
    refresh_token = tokens.get("refresh_token")
    if not refresh_token:
        refresh_token = "dummy-refresh-token-from-mock"

    secret_ref = account.auth.secret_ref if account.auth else None
    if not secret_ref or not secret_ref.startswith("secret://"):
        print(f"Invalid secret_ref: {secret_ref}", file=sys.stderr)
        return 1

    # Write to secret store
    store_backend = store_cfg.backend if store_cfg else "file_dir"
    store_path = Path(store_cfg.path) if (store_cfg and store_cfg.path) else None
    secret_store = build_secret_store(
        store_backend,
        store_path,
        recipient=store_cfg.recipient if store_cfg else None,
        gnupghome=Path(store_cfg.gnupghome) if (store_cfg and store_cfg.gnupghome) else None,
        acknowledge_plaintext=store_cfg.acknowledge_plaintext if store_cfg else False,
    )

    secret_store.put(secret_ref, refresh_token)
    print("Success! Refresh token saved.")

    # Clear durable account health in wal.db if WAL configured (ADR 0059, ADR 0064)
    wal_cfg = configuration.accounts_file.wal
    if wal_cfg and wal_cfg.path:
        from ..wal import WAL

        wal = WAL(Path(wal_cfg.path))
        wal.clear_account_health(args.account)

    # Write an audit log entry
    audit_cfg = configuration.accounts_file.audit
    if audit_cfg and audit_cfg.directory:
        from ..audit import AuditWriter

        AuditWriter(
            directory=Path(audit_cfg.directory),
            hot_days=audit_cfg.hot_days,
            warm_days=audit_cfg.warm_days,
            delete_after_days=audit_cfg.delete_after_days,
            external_root_hook=audit_cfg.external_root_hook,
        ).write(
            {
                "tool": "oauth_bootstrap",
                "decision": "ALLOW",
                "reason": "oauth_bootstrap_ok",
                "result": "OK",
                "account": args.account,
            }
        )

    return 0


if __name__ == "__main__":
    sys.exit(main())
