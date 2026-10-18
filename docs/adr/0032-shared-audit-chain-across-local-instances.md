# ADR 0032: One Audit Chain Shared by Local Server Instances

- **Status:** Proposed
- **Date:** 2026-10-18
- **Deciders:** Randy Nel Gupta

## Context

[ADR 0021] defines the audit log as per-day JSONL files linked by a
SHA-256 hash chain, closed at UTC midnight by an `eof_day` record that
names the day's `final_hash`. [ADR 0022] adds retention and the
`external_root_hook`. Both were written for exactly one writing process
per audit directory, and the writer kept the chain head (`seq`,
`prev_hash`, current day file) in process memory.

In local use that assumption does not hold. An MCP client such as
OpenCode starts one stdio server per session or sub-agent, and all of
them are configured with the same audit directory. Nine concurrent
instances were observed on one workstation. Nothing enforced the single
writer, and the consequences were severe:

- Every start-up appended a further `eof_day` to every past day file
  that already had one, temporarily making sealed files writable. One
  file had accumulated more than 4000 trailers.
- An instance that had written on day D and was still running on day
  D+1 tried to append its own `eof_day` to a file another instance had
  already sealed (mode 0400). The resulting `EACCES` was raised before
  the writer switched to the new day, so every later tool call of that
  instance failed until it was restarted (incident of 2026-10-16).
- Concurrent instances wrote interleaved records with independent
  `seq` counters and `prev_hash` values; the chain verified for none of
  them. A restart within the same day also restarted `seq` at 0.
- OAuth token-refresh and bootstrap records bypassed the writer
  entirely, carried no chain fields, ignored the configured clock and
  were created with the process umask.

## Decision

We let every local server instance that shares an audit directory write
into **one** hash chain, serialised by an exclusive file lock, with the
chain head read from disk on every write.

Mechanism:

1. Each write takes `flock(LOCK_EX)` on `<audit dir>/.audit.lock`
   (plus an in-process lock for threads).
2. Under the lock the writer determines today's file from the UTC
   clock and reads the last chained record of that file. The next
   record gets `seq + 1` and the hash of that line as `prev_hash`.
3. If today's file holds no chained record yet, the writer first seals
   every earlier day file whose last chained record is not `eof_day`,
   invokes the root hook once per sealed file, and chains the new
   record onto the most recent `final_hash`.
4. **A file whose last chained record is `eof_day` is sealed and is
   never opened for writing again**, regardless of its mode. Sealing is
   therefore idempotent; it is performed by whichever instance writes
   first on a new day, or at start-up.
5. Token-refresh and bootstrap records go through the same writer.
6. If the trail cannot be written, the operation fails closed with an
   error whose message starts with `audit trail not writable:` and names
   the cause, and a critical line is logged.

The day boundary is UTC midnight, used for file names and sealing alike.

## Consequences

### Positive

- A long-running instance survives any number of day boundaries and
  any number of sibling instances; no restart is needed.
- Sealed files stay byte-for-byte unchanged after sealing.
- One verifiable chain per audit directory, including token-refresh
  records, as [ADR 0021] intends.
- Audit failures name their cause instead of surfacing as a bare
  `Permission denied` on an unexpected file.

### Negative

- Every audited operation now serialises on a file lock and performs a
  small tail read of today's file. Throughput across instances is
  bounded by fsync latency, which is acceptable for interactive mail
  access.
- `flock` is advisory and local. It does not protect directories on
  network file systems with unreliable lock semantics; the audit
  directory must be on a local file system.
- A writer that stalls while holding the lock blocks all instances'
  audited operations until it proceeds or exits (the kernel releases
  the lock when the process dies).

### Neutral

- Files written by earlier versions keep their duplicate trailers and
  unchained lines; they are not rewritten. A verifier must tolerate
  repeated `eof_day` trailers in files sealed before this change.
- The Kubernetes topology of [ADR 0029] is unaffected: each pod has its
  own volume and therefore its own chain ([LIM-0020]).

## Security Implications

- **Attack surface.** None added. The lock file lives inside the
  audit directory (mode 0700) and is created with mode 0600.
- **Trust boundaries.** Unchanged. All instances run as the same user
  and were already able to write the same directory.
- **Data exposure.** None. Record content and the no-content-leak rule
  of [ADR 0021] are unchanged.
- **Failure modes.** A process killed while holding the lock releases
  it automatically. A truncated last line is not treated as a chain
  record; the chain continues from the last complete record. If the
  clock moves backwards onto an already sealed day, writing fails
  closed rather than reopening the sealed file.
- **Auditability.** Improved: sealed files are immutable in practice,
  token refreshes are chained, and concurrent instances no longer
  produce mutually inconsistent chains.

## Alternatives Considered

- **One chain per instance** (separate sub-directory or file name per
  process). Rejected: produces many short chains whose completeness
  cannot be attested, and leaves an ever-growing set of directories
  for short-lived sub-agent sessions.
- **Refuse to start a second instance on the same directory.**
  Rejected: breaks the normal operation of MCP clients that start one
  server per session.
- **Keep per-process state but recover from `EACCES` on rollover.**
  Rejected: fixes the visible symptom only; interleaved chains,
  repeated sealing and the same-day restart gap remain.

## References

- [ADR 0021](0021-audit-log-format.md) — record format and hash chain.
- [ADR 0022](0022-audit-retention-and-access-model.md) — retention,
  `external_root_hook`.
- [ADR 0023](0023-test-only-control-surface.md) — test hooks, including
  the shared fake clock `IMAP_MCP_FAKE_NOW_FILE`.
- [ADR 0029](0029-kubernetes-sidecar-deployment-topology.md) — per-pod
  deployment.
- [LIM-0020](../limitations/0020-audit-chain-fragments-across-per-agent-instances.md)
  — chain fragmentation across pods.
- BDD: `bdd/features/audit/audit_multi_instance.feature`.

[ADR 0021]: 0021-audit-log-format.md
[ADR 0022]: 0022-audit-retention-and-access-model.md
[ADR 0029]: 0029-kubernetes-sidecar-deployment-topology.md
[LIM-0020]: ../limitations/0020-audit-chain-fragments-across-per-agent-instances.md
