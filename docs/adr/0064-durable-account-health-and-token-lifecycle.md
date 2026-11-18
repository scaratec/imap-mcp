# ADR 0064: Durable Account Health and Token Lifecycle

- **Status:** Accepted
- **Date:** 2026-11-17
- **Deciders:** Randy Nel Gupta

## Context

[ADR 0010](0010-configurable-token-cache-strategy.md) defined `memory_only` and `persist_all` OAuth2 access-token caching alongside an in-memory `needs_rebootstrap` flag, leaving two operational defects:

1. When a refresh token was revoked (`invalid_grant`), `needs_rebootstrap` vanished on process restart or `SIGHUP` reload ([LIM 0017](../limitations/0017-expired-oauth-token-not-proactively-or-durably-detectable.md)), causing restarted processes to hammer the OAuth provider repeatedly.
2. Transient IMAP `AUTHENTICATE XOAUTH2` rejections immediately following token rotation failed the caller request instead of refreshing the cached access token once and retrying.

## Decision

We manage runtime OAuth2 tokens via **per-account `memory_only` / `persist_all` caching, proactive renewal at 80 % of token lifetime with connection-pool drain, a single reactive refresh-and-retry on stale cached XOAUTH2 rejection, and durable `account_health` persistence in `wal.db` across restarts and `SIGHUP` reloads**.

### Rules

1. **Token caching and proactive 80 % renewal:**
   - `memory_only` (default) keeps access tokens in RAM only; `persist_all` also writes access tokens to writable `SecretStore` backends ([ADR 0050](0050-secret-stores-and-oauth-posture.md)) and reuses unexpired persisted tokens across restarts.
   - Once 80 % of an access token's `expires_in` lifetime has elapsed, the next acquire refreshes the token, drains idle/active pooled connections for that account ([ADR 0055](0055-bounded-per-account-connection-pool.md)), and logs `token_refresh` (`reason: "token_refresh_ok"`).
2. **Single reactive XOAUTH2 retry:** If `AUTHENTICATE XOAUTH2` fails while using a cached access token, the server invalidates the cached token, refreshes once at the OAuth token endpoint, drains the account pool, and retries `AUTHENTICATE XOAUTH2` once on a new connection. A second rejection returns `ALLOW + ERROR` (`transient_imap_failure`).
3. **Durable `needs_rebootstrap` in `wal.db`:** When a refresh-token exchange returns `invalid_grant` (or when `oauth_scope` changes on `SIGHUP`, [ADR 0049](0049-single-caller-boundary-and-validated-loading.md)), the server writes `account_health(account_id, state='needs_rebootstrap', secret_sha256, updated_at)` in `wal.db`, audits `token_refresh` (`decision: "DENY"`, `reason: "token_refresh_failed"`), reports `state: "needs_rebootstrap"` in `list_accounts`, and denies all calls to that account with `DENY / needs_rebootstrap` (with zero provider network traffic) across restarts and `SIGHUP` reloads until `imap-mcp-oauth-bootstrap` ([ADR 0059](0059-oauth2-pkce-and-durable-account-health.md)) succeeds or `secret_sha256` of the refresh token changes.

## Consequences

### Positive

- **Revoked tokens stay halted durably.** Eliminates repeated failed OAuth exchanges across restarts (withdrawing [LIM 0017](../limitations/0017-expired-oauth-token-not-proactively-or-durably-detectable.md)) while clearing automatically when an operator rotates the Kubernetes `Secret` ([LIM 0021](../limitations/0021-oauth-bootstrap-and-token-persistence-not-possible-in-pod.md)).
- **Absorbs token revocation races.** Single reactive retry recovers cleanly from mid-TTL access-token invalidation.

### Negative

- Requires `wal.db` write access to persist `account_health` durably (already present in both workstation and `/state` PVC deployments).

### Neutral

- Password-authenticated accounts report `state: "active"` and do not use token refresh.

## Security Implications

- **Attack surface.** Bounds reactive XOAUTH2 retries to strictly 1 per call to prevent auth-failure loops.
- **Trust boundaries.** Isolates token caches per `account_id` and drains pooled sockets on every credential change.
- **Data exposure.** Stores only a SHA-256 fingerprint (`secret_sha256`) of the refresh token in `wal.db`, never raw token bytes.
- **Failure modes.** `invalid_grant` fails closed to `needs_rebootstrap`; transient network errors fail closed to `transient_imap_failure` without poisoning durable health.
- **Auditability.** Every token refresh (`token_refresh_ok` / `token_refresh_failed`) and `pool_drain` writes a chained audit entry ([ADR 0048](0048-pre-effect-audit-and-test-boundary.md)).

## Alternatives Considered

- **Keep `needs_rebootstrap` in volatile memory only ([ADR 0010](0010-configurable-token-cache-strategy.md)).** Rejected: loses revoked-account state on every process restart or new local stdio session.
- **Proactive startup token refresh for every configured account before serving.** Rejected: slows startup of short-lived stdio sessions and fails startup when an unused secondary account is offline.
- **Unlimited reactive retries on XOAUTH2 failure.** Rejected: risks account lockouts at the upstream mail provider.

## References

- Supersedes [ADR 0010](0010-configurable-token-cache-strategy.md).
- Withdraws [LIM 0017](../limitations/0017-expired-oauth-token-not-proactively-or-durably-detectable.md).
- [ADR 0049](0049-single-caller-boundary-and-validated-loading.md), [ADR 0050](0050-secret-stores-and-oauth-posture.md), [ADR 0055](0055-bounded-per-account-connection-pool.md), [ADR 0059](0059-oauth2-pkce-and-durable-account-health.md).
