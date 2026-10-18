# LIM 0021: OAuth bootstrap and token persistence are not possible in-pod

- **Status:** Proposed
- **Resolution intent:** must-resolve (technical debt)
- **Date proposed:** 2026-08-25
- **Date approved:** — (pending owner approval)
- **Proposed by:** the agent (k8s suitability assessment)
- **Approved by:** — (pending; project owner)
- **Related ADRs:** [ADR-0029](../adr/0029-kubernetes-sidecar-deployment-topology.md),
  [ADR-0009](../adr/0009-oauth2-authorization-code-with-scope-minimization.md),
  [ADR-0010](../adr/0010-configurable-token-cache-strategy.md),
  [ADR-0011](../adr/0011-pluggable-secret-store-backend.md)
- **Related Guidelines:** BDD Guidelines §6.1 (isolated runtime),
  §7.1 (deterministic dependencies)

## Resolution intent

`must-resolve`. Two of the three parts are buildable and one is not.
The interactive consent step inherently needs a human and a browser —
that part is permanent and belongs to the operator's workstation, not
to the pod. But **the server being structurally unable to persist a
credential it obtains at runtime** is a solvable gap: [ADR 0009] records
the Device Authorization Grant (RFC 8628) as "deferred, not rejected",
and [ADR 0011] already defines a writable-backend interface with
`gcp_secret_manager` and `hashicorp_vault` listed as interface-compatible
but unimplemented. Either closes the pod-side half. The debt is owed
against the day an OAuth-backed account is deployed under [ADR 0029],
not before.

## Context

[ADR 0029] deploys the server as a sidecar whose `/secrets` mount is a
read-only Kubernetes `Secret` projection. [ADR 0011] offers three
implemented backends: `file_dir` (plaintext files, re-read on each
access), `env_var` (explicitly documented as "intended for Docker,
Kubernetes, CI", and read-only — `put` raises), and `gpg_file`
(subprocess decryption, requires the `gpg` binary and a `GNUPGHOME`).
`sops`, `gcp_secret_manager`, `hashicorp_vault` and `keyring` are named
in the ADR as interface-compatible futures; none is implemented.

[ADR 0009] specifies the credential origin: an Authorization-Code flow
with PKCE, run by a separate CLI. The ADR states the constraint plainly
in its own consequences — "Bootstrap requires a browser. Headless-only
environments (pure servers, CI) cannot run the interactive flow." The
shipped CLI prints an authorization URL and reads the redirect URL back
from the terminal with `input()`; it opens no local listener despite a
`redirect_uri` of `http://localhost:8080/callback`. It writes its result
only for the `file_dir` backend and exits non-zero for any other.

At runtime the server performs only the refresh grant. Access tokens
live in memory; under `token_cache: persist_all` ([ADR 0010]) they are
additionally written as files beneath the secret-store path, and only
for `file_dir`.

[LIM-0017] and [LIM-0018] already record that an expired refresh token
is neither proactively nor durably detectable, and that re-authentication
cannot be initiated from the MCP surface. This record does not restate
those. It records the narrower, deployment-specific consequence: in a
pod, there is no path by which any of it can be repaired in place.

## Nature of the weakness

Precisely: **in the [ADR 0029] topology the server can read credentials
but can never write one, and no component inside the pod can obtain a
new one. Every credential the deployment will ever use must be minted
on an operator workstation and injected before the pod starts.**

Three distinct facets, each with its own observable consequence:

1. **Bootstrap cannot run in the pod.** The CLI needs a TTY for its
   copy-paste step and a browser for consent. A `kubectl exec` provides
   the TTY but not the browser, and the operator would be pasting a
   consent URL out of a production container. There is no non-interactive
   path — the Device Authorization Grant, which is the standard answer
   for exactly this shape, is not implemented.

2. **No implemented backend is both k8s-idiomatic and writable.** A
   `Secret` projection is a read-only tmpfs; `file_dir` pointed at it
   can read but any write fails with `EROFS`. `env_var` refuses writes
   by design. So `token_cache: persist_all` is unusable, and the
   deployment is confined to `memory_only`: every pod restart discards
   the cached access token and re-runs the refresh grant against the
   provider. That is merely wasteful — but it means the *only* durable
   credential is the injected refresh token, and the server has no
   writable place to put a replacement.

3. **A rotated refresh token cannot be retained.** Providers differ:
   Google returns a refresh token once at consent and reuses it,
   whereas Microsoft identity platform issues a new refresh token with
   each refresh response and expires the old one. Against a rotating
   provider, a server with no writable secret store holds a credential
   that the provider considers superseded the moment the first refresh
   succeeds. The deployment then works until the pod restarts, at which
   point the injected token is rejected with `invalid_grant`, the
   account goes to `needs_rebootstrap`, and — per [LIM-0018] — the deny
   carries no remediation guidance to the agent.

   Whether the current implementation attempts to persist a rotated
   refresh token at all was **not verified** during this assessment.
   The point stands either way: with a read-only mount there is nowhere
   for such an attempt to succeed.

## Why the clean solution is not chosen

- **Running the interactive flow in the pod.** Not achievable and not
  desirable. Consent is a human act against a provider's web UI; a
  production container is the wrong place for it even if the plumbing
  existed. This facet is genuinely permanent.
- **Implementing the Device Authorization Grant now.** This is the
  right answer to facet 1 and is deferred rather than rejected by
  [ADR 0009]. It is not being built as part of this assessment because
  it is a self-contained feature with its own provider-capability
  matrix (not every IMAP provider supports RFC 8628 for mail scopes)
  and its own scenarios, and because no OAuth-backed account is
  deployed under [ADR 0029] yet. Deferring it here is a scheduling
  decision, and the debt is recorded rather than hidden.
- **Implementing a writable cloud secret backend.** The right answer to
  facets 2 and 3, and already anticipated by [ADR 0011]'s interface. It
  is not built here because it introduces a cloud dependency and an
  authentication path of its own (workload identity), which is a larger
  decision than this assessment's scope and would warrant its own ADR.
- **Mounting a writable volume for secrets instead of a `Secret`
  projection.** Would make `file_dir` writable, but it abandons the
  Kubernetes secret-management path entirely: no encryption at rest via
  the API server, no RBAC on the secret object, no rotation tooling,
  and the plaintext refresh token sitting on a `PersistentVolume`. That
  trades a credential-lifecycle problem for a credential-storage
  problem, which is worse.

## Mitigations in place

- **Password-authenticated accounts are unaffected.** An IMAP account
  using a static password or an app password needs no rotation and no
  bootstrap; it is injected once as a `Secret` and works indefinitely.
  This covers Dovecot-style and app-password Gmail deployments
  completely, so the limitation binds only OAuth-backed accounts.
- **The failure is loud, not silent.** `invalid_grant` moves the
  account to `needs_rebootstrap`, which is a hard stop ([ADR 0010]);
  tool calls against that account are denied rather than silently
  returning partial data. The operator sees a broken account, not
  wrong results.
- **The refresh grant needs no writable path** in the non-rotating
  case. Against a provider that reuses refresh tokens, a `memory_only`
  cache plus an injected refresh token is a complete and stable
  configuration; the pod can restart indefinitely.
- **`/secrets` is read-only by design** ([ADR 0029]), so the failure
  surfaces as a mount-level error rather than as a partially written
  credential.
- **[LIM-0017] and [LIM-0018]** cover the detection and remediation-
  guidance gaps that this record's failures land in, so the surrounding
  behaviour is at least on the register rather than undocumented.
- **`docs/ops/kubernetes-sidecar-deployment.md`** prescribes
  `token_cache: memory_only`, states that the refresh token must be
  minted on a workstation before first deploy, and flags the rotating-
  provider case as requiring verification before an OAuth account is
  deployed.

## Residual risk

An operator bootstraps a Microsoft 365 mailbox on a workstation,
injects the refresh token as a `Secret`, and deploys the sidecar. The
agent works. Some days later the node is drained; the pod restarts and
presents the originally injected refresh token, which the provider
superseded at the first refresh. Authentication fails with
`invalid_grant`, the account enters `needs_rebootstrap`, and every tool
call against that mailbox is denied. The agent receives a deny with no
remediation guidance ([LIM-0018]) and, depending on its prompt, may
report "no mail found" rather than "credentials are broken". Recovery
requires an operator at a workstation with a browser, so the mailbox is
unavailable for as long as that takes — and the failure is triggered by
a routine cluster operation, not by anything the deployment did wrong.

A milder variant applies to every OAuth account regardless of provider:
there is no in-cluster path to re-authenticate, so credential expiry is
always an operator-workstation event with an unbounded response time.

## Triggers for revisit

- The first OAuth-backed account is deployed under [ADR 0029]. Before
  that happens, whether the provider rotates refresh tokens must be
  determined, and whether the implementation persists a rotated token
  must be verified.
- Any deployment against a provider that rotates refresh tokens; this
  escalates the record from operational friction to a restart-triggered
  outage and makes the paydown due.
- The Device Authorization Grant is implemented ([ADR 0009] deferral),
  which closes facet 1.
- A writable secret-store backend is implemented ([ADR 0011] future
  list), which closes facets 2 and 3 and would make this record
  `Resolved`.
- [LIM-0017] or [LIM-0018] is resolved, which changes how the failure
  presents and requires this record to be re-scoped.

## References

- [ADR-0029](../adr/0029-kubernetes-sidecar-deployment-topology.md) —
  the read-only `/secrets` projection this record bounds.
- [ADR-0009](../adr/0009-oauth2-authorization-code-with-scope-minimization.md)
  — browser requirement stated in its own consequences; Device
  Authorization Grant deferred.
- [ADR-0010](../adr/0010-configurable-token-cache-strategy.md) —
  `persist_all` versus `memory_only`; `needs_rebootstrap` as a hard
  stop.
- [ADR-0011](../adr/0011-pluggable-secret-store-backend.md) — backend
  matrix; `env_var` read-only and named for Kubernetes; writable cloud
  backends listed but unimplemented.
- [LIM-0017](0017-expired-oauth-token-not-proactively-or-durably-detectable.md)
  — detection gap this record's failures land in.
- [LIM-0018](0018-oauth-reauth-not-initiable-from-mcp-no-remediation-guidance.md)
  — remediation gap; why the agent cannot report the real cause.
- RFC 8628 — OAuth 2.0 Device Authorization Grant, the standard remedy
  for facet 1.
- `docs/ops/kubernetes-sidecar-deployment.md` — operator guidance.
