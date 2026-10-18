# LIM 0020: Audit chain fragments across per-agent instances

- **Status:** Proposed
- **Resolution intent:** permanent (architectural boundary)
- **Date proposed:** 2026-08-25
- **Date approved:** — (pending owner approval)
- **Proposed by:** the agent (k8s suitability assessment)
- **Approved by:** — (pending; project owner)
- **Related ADRs:** [ADR-0029](../adr/0029-kubernetes-sidecar-deployment-topology.md),
  [ADR-0021](../adr/0021-audit-log-format.md),
  [ADR-0022](../adr/0022-audit-retention-and-access-model.md),
  [ADR-0007](../adr/0007-sqlite-as-wal-store.md),
  [ADR-0032](../adr/0032-shared-audit-chain-across-local-instances.md)
- **Related Guidelines:** BDD Guidelines §4.3 (silent failures),
  §1.2 (observable behaviour)

## Resolution intent

`permanent`. The fragmentation is a direct consequence of two decisions
that are themselves sound and are not up for revision here: the audit
log is an append-only hash chain bound to one local audit directory
([ADR 0021], [ADR 0032]), and the deployment unit is one server instance
per agent with its own volume ([ADR 0029] §3). Given both, there cannot
be one chain. A single chain across agents would require either a shared
volume with cross-pod locking — which [ADR 0029] rules out — or a networked audit
sink, which [ADR 0022] explicitly rejected in favour of files plus a log
shipper. No paydown is owed inside the server; the reconciliation work
belongs to whatever aggregates the shipped records.

## Context

[ADR 0021] defines the audit record format as a per-day JSONL file whose
records are linked by `prev_hash`, with a `final_hash` written into the
`eof_day` record at the UTC day roll. [ADR 0022] adds retention tiers
and states that the server does not ship logs: operators tail the JSONL
files with Vector, Fluent Bit or Promtail.

The writer derives `seq`, `prev_hash` and the target day file from the
files in the audit directory on every write, under an exclusive file
lock ([ADR 0032]). Every process that sees the same local directory
therefore extends the same chain.

In the local deployment this holds even with several instances: an MCP
client that starts one stdio server per session points all of them at
one state directory, and they share one chain covering every call every
caller ever made.

[ADR 0029] changes the deployment unit. Each agent pod carries its own
sidecar and its own `ReadWriteOnce` state volume. There is no longer one
chain covering the deployment; there are N chains, one per agent, each
internally verifiable and none of them relatable to the others by any
mechanism the server provides.

## Nature of the weakness

Precisely: **the hash chain proves the integrity of one instance's
record sequence and nothing about the completeness of the record set
across instances. A whole instance's chain can be deleted, and no
surviving artefact is inconsistent.**

Conditions under which the weakness is exposed:

- More than one agent workload is deployed under [ADR 0029], **and**
- an aggregated view of "what did all agents do" is required — for a
  compliance question, an incident reconstruction, or a periodic
  review, **and**
- the aggregation is performed over shipped records rather than over
  the on-volume files.

Observable consequences, in ascending severity:

1. **No total order.** Two agents acting on the same mailbox within the
   same second produce records in two chains with independent `seq`
   counters. Reconstructing the interleaving requires trusting record
   timestamps, which are not part of what the chain protects.
2. **Per-instance verification only.** A reviewer can verify chain *i*
   in isolation. There is no artefact that binds chain *i* to chain *j*,
   so "these are all the chains" is an assertion about the cluster's
   inventory, not something the audit format attests.
3. **Silent whole-chain loss.** Deleting an agent's `PersistentVolume`
   — by removing the workload, by a `StatefulSet` teardown with
   `persistentVolumeClaimRetentionPolicy: Delete`, or by a storage-class
   reclaim — destroys that agent's entire audit history. Every remaining
   chain still verifies. Nothing reports a gap, because nothing knows
   the chain existed.

The third consequence is the one that matters. The chain is designed to
make tampering *within* a record set detectable; it offers no defence
against removal of a record set, and the per-agent topology multiplies
the number of independently removable record sets by the number of
agents.

## Why the clean solution is not chosen

The candidate clean solutions each contradict a standing decision or
relocate the problem without shrinking it:

- **One shared audit volume across instances.** Rejected. Locally,
  instances on one directory already share one chain through an
  advisory `flock` ([ADR 0032]). Across pods that would need a
  `ReadWriteMany` volume on a network file system, where `flock`
  semantics are not reliable, and it would reintroduce the cross-pod
  coupling [ADR 0029] §3 removes.
- **A networked audit sink the server writes to directly.** This is
  precisely what [ADR 0022] considered and rejected ("Write to syslog /
  journald instead of files. Rejected"), and adding an HTTP or gRPC
  audit target would put the audit path's availability in the request
  path of every tool call — a tool call that cannot be audited must be
  denied, so an unreachable sink becomes a total outage.
- **A cluster-wide chaining service that re-chains shipped records.**
  Technically viable and genuinely useful, but it is a second system
  with its own storage, availability and integrity story. It is not
  something `imap-mcp` can contain, and building it inside the server
  would invert the [ADR 0022] decision that shipping is the operator's
  concern.
- **Returning to one shared server instance.** Would restore the single
  chain, at the cost of reopening every multi-caller concern [ADR 0029]
  §3 closes. The audit-continuity gain does not justify reintroducing
  cross-caller exposure.

The reconciliation genuinely belongs one layer up, in the aggregator
that [ADR 0022] already assumes exists.

## Mitigations in place

- **Each chain remains individually verifiable.** Nothing about the
  per-agent topology weakens the `prev_hash` / `final_hash` construction
  within one instance. Tampering inside a surviving chain is as
  detectable as it was.
- **`caller_id` is in every record** ([ADR 0015], [ADR 0021]), and under
  [ADR 0029] §3 each instance serves exactly one caller. Chain identity
  and caller identity therefore coincide, so a shipped record set is
  unambiguously attributable even after the chains are merged in an
  aggregator.
- **The state volume is `ReadWriteOnce` and the workload is pinned to
  one replica** ([ADR 0029] §6). The failure mode of two writers in one
  directory cannot arise from a rolling update, because
  `RollingUpdate` is documented as a configuration error.
- **[ADR 0022]'s `external_root_hook`** fires at each day roll and can
  push that day's `final_hash` off-host. An operator who wires it gets
  an off-volume witness per instance per day, which converts
  consequence 3 from "silent" to "detectable": a chain whose root
  hashes were witnessed but whose volume is gone leaves evidence
  behind. This is the single most effective mitigation available and
  the operator guide names it as recommended, not optional.
- **`docs/ops/kubernetes-sidecar-deployment.md`** states the volume
  retention requirement explicitly and calls out
  `persistentVolumeClaimRetentionPolicy` as the setting that decides
  whether an agent's audit history survives its workload.

## Residual risk

An operator scales an agent workload down to zero and deletes it, the
`StatefulSet`'s retention policy reclaims the volume, and that agent's
entire audit history — every mailbox it read, every message it moved,
every attachment it fetched, for the whole life of the deployment — is
gone. Every other chain verifies cleanly. A subsequent audit review
concludes the record set is intact, because every artefact it can see
is intact. The absence is invisible unless someone independently knows
that the agent existed and compares against a cluster inventory.

Worst-realistic case: a GoBD- or GDPR-relevant question is asked about
mail handling six months after a short-lived agent workload was removed
during a cluster migration. The records that would answer it were on a
reclaimed volume; the remaining chains are complete, consistent, and
silent about the gap. Without a wired `external_root_hook`, no artefact
distinguishes "that agent never ran" from "that agent's history was
deleted".

## Triggers for revisit

- A second `imap-mcp` sidecar is deployed in the same cluster, making
  the fragmentation real rather than theoretical.
- Any deployment where the audit log is relied on for a regulatory
  obligation (GoBD, GDPR Art. 30) rather than for operational
  forensics; the aggregation gap must then be closed at the aggregator
  and the closure documented.
- [ADR 0029] is superseded by a shared-instance topology, which would
  restore a single chain and make this record `Resolved`.
- An incident occurs in which a missing chain is discovered after the
  fact, which would argue for escalating `external_root_hook` from
  recommended to required.
- A future audit-verification CLI (not in V1 per [ADR 0022]) gains a
  multi-chain mode; the record should then be re-scoped to what that
  tool cannot do.

## References

- [ADR-0029](../adr/0029-kubernetes-sidecar-deployment-topology.md) —
  the topology that produces one chain per agent.
- [ADR-0021](../adr/0021-audit-log-format.md) — the hash chain whose
  single-writer property is the root constraint.
- [ADR-0022](../adr/0022-audit-retention-and-access-model.md) — files
  plus log shipper, `external_root_hook`, no networked sink.
- [ADR-0007](../adr/0007-sqlite-as-wal-store.md) — the parallel
  single-writer statement for the WAL.
- [LIM-0009](0009-audit-day-roll-deferred.md) — day-roll and retention
  mechanics, Resolved; this record concerns the cross-instance view
  that mechanism does not provide.
- [ADR-0032](../adr/0032-shared-audit-chain-across-local-instances.md) —
  one chain per local audit directory, shared by all instances on it.
- `docs/ops/kubernetes-sidecar-deployment.md` — operator guidance on
  volume retention and root-hash witnessing.
