import httpx

class MockOAuthClient:
    """A thin HTTP wrapper around the navikt/mock-oauth2-server.
    
    This client is used to configure the mock for specific test scenarios,
    such as setting up a user, priming errors, or validating tokens.
    """
    
    def __init__(self, base_url: str = "http://127.0.0.1:19080"):
        self.base_url = base_url.rstrip("/")
        self.client = httpx.Client(base_url=self.base_url)

    def close(self):
        self.client.close()
        
    def reset(self):
        """Reset the mock server state."""
        # While the mock doesn't have a direct /admin/reset endpoint,
        # we might need to rely on container restart if state leaks,
        # or we just re-configure it per test. The mock itself is mostly stateless
        # besides token issuance.
        pass

    def prime_consent(self, issuer_id: str = "default", subject: str = "test-user@example.com"):
        """Configure the mock to issue tokens for a specific subject on an issuer."""
        # The mock-oauth2-server handles consent automatically for the /authorize endpoint
        # based on the login prompt it presents. By default, it presents a simple form.
        # We don't need a specific "prime_consent" API call to the mock server itself,
        # rather we use its interactive authorize endpoint during the flow.
        pass

    def get_discovery_url(self, issuer_id: str = "default") -> str:
        return f"{self.base_url}/{issuer_id}/.well-known/openid-configuration"

    def get_issuer_url(self, issuer_id: str = "default") -> str:
        return f"{self.base_url}/{issuer_id}"
