import os
import pytest
from mock_oauth.client import MockOAuthClient

@pytest.fixture
def mock_client():
    client = MockOAuthClient("http://127.0.0.1:19080")
    yield client
    client.close()

def test_google_auth_flow(mock_client):
    """
    Validate the mock against google-auth / google-auth-oauthlib.
    This proves that the mock satisfies a real OAuth2 client's expectations.
    """
    # Allow http for oauthlib during testing
    os.environ['OAUTHLIB_INSECURE_TRANSPORT'] = '1'
    os.environ['AUTHLIB_INSECURE_TRANSPORT'] = '1'

    from google_auth_oauthlib.flow import Flow

    client_config = {
        "web": {
            "client_id": "test-client-id",
            "client_secret": "test-client-secret",
            "auth_uri": f"{mock_client.base_url}/default/authorize",
            "token_uri": f"{mock_client.base_url}/default/token",
            "redirect_uris": ["http://localhost:8080/callback"]
        }
    }

    flow = Flow.from_client_config(
        client_config,
        scopes=["openid", "profile", "email"]
    )
    flow.redirect_uri = "http://localhost:8080/callback"

    # 1. Generate auth URL without prompt=consent so mock-oauth2-server auto-approves
    auth_url, state = flow.authorization_url()
    assert auth_url.startswith(f"{mock_client.base_url}/default/authorize")

    # 2. Complete flow via HTTP calls
    import httpx
    
    with httpx.Client(follow_redirects=False) as http:
        response = http.get(auth_url)
        
        # mock-oauth2-server auto-approves and redirects immediately
        assert response.status_code == 302
        redirect_loc = response.headers["Location"]
        assert redirect_loc.startswith("http://localhost:8080/callback")
        
        # 3. Exchange code for tokens
        flow.fetch_token(authorization_response=redirect_loc)
        
        # 4. Verify we got valid tokens
        creds = flow.credentials
        assert creds is not None
        assert creds.valid is True
        assert creds.token is not None # access token
        assert creds.id_token is not None
