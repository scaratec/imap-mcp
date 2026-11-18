# LIM 0010: Production IMAPS TLS handshake not exercised by local BDD stack

- **Status:** Accepted
- **Resolution intent:** must-resolve (technical debt)
- **Date proposed:** 2026-05-07
- **Date approved:** 2026-11-17
- **Proposed by:** Spec-Audit (production deployment test)
- **Approved by:** Randy Nel Gupta
- **Scope:** bdd/docker, server/src/imap_mcp/imap_core.py
- **Related ADRs:** [ADR-0012](../adr/0012-python-runtime-and-library-stack.md), [ADR-0050](../adr/0050-secret-stores-and-oauth-posture.md), [ADR-0055](../adr/0055-bounded-per-account-connection-pool.md)
- **Related Guidelines:** BDD Guidelines §7.1 (deterministic mocks), §7.2 (mocks must simulate real behaviour)

## Resolution intent

`must-resolve`. OAuth `client_secret` validation and email-address account IDs (`user` vs. `id`) are now covered directly by the `3.0.0` BDD suite (`auth/oauth2_bootstrap.feature` and `auth/explicit_imap_user.feature`). The remaining gap — exercising `IMAP4_SSL` over TLS port 993 against a local Dovecot listener with ephemeral test PKI — is a bounded fixture debt to be closed when the Docker IMAP stack adds a TLS listener.

## Context

Production IMAP providers (Gmail, Microsoft 365, hosted Dovecot) speak implicit TLS on port 993 (`IMAP4_SSL`), whereas `bdd/docker/docker-compose.yml` exposes plain IMAP on loopback ports `11143` and `12143` (`IMAP4`).

## Nature of the weakness

No automated BDD scenario exercises the `IMAP4_SSL` branch in `imap_core.py`, TLS certificate validation failures, or TLS session reuse across pooled connections.

## Why the clean solution is not chosen

Wiring implicit TLS in `bdd/docker/` requires generating ephemeral CA and leaf certificates before `docker compose up` and injecting a custom `SSL_CERT_FILE` trust bundle into both server and proxy processes without weakening production default `ssl.create_default_context()` verification. Keeping the current plain-TCP loopback stack avoids adding a custom trust-anchor override hook to production TLS setup until a dedicated PKI fixture is built.

## Mitigations in place

- OAuth `client_secret` verification and email-shaped account IDs (originally bundled here) are now covered by `bdd/features/auth/oauth2_bootstrap.feature` and `bdd/features/auth/explicit_imap_user.feature`.
- `IMAP4_SSL` in `server/src/imap_mcp/imap_core.py` is a two-line branch over `port == 993` using stdlib `ssl` defaults (`aioimaplib.IMAP4_SSL`) and is verified in live workstation operation against `imap.gmail.com:993` and hosted Dovecot (`993`).
- Unit tests in `server/tests/` verify `IMAP4_SSL` selection on port `993`.

## Residual risk

An `aioimaplib` upgrade that breaks `IMAP4_SSL` handshake framing or `SSLContext` defaults while leaving plain `IMAP4` intact passes the local BDD suite and fails first against a live port-993 provider.

## Triggers for revisit

- Any `aioimaplib` major/minor dependency bump.
- Addition of custom TLS options (for example client certificates / mTLS or custom CA bundles) in `accounts.yaml`.
- Any TLS-related connection bug observed in production.

## References

- [ADR-0012](../adr/0012-python-runtime-and-library-stack.md) — `aioimaplib` client library.
- [ADR-0050](../adr/0050-secret-stores-and-oauth-posture.md) — explicit `user` login identity and OAuth posture.
- [ADR-0055](../adr/0055-bounded-per-account-connection-pool.md) — per-account connection pool.
