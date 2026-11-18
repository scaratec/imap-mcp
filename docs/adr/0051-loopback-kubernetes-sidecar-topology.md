# ADR 0051: Loopback Kubernetes Sidecar Topology

- **Status:** Accepted
- **Date:** 2026-11-17
- **Deciders:** Randy Nel Gupta

## Context

Running `imap-mcp` under the same OS user as a shell-enabled desktop agent cannot protect policy files or secrets from direct host file access ([LIM 0019](../limitations/0019-policy-integrity-requires-out-of-process-trust-boundary.md)). Deploying `imap-mcp` as a container sidecar establishes an OS and mount-namespace boundary, provided four pitfalls from [ADR 0029](0029-kubernetes-sidecar-deployment-topology.md) are avoided:

1. Binding `0.0.0.0` exposes `/mcp` to the cluster network, whereas binding `127.0.0.1` requires in-container `exec` probes instead of node-originated `httpGet` probes.
2. Mounting `/sink` read-write in the agent container permitted agent-side file tampering.
3. Default `StatefulSet` PVC retention deletes audit chains on workload deletion, and `RollingUpdate` briefly runs two writers against one `ReadWriteOnce` volume.
4. `shareProcessNamespace: true` exposes `/proc/<pid>/root` across containers.

## Decision

We deploy containerized `imap-mcp` as **one loopback-bound (`127.0.0.1`) sidecar container per agent pod inside a single-replica `Recreate` `Deployment`, with read-only `/config`, `/secrets`, and agent `/sink` mounts, a standalone `ReadWriteOnce` state PVC, `shareProcessNamespace: false`, and an unauthenticated `GET /healthz` liveness route**.

### 1. Pod mount and namespace isolation

- `agent` container mounts `/sink` (`emptyDir`) with `readOnly: true` and connects to `http://127.0.0.1:8080/mcp` using `shared_token` ([ADR 0049](0049-single-caller-boundary-and-validated-loading.md)).
- `imap-mcp` container (fixed non-root UID) mounts `/config` (`ConfigMap`, `readOnly: true`), `/secrets` (`Secret`, `readOnly: true`), `/state` (standalone `ReadWriteOnce` PVC for `audit/` and `wal.db`), and `/sink` (`emptyDir`, read-write via [ADR 0047](0047-per-caller-symlink-safe-attachment-delivery.md)).
- `shareProcessNamespace` remains `false`.

### 2. Loopback HTTP listener and `GET /healthz`

- Beside `/mcp`, the HTTP server serves only `GET /healthz`:
  - Returns HTTP `200` `{"status": "ok"}` when configuration is loaded and `/state/audit` is writable.
  - Returns HTTP `503` `{"status": "unavailable"}` when `/state/audit` becomes unwritable.
  - Emits zero account, caller, path, or version fields, writes zero audit records, and returns `404` on non-GET methods and all other routes.
- Kubernetes liveness checks invoke `http://127.0.0.1:8080/healthz` via an `exec` probe inside `imap-mcp`.

## Consequences

### Positive

- **Kernel-enforced policy, secret, and sink isolation.** Mitigates [LIM 0019](../limitations/0019-policy-integrity-requires-out-of-process-trust-boundary.md) for containerized workloads.
- **Preserved audit trail and unwritable-state detection.** Standalone PVC + `Recreate` prevents concurrent writers, and `/healthz` surfaces unwritable `/state/audit` via `503`.

### Negative

- Per-pod audit chains ([LIM 0020](../limitations/0020-audit-chain-fragments-across-per-agent-instances.md)) and out-of-pod OAuth bootstrap ([LIM 0021](../limitations/0021-oauth-bootstrap-and-token-persistence-not-possible-in-pod.md)).

### Neutral

- Workstation `stdio` remains available as a documented convenience mode.

## Security Implications

- **Attack surface.** Loopback binding keeps `/mcp` and `/healthz` unreachable from other pods or nodes.
- **Trust boundaries.** Separates agent and mediator across container, filesystem, and PID namespaces.
- **Data exposure.** `/healthz` returns only `{"status": "ok"}` or `{"status": "unavailable"}`.
- **Failure modes.** Broken projected ConfigMaps fail startup before `/healthz` serves `200`; unwritable audit storage flips `/healthz` to `503`.
- **Auditability.** Probe traffic never pollutes the audit chain; MCP HTTP calls log `http:127.0.0.1:<port>` ([ADR 0048](0048-pre-effect-audit-and-test-boundary.md)).

## Alternatives Considered

- **Multi-tenant cluster `Service` on `0.0.0.0`.** Rejected: widens network exposure and requires cross-pod attachment transport for zero scaling gain.
- **Reload sidecar with `shareProcessNamespace: true`.** Rejected: exposes `/proc/<pid>/root` across containers.
- **Read-write `/sink` mount in the agent container.** Rejected: the agent only reads attachments; `readOnly: true` eliminates agent-side tampering.

## References

- Supersedes [ADR 0029](0029-kubernetes-sidecar-deployment-topology.md).
- [ADR 0046](0046-content-verified-sagas-and-wal-leases.md), [ADR 0047](0047-per-caller-symlink-safe-attachment-delivery.md), [ADR 0048](0048-pre-effect-audit-and-test-boundary.md), [ADR 0049](0049-single-caller-boundary-and-validated-loading.md).
- [LIM 0019](../limitations/0019-policy-integrity-requires-out-of-process-trust-boundary.md), [LIM 0020](../limitations/0020-audit-chain-fragments-across-per-agent-instances.md), [LIM 0021](../limitations/0021-oauth-bootstrap-and-token-persistence-not-possible-in-pod.md).
