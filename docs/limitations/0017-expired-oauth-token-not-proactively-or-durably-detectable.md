# LIM 0017: Expired OAuth refresh token is not proactively or durably detectable

- **Status:** Proposed
- **Resolution intent:** must-resolve (technical debt)
- **Date proposed:** 2026-06-18
- **Date approved:** — (pending owner approval)
- **Proposed by:** Claude (implementation agent)
- **Approved by:** — (pending; project owner)
- **Related ADRs:** [ADR-0009](../adr/0009-oauth2-authorization-code-with-scope-minimization.md), [ADR-0010](../adr/0010-configurable-token-cache-strategy.md), [ADR-0014](../adr/0014-policy-as-git-versioned-yaml.md)
- **Related Guidelines:** BDD Guidelines §4.3 (persistence validation), §7.2 (mocks simulate real behaviour)

## Resolution intent

`must-resolve`. The reactive deny path is functionally safe, but the
absence of proactive and durable detection is an observability gap the
project owes a clean fix. A paydown plan and concrete triggers are
given below.

## Context

[ADR 0009] establishes XOAUTH2 with a per-account refresh-token-based
flow; [ADR 0010] adds the token-cache strategy. When a refresh token
is revoked or expires, Google's token endpoint returns `invalid_grant`
on the next refresh. The server already models a recovery state:
`OAuthManager._needs_rebootstrap` (`server/src/imap_mcp/auth/oauth_manager.py:34`),
flipped on `invalid_grant` (`oauth_manager.py:76-80`), surfaced as
account `state: "needs_rebootstrap"` by `handle_list_accounts`
(`server/src/imap_mcp/handlers/accounts.py:69-72`) and enforced as a
`needs_rebootstrap` DENY by the IMAP handlers
(`accounts.py:89-90`).

The expectation a careful operator would hold — "the server can tell
me an account's credentials are dead" — is only partially met.

## Nature of the weakness

Detection of a dead refresh token has two distinct defects, each with
an observable consequence:

1. **Reactive only — never proactive.** The `invalid_grant` state is
   discovered solely as a side effect of an operation that calls
   `OAuthManager._refresh_token()` and hits the token endpoint
   (`oauth_manager.py:62-80`). No code path validates a refresh token
   at startup, on reload, or on a schedule. `handle_list_accounts`
   reads `is_rebootstrap_needed()` (a dict lookup) and never triggers
   a refresh (`accounts.py:62-72`). Consequence: immediately after a
   process (re)start, `list_accounts` reports an account whose refresh
   token was revoked weeks earlier as `state: "active"`. The truth
   appears only after the first `list_folders` / `search` fails.

2. **In-memory only — not durable.** `_needs_rebootstrap` is a plain
   `dict[str, bool]` on the `OAuthManager` instance (`oauth_manager.py:34`).
   It is not persisted to the secret store, the WAL, or any sidecar
   state. A process restart, or a SIGHUP policy reload that rebuilds
   the live state ([ADR 0014]; the reload swaps PDP + configuration and
   constructs a fresh manager), resets the flag to its empty default.
   Consequence: an account that correctly showed `needs_rebootstrap`
   reverts to `active` after the next reload/restart, until something
   again attempts — and fails — a refresh.

Together these mean the server's answer to "is this account's OAuth
credential still valid?" is trustworthy only in the window *after* a
real failed operation and *before* the next reload/restart — never at
the moment an operator or agent first asks.

## Why the clean solution is not chosen

A proactive + durable design is well understood; it is not blocked by
an external dependency. It is deferred on a cost/benefit basis, not on
difficulty:

- **Proactive validation** means attempting a token refresh per OAuth
  account at boot and/or on a timer. That adds outbound network calls
  to the startup path, introduces partial-failure and rate-limit
  handling, and couples server readiness to a third-party endpoint's
  availability. The benefit over the existing reactive deny is earlier
  *notice*, not earlier *safety* — a broken account is already denied
  correctly the moment it is used.
- **Durable state** means persisting the rebootstrap flag (secret-store
  entry or WAL row) and reconciling it on reload, including the
  inverse transition (a successful bootstrap must clear durable state).
  That is a small but real design surface that has not been scoped.

Because the reactive deny is already correct and safe, the marginal
value is observability and timing, which ranked below correctness-
critical policy work. This record is the IOU.

## Mitigations in place

- **Reactive detection is safe.** Once a refresh fails with
  `invalid_grant`, the account is flagged and every IMAP handler
  denies with `reason: "needs_rebootstrap"` (`accounts.py:89-90`); no
  data is served from an account with dead credentials.
- **Audit trail records every failure.** `OAuthManager._log_audit`
  writes a `token_refresh` / `DENY` / `invalid_grant` entry on each
  failed refresh (`oauth_manager.py:159-179`), so a dead token is
  eventually visible in the audit log even though it is not visible in
  live account state.
- **State is surfaced once known.** `list_accounts` does report
  `needs_rebootstrap` while the in-memory flag is set
  (`accounts.py:69-72`).

## Residual risk

An operator reloads or restarts the server (routine: SIGHUP after a
whitelist edit, or a nightly restart). `list_accounts` reports the
account as `active`. An agent — or a monitoring dashboard polling
`list_accounts` for account health — treats it as usable and plans
dependent work. The first real operation then fails with
`needs_rebootstrap`, after the agent has already taken steps that
assumed a working account. A health dashboard shows all-green for an
account whose credential died weeks ago, because the only signal
(`_needs_rebootstrap`) was reset by the reload. This exact sequence
occurred on 2026-06-18: after SIGHUP and a process restart, the
`scaratec@gmail.com` account presented as available and only a live
`search` surfaced the long-dead refresh token.

## Triggers for revisit

- A monitoring / health-check capability is added that depends on
  `list_accounts` (or any tool) reporting trustworthy account state.
- Proactive token refresh is implemented (foreshadowed by the
  proactive-refresh scenario referenced in [ADR 0010] / LIM-0003 §L8.8);
  durable detection should land with it.
- An incident report attributes an operator- or agent-facing failure
  to an account that read `active` while its refresh token was dead.
- The audit log accumulates ≥ 5 `invalid_grant` `token_refresh` DENY
  entries for the same account across distinct process lifetimes,
  indicating the flag is being repeatedly lost to restart/reload.

## References

- [ADR-0009](../adr/0009-oauth2-authorization-code-with-scope-minimization.md)
  — OAuth2 flow whose failure mode this record concerns.
- [ADR-0010](../adr/0010-configurable-token-cache-strategy.md)
  — token-cache and proactive-refresh strategy.
- [ADR-0014](../adr/0014-policy-as-git-versioned-yaml.md)
  — SIGHUP reload that resets the in-memory flag.
- [LIM-0018](0018-oauth-reauth-not-initiable-from-mcp-no-remediation-guidance.md)
  — the companion gap: even once detection is reliable, the server
  offers no in-band way to act on it.
- `server/src/imap_mcp/auth/oauth_manager.py` — detection mechanism.
- `server/src/imap_mcp/handlers/accounts.py` — state exposure and
  enforcement.
- RFC 6749 §5.2 (`invalid_grant`).

[ADR 0009]: ../adr/0009-oauth2-authorization-code-with-scope-minimization.md
[ADR 0010]: ../adr/0010-configurable-token-cache-strategy.md
[ADR 0014]: ../adr/0014-policy-as-git-versioned-yaml.md
