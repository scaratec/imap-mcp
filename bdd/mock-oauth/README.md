# mock-oauth — OAuth2 provider fixture

A test fixture that gives the BDD suite a programmable OAuth2
authorisation server, so that the eight scenarios in
`../features/auth/oauth2_bootstrap.feature` — currently referring
to a nonexistent `google-mock` provider — have something real to run
against.

Unlike the Gmail mock (which is built from scratch because no
suitable mock for Gmail's private IMAP dialect exists), this mock
**uses an existing, independently validated component**:
[**`navikt/mock-oauth2-server`**][navikt], a mature
OAuth2/OIDC mock server used widely in the JVM and container
ecosystem for test purposes.

This subproject exists only to host a thin Python wrapper around the
mock's HTTP control API plus the configuration the BDD harness feeds
into the container. No Python OAuth server is implemented here.

The accepted technical debt that motivates this subproject is
recorded in [LIM-0003](../../docs/limitations/0003-oauth2-scenarios-not-runnable.md).

[navikt]: https://github.com/navikt/mock-oauth2-server

## Why not written from scratch

Problem 3's character is different from Problem 2's (LIM-0002):

- **OAuth2 is a standardised protocol.** RFC 6749 (core), RFC 6750
  (bearer tokens), RFC 7636 (PKCE), RFC 8252 (native apps). Any
  compliant mock must follow the same behaviour the RFCs describe.
- **Mature test-focused mocks exist.** `navikt/mock-oauth2-server`
  is designed specifically as a test double — its
  "programmable next response" API and its out-of-the-box
  Authorization-Code + PKCE + refresh flow were built for exactly
  our use case.
- **Self-reinforcement is a real risk.** Writing our own OAuth
  server would validate our own understanding of OAuth against our
  own understanding of OAuth. Using an independent implementation
  breaks that symmetry on its own terms.

For those reasons, the subproject does not contain an OAuth server.
It contains configuration and a wrapper.

## Why a separate subproject

Four boundaries:

1. **Isolation from the BDD harness.** The harness under `../`
   does not import from here. The mock is reached over the wire.
2. **Isolation from the Gmail mock.** The OAuth mock and the Gmail
   mock are two orthogonal fixtures that cooperate: the Gmail mock
   validates bearer tokens by calling the OAuth mock's
   introspection endpoint. They are not merged, for the reasons
   spelled out in the LIM-0003 context.
3. **Isolation from the server.** No cross-imports with
   `../../server/`. The server speaks OAuth over HTTP to the mock;
   there is no code sharing.
4. **Own dependency set.** `httpx` for the wrapper's HTTP calls,
   `pydantic` for structured config. Nothing else.

## Validation strategy

Analogous to LIM-0002: the mock is validated against an independent
OAuth client that is widely used against *real* OAuth providers.

Primary validator: **`google-auth`** (Python, Google's own OAuth2
client library). If `google-auth` completes a full
Authorization-Code-with-PKCE flow against our mock, including refresh
and introspection, the mock's RFC-conformance is independently
corroborated by a client maintained by a major OAuth vendor.

Optional secondary validator: **`msal`** (Microsoft Authentication
Library, Python) — for the Microsoft-tenant codepath. Covers
provider-specific edge cases Google does not exercise.

Because `navikt/mock-oauth2-server` is already widely used with
these clients in the Navikt ecosystem, a failed validation is
either a misconfiguration on our side or a regression in navikt —
both useful signals.

## What the subproject contains (planned)

```
bdd/mock-oauth/
├── pyproject.toml          # isolated helper project
├── README.md               # this file
├── config/
│   └── oauth-mock.yaml     # programmable default responses the
│                           # container loads at startup
└── src/mock_oauth/
    ├── __init__.py
    ├── client.py           # thin wrapper over the mock's /admin
    │                       # HTTP API: prime_consent(),
    │                       # prime_error(), set_token_lifetime(),
    │                       # exchange_count(), reset()
    └── types.py            # pydantic models for mock responses
```

The docker-compose integration adds a single service:

```yaml
  oauth-mock:
    image: ghcr.io/navikt/mock-oauth2-server:<pinned-version>
    container_name: imap-mcp-bdd-oauth-mock
    ports:
      - "127.0.0.1:19080:8080"
    volumes:
      - ./mock-oauth/config:/app/config:ro
    environment:
      - JSON_CONFIG_PATH=/app/config/oauth-mock.yaml
```

Port 19080 is chosen to cleanly separate from 11143 / 12143 (dovecot)
and whatever port the Gmail mock ultimately binds.

## Wire-up with the Gmail mock

The Gmail mock under `../mock-gmail/` will accept XOAUTH2 SASL
authentication and delegate bearer-token validation to this OAuth
mock by calling `/introspect`. The introspection response is
authoritative for the Gmail mock's session decision. No in-process
coupling, no shared code — pure HTTP.

The same wiring applies to the Dovecot instances under
`../docker/docker-compose.yml` once they are configured with
Dovecot's `oauth2` passdb pointing at this mock. That lets us
exercise the OAuth2 codepath independently of Gmail specifics.

## Out of scope

- No SMTP, no mail delivery, no user management beyond what the
  OAuth mock declares.
- No realistic rate limits or throttling; the mock is expected to
  respond immediately.
- No production secrets: the mock uses well-known test keys baked
  into the image.
- No Microsoft tenant discovery endpoints unless `msal` is actively
  used as a validator.

## References

- [LIM-0003](../../docs/limitations/0003-oauth2-scenarios-not-runnable.md)
  — the technical debt this subproject pays down.
- [`navikt/mock-oauth2-server`](https://github.com/navikt/mock-oauth2-server)
  — the upstream mock we wrap.
- RFC 6749, RFC 6750, RFC 7636, RFC 8252.
- [`google-auth`](https://github.com/googleapis/google-auth-library-python)
  — primary validator.
- [`msal`](https://github.com/AzureAD/microsoft-authentication-library-for-python)
  — secondary validator, optional.
