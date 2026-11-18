# ADR 0049: Single-Caller Instance Boundary and Validated Configuration

- **Status:** Accepted
- **Date:** 2026-11-17
- **Deciders:** Randy Nel Gupta

## Context

[ADR 0014](0014-policy-as-git-versioned-yaml.md) and [ADR 0015](0015-caller-identity-and-authentication.md) allowed `callers.yaml` to list multiple callers per server instance while caches and state assumed one tenant, and configuration loading lacked three safeguards:

1. Multi-caller `callers.yaml` files started without error despite single-caller operational requirements.
2. Missing `policies/` directories or unresolved caller policy names started cleanly and silently denied all calls.
3. Sequential file reads across Kubernetes `..data` symlinks risked hybrid multi-generation loads, and leading `~` in paths was not expanded.

## Decision

We enforce **strictly one configured caller per server instance**, **complete cross-file referential integrity with `~` expansion**, and **consistent single-generation configuration loading** at startup and `SIGHUP` reload.

### 1. Single-caller boundary and session authentication

- `callers.yaml` must define **exactly one** caller entry; zero callers, two or more callers, or a caller missing its `auth` block fails startup fatally (and rejects `SIGHUP` reload).
- `stdio_trusted` is permitted only on `stdio` transport and requires `IMAP_MCP_CALLER_ID` to match the configured `caller.id` (`auth_failed` otherwise); configuring `stdio_trusted` on `http` is fatal at startup.
- `shared_token` (required on `http`, optional on `stdio`) verifies `Authorization: Bearer <token>` against `token_secret_ref` in constant time.
- Session identity is immutable after `Initialize`: a second `Initialize` or mid-session caller switch on an established session is rejected (`identity_immutable`).

### 2. Cross-file validation, `~` expansion, and atomic `..data` generation snapshot

At startup (fatal on error) and on `SIGHUP` (retaining last known-good state and auditing `policy_reload_rejected` on error), the loader:

1. Verifies that if `config_dir/..data` exists as a symlink, its target remains identical before and after reading all YAML files (retrying up to 3 times if kubelet swapped mid-read, failing closed if still unstable).
2. Validates that `policies/<policy>.yaml` exists with matching internal `name`, every policy-referenced account exists in `accounts.yaml`, every configured filesystem path expands `~` to an absolute path, and all mode/rule/capability invariants ([ADR 0040](0040-message-centric-policy-and-gmail-composition.md), [ADR 0041](0041-direction-aware-rule-grammar-and-provenance.md), [ADR 0045](0045-write-visibility-floors-and-flow-monotonicity.md)) hold.
3. On `SIGHUP`, swaps `(Configuration, PolicyDecisionPoint)` atomically, drains connection pools for removed or credential-changed accounts ([ADR 0055](0055-bounded-per-account-connection-pool.md)), and transitions any active OAuth account whose configured `oauth_scope` changed to durable `needs_rebootstrap` ([ADR 0059](0059-oauth2-pkce-and-durable-account-health.md)).

## Consequences

### Positive

- **Structural tenant isolation.** One process = one caller principal across memory, caches, WAL leases, and transport.
- **No silent policy-less startups.** Missing policy files, typos, and unexpanded `~` paths fail fast with clear diagnostics.
- **Torn ConfigMap reloads eliminated.** `..data` generation checks guarantee self-consistent snapshots.

### Negative

- Distinct agent roles on one host use separate `IMAP_MCP_CONFIG_DIR` overlays (each with its own single-entry `callers.yaml`).

### Neutral

- Policies remain Git-versioned YAML reloaded via `SIGHUP` or pod rollout.

## Security Implications

- **Attack surface.** Eliminates cross-caller in-memory cache sharing and mid-session identity switching.
- **Trust boundaries.** Binds every server process to a single authenticated `caller_id`.
- **Data exposure.** Prevents hybrid ConfigMap reload states from transiently combining mismatched caller and policy files.
- **Failure modes.** Startup validation errors exit non-zero before binding any listener; `SIGHUP` errors preserve previous enforcement.
- **Auditability.** `auth_failed`, `identity_immutable`, `policy_reload_ok`, and `policy_reload_rejected` events are recorded in the hash chain ([ADR 0048](0048-pre-effect-audit-and-test-boundary.md)).

## Alternatives Considered

- **Multi-caller HTTP instances with partitioned state.** Rejected: adds cross-tenant complexity with zero scaling benefit under single-replica sidecars ([ADR 0051](0051-loopback-kubernetes-sidecar-topology.md)).
- **Default missing policy files to empty policy.** Rejected: caused silent total-deny operational outages indistinguishable from empty mailboxes.
- **Automatic filesystem-watch (`inotify`) reloads.** Rejected: races against partial manual file edits compared to explicit `SIGHUP`.

## References

- Supersedes [ADR 0014](0014-policy-as-git-versioned-yaml.md) and [ADR 0015](0015-caller-identity-and-authentication.md).
- [ADR 0051](0051-loopback-kubernetes-sidecar-topology.md) — Kubernetes sidecar deployment.
- [ADR 0055](0055-bounded-per-account-connection-pool.md) — connection pool drain on reload.
- [ADR 0059](0059-oauth2-pkce-and-durable-account-health.md) — OAuth scope change handling.
