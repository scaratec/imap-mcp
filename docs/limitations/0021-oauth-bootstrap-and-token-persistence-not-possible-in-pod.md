# LIM 0021: OAuth interactive bootstrap and secret-projection writes are out-of-pod

- **Status:** Accepted
- **Resolution intent:** permanent (architectural boundary)
- **Date proposed:** 2026-08-25
- **Date approved:** 2026-11-17
- **Proposed by:** Architecture review (Kubernetes sidecar assessment)
- **Approved by:** Randy Nel Gupta
- **Scope:** deployment/kubernetes, server/src/imap_mcp/secrets.py
- **Related ADRs:** [ADR-0050](../adr/0050-secret-stores-and-oauth-posture.md), [ADR-0051](../adr/0051-loopback-kubernetes-sidecar-topology.md), [ADR-0059](../adr/0059-oauth2-pkce-and-durable-account-health.md)
- **Related Guidelines:** BDD Guidelines §6.1 (isolated runtime), §7.1 (deterministic dependencies)

## Resolution intent

`permanent` for the read-only Kubernetes `Secret` mount topology
([ADR-0051](../adr/0051-loopback-kubernetes-sidecar-topology.md)):
interactive browser OAuth2 PKCE consent (`imap-mcp-oauth-bootstrap`,
[ADR-0059](../adr/0059-oauth2-pkce-and-durable-account-health.md)) runs on
an operator workstation, and `/secrets` inside the sidecar container is
mounted `readOnly: true` (`token_cache: memory_only`).

## Context

Under [ADR-0051](../adr/0051-loopback-kubernetes-sidecar-topology.md), the
sidecar container has no interactive browser, no TTY, and a read-only
projected Kubernetes `Secret` at `/secrets`.

## Nature of the weakness

1. Initial OAuth2 refresh-token minting and re-bootstrapping after
   `invalid_grant` cannot be run inside the pod; an operator runs
   `imap-mcp-oauth-bootstrap` on a workstation and updates the Kubernetes
   `Secret`.
2. `token_cache: persist_all` cannot write to a read-only `/secrets` mount,
   so pods use `token_cache: memory_only` (performing one token refresh per
   pod startup) and cannot persist rotating refresh tokens in-pod without
   an external secret-manager backend.

## Why the clean solution is not chosen

Keeping `/secrets` `readOnly: true` and omitting Kubernetes API write
permissions from the pod's ServiceAccount prevents a compromised container
from overwriting cluster secrets. Google OAuth refresh tokens for published
Desktop apps do not rotate on refresh, so `memory_only` works cleanly.

## Mitigations in place

- **Durable `needs_rebootstrap` with automatic secret-fingerprint clear
  ([ADR-0059](../adr/0059-oauth2-pkce-and-durable-account-health.md)):**
  When an operator updates the refresh token in the Kubernetes `Secret` and
  rolls or `SIGHUP`s the pod, the changed `secret_sha256` clears
  `needs_rebootstrap` in `wal.db` automatically.
- **Startup validation ([ADR-0050](../adr/0050-secret-stores-and-oauth-posture.md)):**
  `env_var` + `persist_all` is rejected at startup, and `docs/ops/kubernetes-sidecar-deployment.md`
  mandates `token_cache: memory_only` for projected `Secret` mounts.

## Residual risk

Deploying an OAuth provider that rotates single-use refresh tokens on every
access-token exchange under a read-only `/secrets` mount will invalidate
the projected refresh token on the second pod restart until a writable
secret-manager backend is introduced by a future ADR.

## Triggers for revisit

- Onboarding a mail provider (such as a Microsoft 365 tenant policy with
  mandatory single-use refresh-token rotation) in Kubernetes sidecar pods.

## References

- [ADR-0050](../adr/0050-secret-stores-and-oauth-posture.md), [ADR-0051](../adr/0051-loopback-kubernetes-sidecar-topology.md), [ADR-0059](../adr/0059-oauth2-pkce-and-durable-account-health.md).
- [LIM-0018](0018-oauth-reauth-not-initiable-from-mcp-no-remediation-guidance.md).
