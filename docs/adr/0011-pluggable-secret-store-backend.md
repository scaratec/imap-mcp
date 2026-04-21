# ADR 0011: Pluggable Secret Store Backend

- **Status:** Accepted
- **Date:** 2026-04-20
- **Deciders:** Randy Nel Gupta

## Context

The server manages a small but non-trivial inventory of secrets:

- OAuth2 refresh tokens ([ADR 0009]).
- OAuth2 access tokens, for accounts using `persist_all` ([ADR 0010]).
- Shared tokens for callers whose authentication is `shared_token`
  ([ADR 0015]).
- Legacy password/app-password credentials for non-OAuth accounts.

Deployments differ significantly in where secrets belong:

- A single-user local setup wants encrypted files on disk that
  Git-version-control under a standard flow (git-crypt, sops).
- A Docker/Kubernetes deployment wants tokens from orchestrator-
  managed environment variables or mounted secret volumes.
- A production scaling setup wants an online secret manager (HashiCorp
  Vault, GCP Secret Manager) with rotation, audit, and IAM.

No single choice is correct everywhere. Worse: baking one choice into
the server couples operator practice to server architecture.

Re-implementing cryptography inside the server would be a serious
mistake. Mature ecosystems exist for each of the backends above; the
server's job is to call them, not to replace them.

## Decision

Secrets are accessed through a narrow **`SecretStore` interface**.
Backends are configured per deployment; the server itself performs no
cryptographic operations on secret material.

```python
class SecretStore(Protocol):
    def get(self, account_id: str, key: str) -> str | None: ...
    def put(self, account_id: str, key: str, value: str) -> None: ...
    def delete(self, account_id: str, key: str) -> None: ...
```

Configuration example:

```yaml
secret_store:
  backend: file_dir
  path: ~/Projekte/scaratec/imap-mcp-secrets/
```

**Backends shipped in V1:**

- **`file_dir`** — plaintext files in a directory. The surrounding
  system provides confidentiality (git-crypt, LUKS, restrictive
  permissions). The server reads and writes ordinary files; it knows
  nothing about the encryption layer.
- **`env_var`** — secrets read from environment variables with a
  deterministic name pattern (`IMAP_MCP_SECRET__<account>__<key>`).
  Writes are rejected (environment is immutable from the server's
  perspective). Intended for Docker, Kubernetes, CI.
- **`gpg_file`** — individual GPG-encrypted files. Reads invoke `gpg
  --decrypt` via subprocess, triggering the user's `gpg-agent` /
  `pinentry` if a passphrase is needed. Writes invoke `gpg --encrypt`
  to the account's configured recipient key.

**Backends explicitly interface-compatible but not in V1:**

- `sops` (field-level encrypted YAML).
- `gcp_secret_manager`.
- `hashicorp_vault`.
- `keyring` (OS keyring via libsecret).

Each of these can be added later without changing the `SecretStore`
interface or any caller code.

**Invariants all backends must honour:**

- Secret values are treated as opaque strings. The store does not
  parse them.
- Values returned by `get` must never be logged, printed, or included
  in any audit record. The server is responsible for the logging hygiene;
  the backend is responsible for not emitting secrets to its own logs
  or process output.
- `put` is atomic — partial writes must not leave the store with a
  half-updated value under the given key.
- `get` on a missing key returns `None`; it does not throw.
- A backend may declare itself read-only (e.g. `env_var`). Attempting
  `put` or `delete` on a read-only backend is a fatal configuration
  error, not a silent no-op.

## Consequences

### Positive

- **Deployment flexibility.** Local developers use `file_dir` + Git-
  crypt. Containerized production uses `env_var`. Cloud-hosted
  deployments add `gcp_secret_manager` without touching the server.
- **Clean separation of concerns.** Cryptography lives in mature
  tools; the server stays a mail policy engine.
- **Interface small enough to reason about.** Three methods. Mockable
  for tests without elaborate harnesses.
- **Future-proof.** New backends plug in without changing existing
  code paths.

### Negative

- **Every backend is a potential operational footgun.** Operators
  must understand what their chosen backend does and does not
  protect. Documentation per backend is non-negotiable.
- **Read-only backends require bootstrap to be done elsewhere.**
  `env_var` deployments cannot run `oauth_bootstrap` against
  themselves; bootstrap happens in a dev environment and the tokens
  are handed to the orchestrator manually. Documented in the
  operator manual.

### Neutral

- The interface is synchronous. Async-wrapping happens in the caller
  when the backend blocks (the local filesystem backends are fast
  enough that sync is fine; network backends wrap themselves).

## Security Implications

- **No in-server crypto.** The server never decides how to encrypt or
  how to authenticate to a secret service. That responsibility lives
  in the backend binary or library. This is deliberate: home-grown
  crypto is a classical source of bugs and reviewer fatigue.
- **Backend selection is a security decision.** `file_dir` without an
  encrypting file system is plaintext on disk. The deployment
  documentation must make this explicit; the server refuses to run in
  such a configuration without an explicit `acknowledge_plaintext:
  true` flag in the config.
- **Read-only enforcement.** A read-only backend is a strong statement:
  secrets cannot be modified without going through the orchestrator
  (the true source of truth). The server honours that statement by
  refusing write operations; it does not fall back to a local file.
- **Process-environment visibility.** `env_var` secrets are visible to
  anyone who can read `/proc/<pid>/environ`. On Linux this is
  restricted to the process owner and root, but containers often run
  as root; the threat model must include sibling-container compromise.
- **Subprocess-based backends (`gpg_file`) expose decrypted material
  briefly in pipes.** The subprocess invocation uses inherited stdio;
  the parent reads the decrypted content over a pipe. Memory pressure
  on the host may swap this to disk. LUKS again is the only defence.
- **Audit trail.** Secret-store operations (get/put/delete) are logged
  at the *decision* layer ([ADR 0021]) as "loaded secret for account
  X" or "wrote secret for account X"; the value is never logged. The
  backend itself logs nothing.

## Alternatives Considered

- **Hardcoded backend.** Rejected for the reasons in Context:
  deployment shapes differ.
- **Roll a custom encrypted format.** Rejected. Cryptography inside
  the server introduces review burden and long-tail bugs that are
  already solved by existing tools.
- **Require an external secrets service for all deployments.**
  Rejected. Overkill for single-user local setups and creates a
  dependency that prevents offline use.
- **Store plaintext in the main configuration file.** Rejected for
  every reason a security-conscious project would reject it.

## References

- [ADR 0009] — OAuth2 refresh token lifecycle the store supports.
- [ADR 0010] — token cache strategies that use the store.
- [ADR 0015] — caller `shared_token` authentication.
- [ADR 0021] — audit log; never contains secret values.

[ADR 0009]: 0009-oauth2-authorization-code-with-scope-minimization.md
[ADR 0010]: 0010-configurable-token-cache-strategy.md
[ADR 0015]: 0015-caller-identity-and-authentication.md
[ADR 0021]: 0021-audit-log-format.md
