# ADR 0007: SQLite as Write-Ahead Log Store

- **Status:** Accepted
- **Date:** 2026-04-20
- **Deciders:** Randy Nel Gupta

## Context

[ADR 0006] introduces a write-ahead log for cross-account move sagas.
The WAL must survive process crashes, ordered-write with durability, and
support idempotent recovery. The storage backend choice determines
deployment, operational concerns, and failure modes.

Three realistic options:

- A local embedded database (SQLite).
- An external relational database (PostgreSQL).
- A hand-rolled append-only file (JSONL with `fsync`, for example).

The server's typical deployment is single-instance — one process per
user or per small team. Horizontal scaling is not a design goal; if it
becomes one, the WAL is a migration concern separate from the PDP and
the IMAP core.

## Decision

The WAL is stored in a local **SQLite** database, using SQLite's own
WAL journal mode for crash safety.

Default location follows the XDG Base Directory Specification:

```
$XDG_STATE_HOME/imap-mcp/wal.db
(fallback: ~/.local/state/imap-mcp/wal.db)
```

Schema sketch (details belong to the implementation, not this ADR):

- `transactions(tx_id PK, status, created_at, committed_at,
  caller_id, src_account, src_folder, src_uid, dst_account, dst_folder,
  message_id, content_hash, target_uid, retry_count, last_error)`
- `transaction_events(tx_id FK, step, timestamp, outcome, detail)` —
  append-only per-transaction audit inside the WAL, separate from the
  system audit log in [ADR 0021].

Access is single-process; SQLite's default locking is sufficient. The
WAL database file is owned by the server process user, mode `0600`.

## Consequences

### Positive

- **Zero deployment.** No external service, no TCP port, no network
  auth. The server starts with a file.
- **True ACID locally.** SQLite's WAL mode gives ordered durability per
  commit with `PRAGMA synchronous=FULL`. Recovery after kill -9 is
  well-defined.
- **Simple backup.** A live SQLite database can be backed up with
  `.backup` (hot) or by copying the directory after an orderly server
  stop. Kopia-style snapshotting handles it as a regular file.
- **Observable.** Operators can open the database with `sqlite3`
  outside the server process for read-only inspection of pending
  transactions.
- **Small binary footprint.** SQLite is in the Python standard library;
  no extra dependency.

### Negative

- **Single-writer.** Only one process can actively write to the WAL.
  If horizontal scaling is ever desired, the WAL becomes the
  coordination point and needs replacement. That is a future migration,
  not a present problem.
- **No cross-process notifications.** A separate admin CLI that wants
  to see progress polls the database rather than subscribing to
  events. Acceptable for our use cases.
- **File-system dependence.** WAL integrity relies on the underlying
  file system honouring `fsync` correctly. On Linux with ext4/xfs this
  is solved; on exotic or networked file systems (NFS, some overlay
  setups) operators must verify.

### Neutral

- SQLite's WAL mode is conceptually distinct from our *application-
  level* WAL (the saga transaction log). Both are called "WAL" in
  different layers. Documentation must distinguish them.

## Security Implications

- **At-rest encryption is the file system's job.** SQLite itself is
  not encrypted by default. Deployments that require at-rest encryption
  rely on LUKS (or equivalent) beneath the state directory. The server
  neither implements nor demands SQLCipher; adding it is a future ADR
  if required.
- **WAL content sensitivity.** Transactions record:
  - message IDs and content hashes (non-sensitive as identifiers),
  - source/target account names (configured, non-sensitive),
  - caller IDs (pseudonymous),
  - no message bodies, no headers beyond what is necessary for
    idempotency verification.
  A WAL leak reveals *who moved what between which mailboxes*, not the
  message contents. This matches the sensitivity class of the audit log
  and should be protected similarly (0600, same directory hygiene).
- **Retention.** Transactions in terminal state (`committed`,
  `aborted`, `needs_operator`-resolved) are kept for a bounded retention
  period, then purged. Default is 90 days; configurable. Non-terminal
  transactions are never auto-purged.
- **Recovery-attempt bounding.** A transaction that fails repeatedly
  cannot consume resources forever; the `retry_count` column caps
  attempts and moves the transaction to `needs_operator` after the
  configured threshold ([ADR 0006]).
- **Integrity.** SQLite detects bit-rot via `PRAGMA
  integrity_check` and, optionally, per-row HMACs. V1 does not add
  per-row HMACs; the WAL is protected by file-system integrity and
  backups.

## Alternatives Considered

- **PostgreSQL.** Rejected for V1. Adds an external service, network
  authentication, backup procedure, and operational surface that is
  unjustified for a single-instance server. PostgreSQL remains the
  right answer if horizontal scaling or multi-tenant hosting becomes
  real; the schema is portable.
- **Append-only JSONL file with `fsync`.** Rejected. Recovery semantics
  (finding the last good record, partial-write detection,
  transaction-status updates) would require reimplementing significant
  portions of a database. The cost of the hand-rolled option is higher
  than the cost of SQLite.
- **In-memory state + periodic snapshot.** Rejected. Any crash window
  larger than the snapshot interval risks losing knowledge of a pending
  APPEND to the target, producing either duplicate APPEND on recovery
  or (worse) a silent loss if we mitigate duplicates by dropping
  unknown-state transactions.
- **The existing imap-agent PostgreSQL instance.** Rejected for V1:
  [ADR 0020] preserves imap-agent as a future client of this server,
  not a component; coupling this server to the agent's storage would
  invert the dependency.

## References

- SQLite WAL mode: <https://www.sqlite.org/wal.html>
- [ADR 0006] — the saga mechanism this WAL supports.
- [ADR 0008] — idempotency keys stored in the WAL.
- [ADR 0020] — imap-agent dependency direction.
- [ADR 0021] — the system audit log, distinct from this WAL.

[ADR 0006]: 0006-cross-account-move-via-saga.md
[ADR 0008]: 0008-idempotency-via-message-id-and-hash.md
[ADR 0020]: 0020-imap-agent-as-future-client.md
[ADR 0021]: 0021-audit-log-format.md
