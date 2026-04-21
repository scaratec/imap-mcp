# ADR 0006: Cross-Account Move via Saga, Native MOVE Within Account

- **Status:** Accepted
- **Date:** 2026-04-20
- **Deciders:** Randy Nel Gupta

## Context

A primary requirement of this project is that callers can move messages
between folders, including **across different IMAP accounts**. The user
must be able to trust this operation in the face of crashes, network
partitions, and server errors — messages must not silently disappear.

IMAP itself gives us very different guarantees depending on whether the
move is within one account or across two:

- **Intra-account:** RFC 6851 `MOVE` is atomic on the server. If it
  completes, the message is in the target folder and gone from the
  source. If it fails, the source is unchanged. No client-side work is
  required to make this safe.
- **Cross-account:** There is no standard cross-server primitive. A move
  decomposes into `FETCH` from source, `APPEND` to target, `STORE
  \Deleted` + `EXPUNGE` on source — three independent operations against
  two different servers. Any step may succeed while subsequent steps
  fail, leaving the system in a mixed state.

Doing nothing would leave cross-account correctness to the caller — in
this system, an LLM — which is exactly the kind of trust boundary the
server is designed to enforce.

True exactly-once semantics against unco­ordinated mail servers is
not achievable. The best achievable correctness property is
*at-least-once with no message loss and deterministic deduplication on
recovery*.

## Decision

The server offers a single `move` (and `copy`) tool whose semantics
differ by target:

- **Intra-account move:** executed as a single RFC 6851 `MOVE`. If the
  server does not advertise the `MOVE` extension, fall back to `COPY +
  STORE \Deleted + EXPUNGE` on the same connection, ordered
  appropriately. No WAL, no saga.

- **Cross-account move:** executed as a **saga** with a durable
  write-ahead log. The steps are:

  ```
  1. BEGIN(tx)        — assign tx_id, persist intent in WAL
  2. FETCH source     — RFC822 bytes + INTERNALDATE + flags; compute
                        content hash, persist in WAL
  3. APPEND target    — using fetched bytes, flags, INTERNALDATE
                        (UIDPLUS → capture assigned UID if available)
  4. VERIFY           — SEARCH target for Message-ID (or fallback
                        query) to confirm presence; persist "staged"
  5. DELETE source    — STORE \Deleted + EXPUNGE (or native MOVE to
                        a hidden local Trash if supported)
  6. COMMIT(tx)       — WAL marks "committed"
  ```

  Crash recovery on startup scans the WAL for non-terminal transactions
  and resumes from the last committed step, relying on the idempotency
  key ([ADR 0008]) to avoid re-APPENDing a message that already made it
  to the target.

Both variants are exposed through the same MCP tool; the switch is
transparent to the caller except that cross-account `move` returns a
`tx_id` and a `get_transaction_status` tool allows polling its state.

## Consequences

### Positive

- **Callers see a single abstraction** and the same transparency
  contract ([ADR 0017]), while the server honours the guarantee each
  leg can provide.
- **Intra-account moves pay no overhead.** They are a single IMAP
  command and benefit directly from server-side atomicity.
- **Cross-account moves survive crashes.** A crash between APPEND and
  DELETE is recoverable to a consistent state.
- **The guarantee is honest.** We commit to at-least-once, no loss,
  deterministic dedup — not a stronger claim we cannot make.

### Negative

- **Short-lived duplicates exist.** Between successful APPEND to the
  target and successful EXPUNGE at the source, the message is in both
  places. This window is usually seconds, sometimes longer on a failing
  server. Consumers of the target mailbox must tolerate this.
- **Operator intervention is possible.** A persistent target-server
  outage leaves transactions in `staged` state. They do not commit and
  do not roll back; an operator must decide (retry later, or reverse
  the APPEND manually).
- **Cross-account moves are slower than intra-account.** Three round
  trips to two servers, plus WAL fsyncs. Measured in low hundreds of
  milliseconds rather than tens. Acceptable for the use case.

### Neutral

- The WAL is a separate concern addressed by [ADR 0007]. The
  idempotency key by [ADR 0008]. This ADR fixes only the split between
  native and saga paths.

## Security Implications

- **No message loss by construction.** The source is never deleted
  until the target has confirmed possession. A bug, crash, or kill -9
  leaves the source intact.
- **Short-lived duplicates are a compliance consideration.** If a
  downstream system counts messages for reporting, it must dedupe by
  Message-ID or accept small transient over-counts. This must be
  documented in the operator manual.
- **Recovery attempts are bounded and logged.** A transaction cannot
  retry forever; it records each attempt in the WAL, and after a
  configurable threshold it moves to `needs_operator` and stops. This
  prevents a pathological retry loop from amplifying an attack
  (message flooding, auth lockouts).
- **Source-credential exposure is minimized.** Fetch uses the source
  account's credentials, APPEND uses the target's. The two credential
  stores are never mixed.
- **Audit records every step.** Beginning, staged, committed, and
  failed transitions appear in the audit log with the `tx_id`
  ([ADR 0021]). A forensic reviewer can reconstruct a full saga
  trajectory without reading the WAL itself.

## Alternatives Considered

- **Refuse cross-account moves; require the caller to orchestrate.**
  Rejected. The caller is an LLM or an agent driven by one; they are
  exactly the wrong place to implement transactional discipline.
- **Best-effort copy-then-delete without WAL.** Rejected. A crash
  between the two operations produces either permanent duplicates (with
  no cleanup record) or, with reversed order, permanent data loss.
  Either is unacceptable for mail content that may be legally or
  financially significant.
- **Source-delete-first ("optimistic").** Rejected as the mirror of the
  above: any APPEND failure with the source already deleted is
  unrecoverable.
- **Two-phase commit across unrelated IMAP servers.** Not possible —
  the protocol has no prepare phase. A homebrew 2PC would require a
  cooperating target, which we cannot assume.
- **Offer separate `intra_move` and `cross_move` tools.** Rejected;
  that leaks an implementation concern into the tool surface and
  creates caller decision pressure that the server can make internally.

## References

- RFC 6851 — IMAP `MOVE` extension.
- RFC 4315 — IMAP `UIDPLUS` extension, used to capture target UID.
- [ADR 0007] — WAL storage backend.
- [ADR 0008] — idempotency key design.
- [ADR 0017] — transparency contract that references `tx_id`.
- [ADR 0021] — audit records for saga transitions.

[ADR 0007]: 0007-sqlite-as-wal-store.md
[ADR 0008]: 0008-idempotency-via-message-id-and-hash.md
[ADR 0017]: 0017-response-transparency-for-filtered-data.md
[ADR 0021]: 0021-audit-log-format.md
