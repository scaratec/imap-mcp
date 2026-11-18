# mock-oauth — OAuth2 provider fixture

HTTP control and RFC 6749/7636 PKCE proxy/wrapper (`src/mock_oauth/server.py`,
`src/mock_oauth/client.py`) backed by [`navikt/mock-oauth2-server`][navikt]
(`127.0.0.1:19080` in `../docker/docker-compose.yml`), exercising the ten
OAuth2 PKCE, proactive/reactive XOAUTH2 refresh, and durable
`needs_rebootstrap` scenarios in `../features/auth/oauth2_bootstrap.feature`
([ADR-0059](../../docs/adr/0059-oauth2-pkce-and-durable-account-health.md),
[ADR-0064](../../docs/adr/0064-durable-account-health-and-token-lifecycle.md),
[LIM-0003](../../docs/limitations/0003-oauth2-scenarios-not-runnable.md)).

[navikt]: https://github.com/navikt/mock-oauth2-server

## Capabilities

- Real Authorization Code + PKCE (`S256`) and `refresh_token` token
  exchanges over HTTP against `navikt/mock-oauth2-server` (never in-server
  error-injection environment variables).
- Programmable control hooks (`reset()`, `prime_token_error()`,
  `set_token_lifetime()`, `token_exchange_count()`, `validate_bearer()`)
  used by both the BDD steps and `mock-gmail`'s `AUTHENTICATE XOAUTH2`
  handler.
- Standalone validation tests in `tests/test_validation.py` covering
  Authorization Code + PKCE, refresh token grant, PKCE mismatch rejection,
  and `invalid_grant` error responses.
