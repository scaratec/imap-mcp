# LIM 0003: OAuth2 scenarios reference a nonexistent mock provider

- **Status:** Resolved
- **Resolution intent:** must-resolve (technical debt)
- **Date proposed:** 2026-04-21
- **Date approved:** 2026-04-21
- **Date resolved:** 2026-05-06 — Mock-OAuth-Server
  (`navikt/mock-oauth2-server`) im Docker-Stack, OAuth-Bootstrap,
  Token-Lifecycle und Scope-Minimization Szenarien grün.
  Scope-Change-Detection bei SIGHUP-Reload implementiert: Account
  wechselt zu `needs_rebootstrap`, IMAP-Handler verweigern
  Verbindungen. Alle `@pending_LIM_0003`-Szenarien aufgelöst.
- **Proposed by:** Claude (implementation agent)
- **Approved by:** Randy Nel Gupta
- **Related ADRs:** [ADR-0009](../adr/0009-oauth2-authorization-code-with-scope-minimization.md), [ADR-0010](../adr/0010-configurable-token-cache-strategy.md), [ADR-0011](../adr/0011-pluggable-secret-store-backend.md)
- **Related Guidelines:** BDD Guidelines §7.1 (deterministic mocks), §7.2 (mocks must simulate behaviour, not wishful thinking), §4.3 (persistence validation)

## Context

[ADR 0009] specifies that the server authenticates against IMAP
providers via OAuth2 XOAUTH2 with a per-account scope-minimised
authorization-code flow (RFC 8252 with PKCE). [ADR 0010] adds the
per-account token-cache strategy. [ADR 0011] defines the secret-store
interface against which refresh tokens are persisted.

The BDD suite encodes these requirements in
`bdd/features/auth/oauth2_bootstrap.feature`. All eight scenarios
reference a fixture named `google-mock` at a
`https://localhost:mock-oauth2` address. That fixture does not
exist today. No Docker service, no Python stub, no HTTP endpoint —
only a placeholder name in feature files.

This blocks every scenario in the feature file. It also blocks every
other scenario that implicitly assumes an OAuth-authenticated
account, such as cross-account moves where one side is an OAuth
provider.

## Nature of the weakness

Without a mock provider, the OAuth2 code path of the server is
**not exercised by the BDD suite at all**. Specifically:

- **L8.1 (happy-path bootstrap)** — the full Authorization-Code +
  PKCE + token-exchange pipeline is unreachable.
- **L8.2 (user denies consent)** — no way to simulate the consent
  screen producing `access_denied`.
- **L8.3 (PKCE tampering)** — no authorization server to tamper
  with code_challenge against.
- **L8.5 (invalid_grant on refresh)** — no refresh endpoint to
  force into an error.
- **L8.6, L8.7 (token-cache memory_only vs persist_all)** — require
  a real token lifecycle with exchange telemetry to assert on.
- **L8.8 (proactive refresh)** — requires a token endpoint that
  counts exchanges, so we can assert "≥ 2 exchanges occurred within
  50 seconds of a 60-second access-token lifetime".
- **L8.10 (concurrent refresh attempts)** — requires a real race
  against a single token endpoint.

All are paper specifications with no runnable verification.

Further, the Gmail adapter's XOAUTH2 SASL layer — even once the
Gmail-IMAP mock from LIM-0002 is in place — cannot be end-to-end
tested without an OAuth source of truth to validate the tokens
against.

## Why the clean solution is not chosen (yet)

A clean solution exists and is approved (see "Mitigations in place"
below): use `navikt/mock-oauth2-server` as an externally built,
independently validated OAuth2 mock, plus a thin Python wrapper for
programmable Test-API access. That solution is cheap and well-
understood. It is, however, **not yet integrated** — neither as a
Docker service in `bdd/docker/docker-compose.yml`, nor as a Python
helper, nor against real OAuth clients as validators.

This record captures the interim state honestly: until the
integration is complete, the scenarios in `oauth2_bootstrap.feature`
are not runnable.

## Mitigations in place

1. **Subproject scaffolded.** `bdd/mock-oauth/` exists, isolated
   from the BDD harness, from `bdd/mock-gmail/`, and from the
   server. Own `pyproject.toml`, own `.venv`, own minimal dependency
   set.
2. **Upstream component selected and documented.**
   `navikt/mock-oauth2-server` is chosen over alternatives (Ory
   Hydra, axa-group/oauth2-mock-server, WireMock) because it is
   explicitly built for test use, maintained in production by the
   Norwegian labour administration, programmable via its
   documented HTTP admin API, and Docker-image-native. Rationale is
   in `bdd/mock-oauth/README.md`.
3. **Validation strategy committed.** The mock is validated by
   running a flow with `google-auth` (Python, Google's official
   OAuth client). If Google's own client library completes the
   full flow against our mock, the mock behaves correctly enough
   for our server's codepath. Optional secondary validator:
   `msal` for the Microsoft tenant flavour.
4. **Wire-up plan for cross-mock integration is explicit.** The
   Gmail mock (LIM-0002) will validate XOAUTH2 bearer tokens by
   calling this OAuth mock's `/introspect` endpoint. The two mocks
   communicate only over HTTP. No shared code between them.
5. **Wire-up plan for Dovecot is explicit.** The Dovecot
   instances in `bdd/docker/` will gain an `oauth2` passdb entry
   pointing at this mock, so OAuth2 scenarios against a
   non-Gmail IMAP server are possible without the Gmail mock being
   involved.
6. **Interim suppression (explicit, not silent).** Until the
   integration lands, the eight scenarios in
   `oauth2_bootstrap.feature` are tagged `@pending:LIM-0003` and
   excluded from CI runs. Running the suite without the tag filter
   yields no false positives from OAuth scenarios passing against
   a missing provider.
7. **Error-path analysis marked accurately.** L8 entries that
   were previously shown as `covered` (L8.1, L8.2, L8.3, L8.6,
   L8.7, L8.10) are downgraded to `covered_by_LIM-0003` until the
   wiring is in place and validated.

## Residual risk

Even after the mock is wired up and validated, two categories of
risk persist:

- **Validator-client blind spots.** `google-auth` and `msal` cover
  the flows they are designed for — authorization-code, refresh,
  introspection against their respective providers' quirks. If our
  server's OAuth adapter uses a code path neither client exercises
  in a representative test, the mock's response for that path has
  only our own assumptions to lean on. This is the OAuth-layer
  analogue of LIM-0001's reason-code symmetry and LIM-0002's
  Gmail-path residuals.
- **Provider drift.** Google and Microsoft change OAuth details
  over time — new scope enforcements, new deprecations, changes in
  consent-screen behaviour, adjustments to refresh-token grace
  periods. The mock, even when current against the RFC, may fall
  behind provider-specific quirks without us noticing, until a
  real-provider incident exposes it. Periodic re-validation is the
  only honest answer.

Both residuals will, if they materialise, generate their own
subsidiary Limitation Records rather than being folded into this
one.

## Triggers for revisit

- **Mock is wired up and passes `google-auth` validation.** At
  that point this record moves to `Mitigated`. Any
  validator-blind-spot residuals get subsidiary records.
- **A production or staging OAuth incident** is traced to a
  behaviour a suppressed scenario should have caught.
- **`navikt/mock-oauth2-server` becomes unmaintained** or drops a
  feature we rely on. The upstream's status is itself a trigger.
- **18 months since the last real-provider flow was run against
  the mock**, regardless of other triggers. Provider drift is not
  a hypothetical risk.
- **A new `auth.type` is added** (e.g. `mtls` as mentioned in ADR
  0015) that is not yet mock-supported.

## References

- [ADR-0009](../adr/0009-oauth2-authorization-code-with-scope-minimization.md)
  — OAuth2 flow specification.
- [ADR-0010](../adr/0010-configurable-token-cache-strategy.md)
  — token-cache modes that the suppressed scenarios test.
- [ADR-0011](../adr/0011-pluggable-secret-store-backend.md)
  — refresh-token persistence that feeds into the OAuth flow.
- `bdd/mock-oauth/README.md` — the subproject design.
- `bdd/features/auth/oauth2_bootstrap.feature` — the eight
  scenarios currently suppressed under this limitation.
- `docs/error_path_analysis.md` — error-path table, L8 entries
  downgraded.
- RFC 6749, RFC 6750, RFC 7636, RFC 8252.
- [`navikt/mock-oauth2-server`](https://github.com/navikt/mock-oauth2-server)
- [`google-auth`](https://github.com/googleapis/google-auth-library-python)
- [`msal`](https://github.com/AzureAD/microsoft-authentication-library-for-python)

[ADR 0009]: ../adr/0009-oauth2-authorization-code-with-scope-minimization.md
