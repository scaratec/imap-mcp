# ADR 0009: OAuth2 Authorization-Code Flow with Per-Account Scope Minimization

- **Status:** Accepted
- **Date:** 2026-04-20
- **Deciders:** Randy Nel Gupta

## Context

Modern mail providers (Google, Microsoft 365) have deprecated basic-auth
for IMAP in favour of OAuth2 over the SASL `XOAUTH2` mechanism. The
server must therefore obtain, refresh, and present bearer tokens at IMAP
connect time.

Three independent questions must be answered:

1. **Which OAuth2 flow** bootstraps the initial token set?
2. **What scope** is requested, and is it uniform or per-account?
3. **Where do tokens live** between server restarts?

Question 3 is addressed by [ADR 0010] (token cache strategy) and
[ADR 0011] (secret store). This ADR answers 1 and 2.

A naive design picks the widest usable scope ("full IMAP") for all
accounts and calls it done. This fails the least-privilege test: a
policy-layer bug that mistakenly allows `move_out` on a read-only
account could still be blocked at the provider if the account's token
lacks write scope. The OAuth scope is a second, independent
authorization layer beneath our policy.

## Decision

**Flow: Authorization Code Flow for Installed Applications** (RFC 8252),
with PKCE. Each account is bootstrapped once, interactively:

- A bootstrap script (`scripts/oauth_bootstrap.py --account <id>`)
  launches the user's browser, receives the callback at
  `http://127.0.0.1:<ephemeral-port>`, exchanges the authorization code
  for access + refresh tokens, and writes them to the secret store
  chosen by the account's configuration.
- The refresh token is long-lived and is the only artefact persisted by
  the server itself; access tokens are treated per [ADR 0010].

**Scope: declared per-account in configuration**, the strictest value
that supports the policy assigned to that account. The server's OAuth
machinery reads this scope from the account config and requests
exactly it during bootstrap. Scope is not centrally hard-coded.

Example per-account scopes:

| Provider        | Read-only                                              | Full IMAP |
|-----------------|--------------------------------------------------------|-----------|
| Google          | `https://www.googleapis.com/auth/gmail.readonly`       | `https://mail.google.com/` |
| Microsoft 365   | `https://outlook.office.com/IMAP.AccessAsUser.All` + `offline_access` (no narrower IMAP scope exists) | same |

Accounts with no per-account scope fall back to a configured default
(recommended: the read-only scope for the provider).

**Refresh tokens** are redeemed for access tokens at connect time and
before the 80% lifetime mark during long-running connections. Failure
modes are handled per [ADR 0010].

**No service-account / domain-wide delegation.** Google Workspace and
Microsoft tenant admins can grant an application access to any user's
mailbox without per-user consent. This is the opposite of least-privilege
and is excluded from V1.

## Consequences

### Positive

- **Two authorization layers.** Policy (internal) and OAuth scope
  (external). A caller cannot perform an operation unless *both* allow
  it. A bug in one is not a full bypass.
- **Interactive bootstrap is transparent.** The user sees the consent
  screen, approves, and the server never handles credentials directly.
- **PKCE protects against code interception** on the loopback
  redirect.
- **Revocation works.** The user can revoke at the provider's UI; the
  server observes the revocation as a refresh failure, marks the
  account `needs_rebootstrap`, and stops connection attempts.

### Negative

- **Bootstrap requires a browser.** Headless-only environments (pure
  servers, CI) cannot run the interactive flow. The device-authorization
  flow (RFC 8628) is the natural fallback and is documented as a future
  optional flow; it is not in V1.
- **Providers differ.** Each new provider needs an auth adapter. The
  adapter is small (endpoints, scope map, `XOAUTH2` string format), but
  the list grows.
- **Scope granularity differs by provider.** Google splits read from
  full IMAP; Microsoft does not. The per-account scope field therefore
  has asymmetric expressive power, which must be documented.

### Neutral

- Bootstrap writes directly to the configured secret store; it never
  prints tokens to stdout or to shell history. Operators who want to
  script bootstrap do so through the secret-store API.

## Security Implications

- **Scope as defence-in-depth.** A misconfigured policy cannot ask the
  IMAP server for more than the token's scope allows. This is a
  genuine second layer, not a ceremonial one.
- **Refresh tokens are long-lived credentials.** They must be stored
  with the same care as passwords. The secret-store abstraction
  ([ADR 0011]) carries that responsibility; this ADR forbids in-server
  refresh-token persistence outside that abstraction.
- **Access tokens never hit disk** under the default cache strategy
  ([ADR 0010]). When the `persist_all` strategy is selected, they land
  in the same secret store as refresh tokens, under the same protection.
- **PKCE closes the loopback-interception vector.** A malicious local
  process that sniffs the authorization code cannot exchange it
  without the PKCE verifier held in the bootstrap process's memory.
- **Consent-replay is bounded.** If a refresh token is exfiltrated,
  the attacker can mint access tokens until the user revokes at the
  provider. The server detects a successful reauthorization by a third
  party only through unusual access patterns in the audit log; this is
  not a defence, merely a documented limit.
- **No service account.** Ruling out domain-wide delegation is a
  policy choice; it sacrifices some deployment convenience for a much
  cleaner blast radius. A future ADR may revisit this for controlled
  multi-mailbox scenarios, with a mandatory justification section.

## Alternatives Considered

- **Device Authorization Flow (RFC 8628).** Deferred, not rejected.
  Appropriate for headless servers; documented as a future flow.
  V1 focuses on the common interactive case.
- **Single hard-coded full scope per provider.** Rejected for loss of
  defence-in-depth and for the "archive readonly" use case where the
  provider offers a narrower scope.
- **Service accounts with domain-wide delegation.** Rejected as
  above.
- **Password / App-Password only.** Remains supported for providers
  that still allow it (self-hosted dovecot, some legacy setups), via a
  separate auth adapter. Not the subject of this ADR.
- **Client credentials flow.** Not applicable to user-mailbox access
  on the targeted providers.

## References

- RFC 8252 — OAuth 2.0 for Native Apps (Installed App pattern).
- RFC 7636 — PKCE.
- RFC 8628 — Device Authorization Grant (future flow).
- Google IMAP XOAUTH2:
  <https://developers.google.com/gmail/imap/xoauth2-protocol>
- Microsoft 365 IMAP XOAUTH2:
  <https://learn.microsoft.com/en-us/exchange/client-developer/legacy-protocols/how-to-authenticate-an-imap-pop-smtp-application-by-using-oauth>
- [ADR 0010] — token cache strategies.
- [ADR 0011] — secret store interface.

[ADR 0010]: 0010-configurable-token-cache-strategy.md
[ADR 0011]: 0011-pluggable-secret-store-backend.md
