# LIM 0020: Audit chain fragments across per-agent sidecar instances

- **Status:** Accepted
- **Resolution intent:** permanent (architectural boundary)
- **Date proposed:** 2026-08-25
- **Date approved:** 2026-11-17
- **Proposed by:** Architecture review (Kubernetes sidecar assessment)
- **Approved by:** Randy Nel Gupta
- **Scope:** server/src/imap_mcp/audit.py, deployment/kubernetes
- **Related ADRs:** [ADR-0048](../adr/0048-pre-effect-audit-and-test-boundary.md), [ADR-0049](../adr/0049-single-caller-boundary-and-validated-loading.md), [ADR-0051](../adr/0051-loopback-kubernetes-sidecar-topology.md)
- **Related Guidelines:** BDD Guidelines §1.2 (observable behaviour), §4.3 (persistence validation)

## Resolution intent

`permanent`. Under [ADR-0049](../adr/0049-single-caller-boundary-and-validated-loading.md)
and [ADR-0051](../adr/0051-loopback-kubernetes-sidecar-topology.md), each
containerized agent pod runs its own single-replica `Recreate` `Deployment`
with its own standalone `ReadWriteOnce` `/state` PVC. Consequently, N
containerized agents produce N independent daily SHA-256 audit chains
(unlike local workstation `stdio` processes sharing one `~/.config/imap-mcp/audit`
directory via `.audit.lock` per [ADR-0048](../adr/0048-pre-effect-audit-and-test-boundary.md)).

## Context

Each sidecar pod isolates its state volume (`wal.db` and `audit/`) from
sibling pods. While every pod's chain is internally tamper-evident, losing
an entire PVC removes that one agent's chain without breaking any sibling
pod's chain.

## Nature of the weakness

Deleting or losing a single agent's standalone `/state` PVC erases that
agent's local audit history without invalidating hashes in other agents'
PVCs unless daily root hashes were anchored externally.

## Why the clean solution is not chosen

Sharing a single `ReadWriteMany` POSIX-locked filesystem across pods or
embedding a networked log-shipping consensus client inside `imap-mcp` adds
cluster storage coupling and availability dependencies disproportionate to
the mediator's scope ([ADR-0048](../adr/0048-pre-effect-audit-and-test-boundary.md)).

## Mitigations in place

- **Standalone `ReadWriteOnce` PVC + `strategy: Recreate` ([ADR-0051](../adr/0051-loopback-kubernetes-sidecar-topology.md)):**
  Survives `Deployment` deletion (`kubectl delete deployment`), unlike
  default `StatefulSet` `volumeClaimTemplates`.
- **Scrubbed `external_root_hook` ([ADR-0048](../adr/0048-pre-effect-audit-and-test-boundary.md)):**
  Pushes each UTC day's `final_hash` off-pod at day seal so an unlinked PVC
  is provably detectable against anchored roots.
- **Unwritable `/state/audit` detection ([ADR-0051](../adr/0051-loopback-kubernetes-sidecar-topology.md)):**
  Flips `GET /healthz` to `503` and aborts writes at the pre-effect
  `intent` stage ([ADR-0048](../adr/0048-pre-effect-audit-and-test-boundary.md)).

## Residual risk

Entries written on the current unsealed UTC day before a catastrophic PVC
loss without real-time log shipping are lost up to the previous UTC
midnight `external_root_hook` anchor.

## Triggers for revisit

- Compliance requirement mandating synchronous off-pod audit replication
  before write acknowledgement.

## References

- [ADR-0048](../adr/0048-pre-effect-audit-and-test-boundary.md), [ADR-0049](../adr/0049-single-caller-boundary-and-validated-loading.md), [ADR-0051](../adr/0051-loopback-kubernetes-sidecar-topology.md).
- `docs/ops/kubernetes-sidecar-deployment.md`.
