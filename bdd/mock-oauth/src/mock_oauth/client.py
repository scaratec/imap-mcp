"""MockOAuthClient for configuring and verifying the OAuth mock."""

from __future__ import annotations

import httpx

from .server import MockOAuthServer


class MockOAuthClient:
    """Client for controlling and testing against the OAuth mock."""

    def __init__(
        self,
        base_url: str = "http://127.0.0.1:19085",
        server: MockOAuthServer | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.server = server
        self.client = httpx.Client(base_url=self.base_url)

    def close(self) -> None:
        self.client.close()

    def reset(self) -> None:
        """Reset mock server state."""
        if self.server is not None:
            self.server.state.reset()
            return
        res = self.client.post("/__admin/reset", timeout=5.0)
        res.raise_for_status()

    def prime_token_error(
        self,
        error: str,
        description: str | None = None,
        grant_type: str | None = None,
        once: bool = True,
    ) -> None:
        """Prime an OAuth error on the next token exchange."""
        if self.server is not None:
            self.server.state.prime_token_error(
                error=error,
                description=description,
                grant_type=grant_type,
                once=once,
            )
            return
        res = self.client.post(
            "/__admin/prime_token_error",
            json={
                "error": error,
                "description": description,
                "grant_type": grant_type,
                "once": once,
            },
            timeout=5.0,
        )
        res.raise_for_status()

    def set_token_lifetime(self, expires_in_seconds: int) -> None:
        """Set custom token lifetime in seconds returned by token endpoint."""
        if self.server is not None:
            self.server.state.set_token_lifetime(expires_in_seconds)
            return
        res = self.client.post(
            "/__admin/set_token_lifetime",
            json={"expires_in": expires_in_seconds},
            timeout=5.0,
        )
        res.raise_for_status()

    def token_exchange_count(self) -> int:
        """Return total number of token exchange requests."""
        if self.server is not None:
            return self.server.state.token_exchange_count()
        res = self.client.get("/__admin/token_exchange_count", timeout=5.0)
        res.raise_for_status()
        return int(res.json()["count"])

    def validate_bearer(self, access_token: str) -> bool:
        """Validate an access token against expiry and revocation."""
        if self.server is not None:
            return self.server.state.validate_bearer(access_token)
        res = self.client.post(
            "/__admin/validate_bearer",
            json={"access_token": access_token},
            timeout=5.0,
        )
        res.raise_for_status()
        return bool(res.json()["valid"])

    def revoke_token(self, access_token: str) -> None:
        """Revoke an issued access token."""
        if self.server is not None:
            self.server.state.revoke_token(access_token)
            return
        res = self.client.post(
            "/__admin/revoke_token",
            json={"access_token": access_token},
            timeout=5.0,
        )
        res.raise_for_status()

    def register_refresh_token(
        self, refresh_token: str, scope: str = "https://mail.google.com/"
    ) -> None:
        """Register a known refresh token with the mock."""
        if self.server is not None:
            self.server.state.register_refresh_token(refresh_token, scope=scope)
            return
        res = self.client.post(
            "/__admin/register_refresh_token",
            json={"refresh_token": refresh_token, "scope": scope},
            timeout=5.0,
        )
        res.raise_for_status()

    def revoke_refresh_token(self, refresh_token: str) -> None:
        """Revoke a registered refresh token."""
        if self.server is not None:
            self.server.state.revoke_refresh_token(refresh_token)
            return
        res = self.client.post(
            "/__admin/revoke_refresh_token",
            json={"refresh_token": refresh_token},
            timeout=5.0,
        )
        res.raise_for_status()

    def get_discovery_url(self, issuer_id: str = "default") -> str:
        return f"{self.base_url}/{issuer_id}/.well-known/openid-configuration"

    def get_issuer_url(self, issuer_id: str = "default") -> str:
        return f"{self.base_url}/{issuer_id}"
