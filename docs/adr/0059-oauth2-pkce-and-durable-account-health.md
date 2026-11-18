# ADR 0059: OAuth2 PKCE CLI Bootstrap

- **Status:** Accepted
- **Date:** 2026-11-17
- **Deciders:** Randy Nel Gupta

## Context

[ADR 0009](0009-oauth2-authorization-code-with-scope-minimization.md) specified interactive OAuth2 bootstrap for XOAUTH2 IMAP accounts, but contained three flaws:

1. It claimed `https://www.googleapis.com/auth/gmail.readonly` provided a second independent read-only IMAP authorization layer on Gmail, whereas Google's IMAP server requires `https://mail.google.com/` (`gmail.readonly` works only on the REST Gmail API).
2. `state` mismatch handling before token exchange was unspecified.
3. Loopback HTTP listener mode vs. manual URL-paste mode for headless sessions was conflated.

## Decision

We bootstrap OAuth2 refresh tokens out-of-band via **`imap-mcp-oauth-bootstrap` using Authorization Code + PKCE (`S256`) and cryptographic `state` verification**, while **enforcing startup rejection of incompatible narrow custom scopes and documenting honestly that PDP policy is the sole authorization boundary on Google and Microsoft 365 IMAP**.

### Rules

1. **Interactive PKCE + `state` flow:** `imap-mcp-oauth-bootstrap --account <id>` generates a high-entropy PKCE `code_verifier` (`S256`) and random `state`, listens on an ephemeral `127.0.0.1` callback port by default (or accepts `--manual-paste` on headless terminals), rejects `state` mismatches (`state_mismatch`) and consent refusals (`access_denied` -> `user_denied`) before contacting the token endpoint, writes the resulting `refresh_token` atomically via `SecretStore` ([ADR 0050](0050-secret-stores-and-oauth-posture.md)), clears any durable `needs_rebootstrap` health record for `<id>` ([ADR 0064](0064-durable-account-health-and-token-lifecycle.md)), and writes an `oauth_bootstrap` audit entry (`reason: "oauth_bootstrap_ok"`).
2. **Honest provider scope validation:** On Google (`https://mail.google.com/`) and Microsoft 365 (`IMAP.AccessAsUser.All`), no read-only IMAP OAuth scope exists, so the `imap-mcp` PDP is the single read/write enforcement layer. When an operator configures a generic OAuth IMAP provider with a read-only scope (`*.readonly`) alongside folder write capabilities in policy, server startup refuses the incompatible configuration closed.

## Consequences

### Positive

- **Honest blast-radius model.** Eliminates wishful reliance on non-existent Gmail read-only IMAP scopes (satisfying BDD Guideline §7.2).
- **CSRF- and interception-resistant bootstrap.** Enforces both `state` and PKCE (`S256`) before storing any credential.

### Negative

- Initial OAuth consent and re-consent require human interaction on a workstation ([LIM 0018](../limitations/0018-oauth-reauth-not-initiable-from-mcp-no-remediation-guidance.md), [LIM 0021](../limitations/0021-oauth-bootstrap-and-token-persistence-not-possible-in-pod.md)).

### Neutral

- Runtime token caching, reactive XOAUTH2 retry, and durable `needs_rebootstrap` health are governed by [ADR 0064](0064-durable-account-health-and-token-lifecycle.md).

## Security Implications

- **Attack surface.** Rejects tampered callbacks (`state_mismatch`) prior to token exchange and forbids service-account domain-wide delegation.
- **Trust boundaries.** Keeps OAuth consent strictly out-of-band on the operator CLI, never on the MCP bus ([ADR 0052](0052-tool-surface-3-0-0-and-non-goals.md)).
- **Data exposure.** Authorization codes and refresh tokens are never printed to stdout/stderr or written to audit logs.
- **Failure modes.** Read-only `env_var` secret stores, denied consent, PKCE mismatches, or scope/capability conflicts fail closed without writing partial secrets.
- **Auditability.** Successful bootstrap writes a chained `oauth_bootstrap_ok` audit record ([ADR 0048](0048-pre-effect-audit-and-test-boundary.md)).

## Alternatives Considered

- **Simulate `gmail.readonly` IMAP enforcement in test mocks ([ADR 0009](0009-oauth2-authorization-code-with-scope-minimization.md)).** Rejected: violates BDD Guideline §7.2 and misleads operators.
- **Expose an `initiate_oauth` MCP tool.** Rejected: enables consent-URL phishing by prompt-injected agents ([LIM 0018](../limitations/0018-oauth-reauth-not-initiable-from-mcp-no-remediation-guidance.md)).
- **OAuth2 Device Authorization Grant (RFC 8628) as sole flow.** Rejected: Google restricts device-code scopes for IMAP (`https://mail.google.com/`), whereas Desktop Authorization Code + PKCE works universally.

## References

- Supersedes [ADR 0009](0009-oauth2-authorization-code-with-scope-minimization.md).
- [ADR 0050](0050-secret-stores-and-oauth-posture.md), [ADR 0064](0064-durable-account-health-and-token-lifecycle.md).
- [LIM 0018](../limitations/0018-oauth-reauth-not-initiable-from-mcp-no-remediation-guidance.md), [LIM 0021](../limitations/0021-oauth-bootstrap-and-token-persistence-not-possible-in-pod.md).
- RFC 6749, RFC 7636, RFC 8252.
