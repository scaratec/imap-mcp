"""Validation tests for the mock OAuth2 server and client."""

from __future__ import annotations

import base64
import hashlib
from collections.abc import Generator
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from mock_oauth.client import MockOAuthClient
from mock_oauth.server import MockOAuthServer


@pytest.fixture(scope="module")
def oauth_server() -> Generator[MockOAuthServer, None, None]:
    server = MockOAuthServer(upstream_url="http://127.0.0.1:19080", host="127.0.0.1", port=0)
    server.start()
    yield server
    server.stop()


@pytest.fixture
def mock_client(oauth_server: MockOAuthServer) -> Generator[MockOAuthClient, None, None]:
    client = MockOAuthClient(base_url=oauth_server.base_url, server=oauth_server)
    client.reset()
    yield client
    client.close()


def _generate_pkce() -> tuple[str, str]:
    verifier = "dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk-verifier123456789"
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")
    return verifier, challenge


def test_authorization_code_pkce_s256_happy_path(mock_client: MockOAuthClient) -> None:
    """(a) Authorization Code + PKCE S256 Happy Path."""
    verifier, challenge = _generate_pkce()
    redirect_uri = "http://localhost:8080/callback"

    with httpx.Client(follow_redirects=False) as http:
        # 1. Authorize request with PKCE S256
        auth_res = http.get(
            f"{mock_client.base_url}/default/authorize",
            params={
                "response_type": "code",
                "client_id": "test-client",
                "redirect_uri": redirect_uri,
                "scope": "openid email offline_access",
                "state": "state123",
                "code_challenge": challenge,
                "code_challenge_method": "S256",
            },
        )
        assert auth_res.status_code == 302
        location = auth_res.headers["Location"]
        query = parse_qs(urlparse(location).query)
        code = query["code"][0]

        # 2. Token exchange with matching code_verifier
        token_res = http.post(
            f"{mock_client.base_url}/default/token",
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": redirect_uri,
                "client_id": "test-client",
                "code_verifier": verifier,
            },
        )
        assert token_res.status_code == 200
        data = token_res.json()
        assert "access_token" in data
        assert "refresh_token" in data
        assert mock_client.validate_bearer(data["access_token"]) is True
        assert mock_client.token_exchange_count() == 1


def test_pkce_code_verifier_mismatch(mock_client: MockOAuthClient) -> None:
    """(b) PKCE code_verifier Mismatch -> 400 invalid_grant."""
    _, challenge = _generate_pkce()
    redirect_uri = "http://localhost:8080/callback"

    with httpx.Client(follow_redirects=False) as http:
        auth_res = http.get(
            f"{mock_client.base_url}/default/authorize",
            params={
                "response_type": "code",
                "client_id": "test-client",
                "redirect_uri": redirect_uri,
                "scope": "openid email",
                "state": "state123",
                "code_challenge": challenge,
                "code_challenge_method": "S256",
            },
        )
        assert auth_res.status_code == 302
        code = parse_qs(urlparse(auth_res.headers["Location"]).query)["code"][0]

        # Exchange with wrong verifier
        token_res = http.post(
            f"{mock_client.base_url}/default/token",
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": redirect_uri,
                "client_id": "test-client",
                "code_verifier": "completely-wrong-code-verifier-string-12345",
            },
        )
        assert token_res.status_code == 400
        err = token_res.json()
        assert err["error"] == "invalid_grant"
        assert err["error_description"] == "PKCE verification failed"


def test_refresh_token_grant(mock_client: MockOAuthClient) -> None:
    """(c) refresh_token Grant."""
    verifier, challenge = _generate_pkce()
    redirect_uri = "http://localhost:8080/callback"

    with httpx.Client(follow_redirects=False) as http:
        auth_res = http.get(
            f"{mock_client.base_url}/default/authorize",
            params={
                "response_type": "code",
                "client_id": "test-client",
                "redirect_uri": redirect_uri,
                "scope": "openid email offline_access",
                "state": "state123",
                "code_challenge": challenge,
                "code_challenge_method": "S256",
            },
        )
        code = parse_qs(urlparse(auth_res.headers["Location"]).query)["code"][0]

        token_res = http.post(
            f"{mock_client.base_url}/default/token",
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": redirect_uri,
                "client_id": "test-client",
                "code_verifier": verifier,
            },
        )
        assert token_res.status_code == 200
        refresh_token = token_res.json()["refresh_token"]

        # Refresh grant
        refresh_res = http.post(
            f"{mock_client.base_url}/default/token",
            data={
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
                "client_id": "test-client",
            },
        )
        assert refresh_res.status_code == 200
        data = refresh_res.json()
        assert "access_token" in data
        assert mock_client.validate_bearer(data["access_token"]) is True
        assert mock_client.token_exchange_count() == 2


def test_primed_invalid_grant_on_refresh(mock_client: MockOAuthClient) -> None:
    """(d) Geprimtes invalid_grant auf Refresh."""
    mock_client.register_refresh_token("valid_rt")
    mock_client.prime_token_error(
        error="invalid_grant",
        description="Refresh token has been revoked",
        grant_type="refresh_token",
        once=True,
    )

    with httpx.Client(follow_redirects=False) as http:
        # First refresh attempt fails with primed error
        res1 = http.post(
            f"{mock_client.base_url}/default/token",
            data={
                "grant_type": "refresh_token",
                "refresh_token": "valid_rt",
                "client_id": "test-client",
            },
        )
        assert res1.status_code == 400
        data1 = res1.json()
        assert data1["error"] == "invalid_grant"
        assert data1["error_description"] == "Refresh token has been revoked"

        # Second refresh attempt does not get the primed error (once=True)
        res2 = http.post(
            f"{mock_client.base_url}/default/token",
            data={
                "grant_type": "refresh_token",
                "refresh_token": "valid_rt",
                "client_id": "test-client",
            },
        )
        assert res2.status_code == 200
        assert "access_token" in res2.json()


def test_unknown_refresh_token_rejected(mock_client: MockOAuthClient) -> None:
    """Unknown or revoked refresh token -> 400 invalid_grant."""
    with httpx.Client(follow_redirects=False) as http:
        res = http.post(
            f"{mock_client.base_url}/default/token",
            data={
                "grant_type": "refresh_token",
                "refresh_token": "unknown-never-registered-token",
                "client_id": "test-client",
            },
        )
        assert res.status_code == 400
        err = res.json()
        assert err["error"] == "invalid_grant"


def test_expires_in_control_and_revocation(mock_client: MockOAuthClient) -> None:
    """(e) expires_in-Steuerung und Token Revocation."""
    mock_client.set_token_lifetime(60)
    verifier, challenge = _generate_pkce()
    redirect_uri = "http://localhost:8080/callback"

    with httpx.Client(follow_redirects=False) as http:
        auth_res = http.get(
            f"{mock_client.base_url}/default/authorize",
            params={
                "response_type": "code",
                "client_id": "test-client",
                "redirect_uri": redirect_uri,
                "scope": "openid email",
                "state": "state123",
                "code_challenge": challenge,
                "code_challenge_method": "S256",
            },
        )
        code = parse_qs(urlparse(auth_res.headers["Location"]).query)["code"][0]

        token_res = http.post(
            f"{mock_client.base_url}/default/token",
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": redirect_uri,
                "client_id": "test-client",
                "code_verifier": verifier,
            },
        )
        assert token_res.status_code == 200
        data = token_res.json()
        assert data["expires_in"] == 60
        access_token = data["access_token"]
        assert mock_client.validate_bearer(access_token) is True

        # Test revocation
        mock_client.revoke_token(access_token)
        assert mock_client.validate_bearer(access_token) is False
