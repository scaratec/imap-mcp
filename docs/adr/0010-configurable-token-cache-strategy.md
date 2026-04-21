# ADR 0010: Configurable Token Cache Strategy

- **Status:** Accepted
- **Date:** 2026-04-20
- **Deciders:** Randy Nel Gupta

## Context

[ADR 0009] establishes that OAuth2 refresh tokens must be persisted
(otherwise the server would be unable to function after a restart).
Access tokens are a separate question: they are short-lived (typically
1 hour for Google, similar for Microsoft) and are renewable from the
refresh token.

Two sensible policies exist:

- **Memory-only access tokens.** Minimizes the amount of bearer
  material that exists on disk at any time. Access tokens live only in
  the server process.
- **Persisted access tokens.** Keeps the active access token in the
  secret store alongside the refresh token. The server can start up and
  immediately connect to IMAP without re-minting a token, and repeated
  restarts do not burn refresh-token usage.

Neither policy is unambiguously better. Deployments with
LUKS-encrypted storage and a trusted operator prefer the persisted
variant for faster restarts and reduced provider interaction. Deployments
with a larger attacker population and frequent process restarts prefer
the memory-only variant.

This is a deployment-shaped question, not an architecture one. The
design must accept both, not pick one.

## Decision

The token cache strategy is a **per-account configuration choice** with
two modes; a global default is configurable and overridden per account.

```yaml
oauth_defaults:
  token_cache: memory_only        # conservative global default

accounts:
  - id: rechnungen
    provider: google
    oauth_scope: https://mail.google.com/
    token_cache: persist_all      # override: keep access token too

  - id: audit-readonly
    provider: google
    oauth_scope: https://www.googleapis.com/auth/gmail.readonly
    # inherits token_cache: memory_only from the global default
```

Semantics:

- **`memory_only`:** only the refresh token is persisted via the secret
  store ([ADR 0011]). Access tokens are requested at server startup and
  before expiry; they exist only in the process memory space.
- **`persist_all`:** access tokens are *also* written to the secret
  store, in the same backend, under a per-account key. On server
  startup the cached access token is loaded; if it is still valid it
  is used directly. When it expires, the renewed token is written back.

Both modes share:

- **Proactive refresh.** At ~80% of the known access-token lifetime, a
  background task renews the token, transferring the new one atomically
  into the cache.
- **Reactive refresh.** If IMAP returns `AUTHENTICATIONFAILED` despite
  the cache indicating a fresh token, the server renews once and
  retries the single IMAP command that triggered the failure. A second
  failure marks the account `unhealthy`; no more retries until operator
  intervention.
- **Refresh-token exhaustion handling.** If the refresh endpoint
  returns `invalid_grant` or equivalent (user revoked consent, grace
  period lapsed), the account moves to `needs_rebootstrap`. An audit
  event is emitted and no further connections are attempted until the
  operator reruns the bootstrap flow.
- **No plaintext disk writes by the server itself.** All persistence
  goes through the secret store; the server does not implement its own
  encryption or its own file format for tokens.

## Consequences

### Positive

- **Operators choose the trade-off** appropriate to their environment
  rather than being forced into one posture.
- **No duplicated crypto.** Tokens reuse whatever protection the
  secret-store backend provides.
- **Restart cost is bounded.** `persist_all` skips one OAuth round-trip
  per restart; `memory_only` pays exactly one. Both are acceptable.
- **Uniform failure handling.** The two modes differ only in
  persistence, not in the state machine for refresh and recovery.

### Negative

- **Two modes to test.** The CI matrix must include a `persist_all`
  path and a `memory_only` path.
- **Config drift risk.** A global default of `memory_only` with some
  accounts overridden to `persist_all` requires operator awareness.
  Mitigation: `describe_policy` ([ADR 0017]) exposes the mode per
  account so operators can see what is actually in effect.

### Neutral

- Both modes assume the refresh token itself is persistent. Pure
  in-memory OAuth with no refresh token would require interactive
  bootstrap on every restart and is not offered.

## Security Implications

- **Least-privilege when desired.** `memory_only` keeps the on-disk
  bearer surface at one artefact per account (the refresh token).
- **Defence when convenient.** `persist_all` trades a small additional
  disk footprint (access token) for faster and more provider-friendly
  restarts; the additional item is protected by the same secret-store
  backend, so the security boundary does not move.
- **Reactive-refresh retry is bounded.** Exactly one retry on
  `AUTHENTICATIONFAILED`; a second failure is an error. This limits
  the server's ability to amplify an OAuth-related incident.
- **`needs_rebootstrap` is a hard stop.** The server does not attempt
  any silent recovery. An operator must re-run the interactive
  bootstrap, which re-obtains explicit user consent at the provider.
- **Audit trail.** Mode, refreshes, and state transitions
  (`unhealthy`, `needs_rebootstrap`) are audited ([ADR 0021]) with
  enough detail to reconstruct token life-cycle without exposing the
  tokens themselves.

## Alternatives Considered

- **Always in-memory.** Forces a refresh on every restart. For
  single-user local deployments, this is an unnecessary tax; for
  hardened environments it is the right choice — hence the mode.
- **Always persist.** Surfaces access tokens on disk by default; too
  permissive a default for a security-oriented project.
- **Custom encryption of tokens inside the server.** Rejected. The
  server must not re-invent cryptography when the secret-store
  abstraction already exists to handle this.
- **Separate backend for access tokens.** Rejected as unnecessary
  complexity: if the backend is good enough for the refresh token, it
  is good enough for the access token.

## References

- [ADR 0009] — OAuth2 flow and scope design.
- [ADR 0011] — secret-store interface and backends.
- [ADR 0017] — transparency of per-account configuration.
- [ADR 0021] — audit events for token lifecycle.

[ADR 0009]: 0009-oauth2-authorization-code-with-scope-minimization.md
[ADR 0011]: 0011-pluggable-secret-store-backend.md
[ADR 0017]: 0017-response-transparency-for-filtered-data.md
[ADR 0021]: 0021-audit-log-format.md
