# LIM 0010: Production IMAP scenarios not covered by BDD

- **Status:** Accepted
- **Resolution intent:** must-resolve (technical debt)
- **Date proposed:** 2026-05-07
- **Proposed by:** Spec-Audit (production deployment test)
- **Related ADRs:** ADR-0009, ADR-0011, ADR-0015
- **Related Guidelines:** BDD Guidelines §7.2 (mocks must simulate behaviour, not wishful thinking)

## Context

Three production-relevant code paths are not covered by BDD scenarios
because the test fixtures do not replicate production conditions:

### 1. IMAPS (Port 993 / TLS)

The entire BDD stack runs against Dovecot on port 11143 (plain IMAP).
No scenario exercises `IMAP4_SSL`. The bug (connection hang on port
993) was discovered during production deployment against Gmail and
fixed empirically.

**Infrastructure needed:** Dovecot with TLS certificate in
`bdd/docker/docker-compose.yml`.

### 2. OAuth `client_secret` in token exchange

The mock OAuth server (`navikt/mock-oauth2-server`) does not require
`client_secret` in token exchange requests. The bug (missing
`client_secret` in both `oauth_bootstrap.py` and `oauth_manager.py`)
was discovered during production deployment against Google OAuth and
fixed empirically.

**Infrastructure needed:** Configure mock OAuth server to validate
`client_secret` presence, or add a BDD scenario that asserts the
token exchange request body contains `client_secret`.

### 3. Account IDs with email addresses

The BDD fixture uses short-form account IDs (`gupta-scaratec`,
`personal`) that comply with the `_imap_user_for()` convention
(split at first dash). Production accounts use email addresses
(`gupta@scaratec.com`) as IDs, which pass through unsplit. No
scenario tests this path.

**Infrastructure needed:** A scenario with an email-address account
ID, or better: an explicit `imap_user` field in the Account config
model so the IMAP username is not derived by convention.

## Nature of the weakness

All three gaps were found during the first production deployment.
The BDD suite gave a false sense of completeness because the mocks
were too permissive (§7.2 violation).

## Mitigations in place

- All three bugs have been fixed in the server code.
- The fixes are covered by empirical evidence (manual tests against
  real Gmail).
- The fixes are deployed via `pipx install sc-imap-mcp`.

## Triggers for revisit

- Any new IMAP provider integration (Exchange, Fastmail).
- OAuth scope or credential changes.
- Next BDD sprint targeting production parity.
