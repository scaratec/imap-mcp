# LIM 0018: OAuth re-authentication cannot be initiated from the MCP surface

- **Status:** Accepted
- **Resolution intent:** permanent (architectural boundary)
- **Date proposed:** 2026-06-18
- **Date approved:** 2026-11-17
- **Proposed by:** Claude (implementation agent)
- **Approved by:** Randy Nel Gupta
- **Scope:** server/src/imap_mcp/auth/oauth_bootstrap.py, server/src/imap_mcp/dispatch.py
- **Related ADRs:** [ADR-0044](../adr/0044-normalized-envelope-and-code-registry.md), [ADR-0050](../adr/0050-secret-stores-and-oauth-posture.md), [ADR-0052](../adr/0052-tool-surface-3-0-0-and-non-goals.md)
- **Related Guidelines:** BDD Guidelines §1.2 (observable behaviour), §4.2 (negative paths)

## Resolution intent

`permanent`. Interactive OAuth2 consent is an operator/mailbox-owner ceremony and is intentionally excluded from the MCP caller surface (`initiate_oauth` is a permanent non-goal in [ADR-0052]). The earlier remediation-documentation gap noted in the original draft has been closed in `README.md` and `list_accounts` (`state: "needs_rebootstrap"`).

## Context

When an account's OAuth2 refresh token is revoked or expires (`invalid_grant`), `imap-mcp` persists `needs_rebootstrap` ([ADR-0050]) and denies calls to that account with `DENY` (`reason: "needs_rebootstrap"`). Re-minting a refresh token requires running `imap-mcp-oauth-bootstrap --account <id>` interactively as the human mailbox owner.

## Nature of the weakness

An autonomous LLM agent cannot re-authorize a revoked mailbox on its own over MCP; mailbox access for that account halts until the human operator runs `imap-mcp-oauth-bootstrap` on a workstation (and updates the Kubernetes `Secret` if deployed under [ADR-0051]).

## Why the clean solution is not chosen

Exposing an OAuth consent ceremony over MCP (`initiate_oauth` / `complete_oauth`) would allow a compromised or prompt-injected agent to phish the human user with an authorization URL or bind attacker-chosen OAuth credentials, violating the strict caller/operator plane separation of [ADR-0052].

## Mitigations in place

- `list_accounts` durably reports `state: "needs_rebootstrap"` across restarts and `SIGHUP` reloads ([ADR-0050]), so callers and health checks immediately see why the account is unavailable.
- Every tool call against the affected account returns the deterministic categorical code `needs_rebootstrap` ([ADR-0044]) without affecting other configured accounts.
- `README.md` and `docs/ops/kubernetes-sidecar-deployment.md` document the exact `imap-mcp-oauth-bootstrap` CLI command and required environment variables.

## Residual risk

Unattended background agents experience downtime on a revoked OAuth account until a human operator completes `imap-mcp-oauth-bootstrap`.

## Triggers for revisit

- Adoption of an out-of-band operator notification webhook or alerting metric for `needs_rebootstrap` transitions.
- Introduction of non-interactive workload credentials on a provider that supports least-privilege per-mailbox delegation approved by a future ADR.

## References

- [ADR-0044](../adr/0044-normalized-envelope-and-code-registry.md) — `needs_rebootstrap` reason code.
- [ADR-0050](../adr/0050-secret-stores-and-oauth-posture.md) — durable `needs_rebootstrap` state and PKCE bootstrap CLI.
- [ADR-0052](../adr/0052-tool-surface-3-0-0-and-non-goals.md) — `initiate_oauth` non-goal.
