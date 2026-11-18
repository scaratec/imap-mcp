# ADR 0050: Pluggable Secret Stores

- **Status:** Accepted
- **Date:** 2026-11-17
- **Deciders:** Randy Nel Gupta

## Context

[ADR 0011](0011-pluggable-secret-store-backend.md) defined three secret backends (`file_dir`, `env_var`, `gpg_file`), but review exposed three gaps:

1. `acknowledge_plaintext: true` on `file_dir` was promised by [ADR 0011](0011-pluggable-secret-store-backend.md) yet unenforced by the configuration schema.
2. `gpg_file` error semantics did not distinguish pre-handshake caller token decryption failure (`auth_failed`) from post-handshake account credential decryption failure (`ALLOW + ERROR / secret_unavailable`).
3. Explicit IMAP login usernames (`account.user` distinct from email-shaped `account.id`) lacked a formal ADR anchor.

## Decision

We resolve all credentials (`secret://<category>/<id>/<key>`) and account login identities through **three atomic `SecretStore` backends with enforced `acknowledge_plaintext: true` on `file_dir` and distinct caller-handshake vs. account-credential failure codes**.

### 1. Account login identity (`user` vs. `id`)

Each account in `accounts.yaml` declares `id` (used in policies, tool arguments, audit records, and structured `From:` headers when email-shaped) and optional `user` (used on the wire for IMAP `LOGIN` and SASL `XOAUTH2`; defaults to `id` when omitted).

### 2. Three `SecretStore` backends

- **`file_dir`:** Reads/writes `<path>/<category>/<id>/<key>` (`0600` in `0700` directories) using atomic temp-file + `fsync` + rename writes. Requires explicit `acknowledge_plaintext: true` in `secret_store` configuration; omitting or setting it to `false` refuses server startup fatally.
- **`env_var`:** Read-only lookup from `IMAP_MCP_SECRET__<UPPER_PATH_WITH_DOUBLE_UNDERSCORES_AND_HYPHENS_AS_UNDERSCORES>`. Rejects write operations (`imap-mcp-oauth-bootstrap`) and rejects `token_cache: persist_all` at startup.
- **`gpg_file`:** Encrypts and decrypts `<path>/<category>/<id>/<key>.gpg` via `gpg` batch subprocesses (supporting optional `gnupghome` and loopback passphrase) with atomic temp-file replacement on write.

### 3. Failure taxonomy without secret leakage

- **Caller authentication token (`callers/<id>/token`):** Missing or undecryptable caller tokens fail the MCP handshake with `auth_failed` and record `reason: "auth_failed"` in the audit trail.
- **Account or operational secret (`accounts/<id>/...`):** Missing or undecryptable account secrets during an authorized tool call return `ALLOW + ERROR` with `error.type: "secret_unavailable"` and sanitized `error.detail` (never echoing secret bytes or `gpg` stderr).

## Consequences

### Positive

- **Explicit plaintext acknowledgement.** Operators cannot deploy unencrypted `file_dir` secrets accidentally without setting `acknowledge_plaintext: true`.
- **Clean pre- vs. post-handshake error boundary.** Caller auth failures stay `auth_failed`, while broken account GPG/file secrets surface `secret_unavailable`.
- **Support for non-email IMAP usernames.** Accounts like `id: accounting@company.com, user: mail2223` authenticate cleanly while keeping email-shaped policy IDs.

### Negative

- Existing `accounts.yaml` configurations using `file_dir` must add `acknowledge_plaintext: true`.

### Neutral

- OAuth2 PKCE bootstrap, XOAUTH2 refresh, and durable `needs_rebootstrap` health are governed by [ADR 0059](0059-oauth2-pkce-and-durable-account-health.md).

## Security Implications

- **Attack surface.** Atomic temp-file writes prevent truncated secret files; `gpg` runs without a shell.
- **Trust boundaries.** Keeps raw credentials strictly inside `SecretStore` consumers (`auth`, `imap_core`, `audit` HMAC key).
- **Data exposure.** Secret values, passphrases, and `gpg` stderr lines never appear in responses, logs, traces, or audit entries.
- **Failure modes.** Missing `acknowledge_plaintext: true` or `env_var + persist_all` refuses startup; missing/undecryptable secrets fail closed.
- **Auditability.** Handshake failures emit `auth_failed`; account secret failures emit `ALLOW + ERROR` outcomes with `error_type: "secret_unavailable"` ([ADR 0048](0048-pre-effect-audit-and-test-boundary.md)).

## Alternatives Considered

- **Drop `acknowledge_plaintext` from `file_dir`.** Rejected: silent plaintext secret storage on shared hosts is an avoidable footgun.
- **In-process Python OpenPGP cryptography.** Rejected: delegating to OS `gpg` avoids maintaining custom cryptographic keyrings in Python.
- **OS desktop keyring (`libsecret` / Keychain) as primary backend.** Rejected: unavailable in headless containers and Kubernetes sidecar pods ([ADR 0051](0051-loopback-kubernetes-sidecar-topology.md)).

## References

- Supersedes [ADR 0011](0011-pluggable-secret-store-backend.md).
- [ADR 0042](0042-cryptographic-and-wal-provenance.md) — `provenance_hmac_key` secret entry.
- [ADR 0059](0059-oauth2-pkce-and-durable-account-health.md) — OAuth2 PKCE bootstrap and token caching.
