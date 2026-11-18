# ADR 0046: Content-Verified Transfer Sagas

- **Status:** Accepted
- **Date:** 2026-11-17
- **Deciders:** Randy Nel Gupta

## Context

Review of the transfer and WAL subsystem ([ADR 0006](0006-cross-account-move-via-saga.md), [ADR 0007](0007-sqlite-as-wal-store.md), [ADR 0008](0008-idempotency-via-message-id-and-hash.md)) revealed five hazards:

1. **Data loss on `Message-ID` or 4 KiB prefix collision.** Matching destination candidates by `Message-ID` alone (or a 4 KiB prefix hash) deleted the source message even when bytes past offset 4 096 differed.
2. **Collateral deletion via unqualified `EXPUNGE`.** Intra-account `COPY + STORE \Deleted + EXPUNGE` permanently expunged unrelated `\Deleted` messages in the source folder.
3. **Unauthenticated, side-effecting `get_transaction_status`.** Status queries checked no `caller_id` and triggered IMAP recovery writes.
4. **Missing startup recovery and multi-instance races.** Sagas stalled across restarts unless polled, and concurrent local processes lacked SQLite transaction leases or `UIDVALIDITY` guards.
5. **Transient WAL payload retention.** Staged RFC822 bytes needed between `fetched` and `staged` were not scrubbed upon commit.

## Decision

We execute cross-account transfers and draft revisions through a **`0600` SQLite WAL with cross-process leases, full canonical SHA-256 verification before source deletion, `UID EXPUNGE`-only fallback, automatic startup recovery, immediate payload scrubbing, and read-only caller-bound status queries**.

### 1. WAL leases and immediate payload scrubbing

- `wal.db` runs with WAL journaling and full synchronous durability (`0600` in a `0700` state directory).
- `staged_rfc822` holds source bytes only while a transaction is in state `fetched`; transitioning to `staged` or any terminal state (`committed`, `aborted`, `needs_operator`) overwrites `staged_rfc822` with `NULL` in the same SQLite transaction. Terminal metadata rows expire after `retention_days` (default 90).
- Every recovery pass and `update_draft` revision acquires an atomic SQLite lease keyed on `tx_id` (and `(account_id, draft_id)` for draft CAS), preventing duplicate concurrent execution across local stdio instances.

### 2. Full `canonical_sha256` target verification before source delete

- At `FETCH`, the saga records normalized `message_id`, `raw_sha256`, exact `size_bytes`, and `canonical_sha256` (SHA-256 over canonicalized `From`, `To`, `Cc`, `Subject`, `Date`, `Message-ID`, and ordered MIME leaf headers and decoded payloads, excluding provider-prepended trace headers).
- Before deleting the source message, destination candidates (by `APPENDUID`, `HEADER Message-ID`, or `FROM + SENTON` fallback) are fetched in full and compared against `canonical_sha256`:
  - Exactly one candidate matches `canonical_sha256` -> confirmed `staged`, proceed to source delete (leaving any non-matching `Message-ID` neighbours untouched).
  - Pre-`APPEND` (`fetched`) with only non-matching `Message-ID` neighbours -> append the authentic payload, verify the newly appended copy's `canonical_sha256`, and delete source while leaving the non-matching neighbour intact.
  - Multiple identical matching fallback candidates where delivery cannot be disambiguated -> escalate to `needs_operator` (`reason: "ambiguous_target_match"`), keeping source intact.

### 3. Safe intra-account move and `UIDVALIDITY` guard

- Intra-account `move` uses Gmail label swap on Google accounts, RFC 6851 `UID MOVE` when `MOVE` is advertised, `UID COPY` + `UID STORE +FLAGS.SILENT (\Deleted)` + **`UID EXPUNGE <uid>`** when `UIDPLUS` is advertised without `MOVE`, and refuses before mutation with `ALLOW + ERROR` (`unsupported_imap_capability`) when neither extension is advertised.
- UID-taking tools accept optional `uidvalidity` and track observed session `UIDVALIDITY` per `(account, folder)`, failing closed with `ALLOW + ERROR` (`uidvalidity_changed`) on mismatch.

### 4. Automatic startup recovery, unified revocation rule, and read-only status

- On startup, the server claims unleased non-terminal WAL rows for its `caller_id` and runs one bounded recovery pass without blocking MCP handshake readiness:
  - **Unified policy-revocation rule (live & startup):** If a transaction has not yet reached `staged` (`pending`, or `fetched` after current policy revoked transfer permission), it transitions to `aborted` (surfacing `ALLOW + ERROR` `saga_aborted` if synchronous) with source untouched. Once `staged` (verified at target), completion of cleanup (`deleted -> committed`) proceeds under the durable recorded pre-effect `intent` ([ADR 0048](0048-pre-effect-audit-and-test-boundary.md)).
- `get_transaction_status(tx_id)` performs **zero IMAP I/O**, never resumes sagas, and returns opaque `ALLOW + ERROR` (`not_found`) whenever `tx_id` is absent or owned by another `caller_id`.

## Consequences

### Positive

- **Zero silent message loss.** Neither `Message-ID` collisions past 4 KiB, `UIDVALIDITY` shifts, nor unqualified `EXPUNGE` can destroy mail.
- **Consistent multi-instance and revocation behaviour.** Live `SIGHUP` reloads and post-crash startups apply the exact same `staged` vs pre-`staged` rule under SQLite leases.
- **Caller-isolated, idempotent status polling.** `get_transaction_status` is side-effect-free.

### Negative

- Servers supporting neither `MOVE` nor `UIDPLUS` cannot run intra-account `move`.

### Neutral

- `_test_run_recovery` remains gated behind `IMAP_MCP_TEST_MODE=1` ([ADR 0058](0058-closed-test-mode-control-boundary.md)) for multi-retry fault tests.

## Security Implications

- **Attack surface.** Closes cross-caller `tx_id` probing/mutation, `Message-ID` collision data loss, collateral `EXPUNGE`, and `update_draft` TOCTOU races.
- **Trust boundaries.** Enforces `caller_id` ownership on every WAL row.
- **Data exposure.** Scrubs `staged_rfc822` to `NULL` on `staged`/terminal transitions.
- **Failure modes.** Ambiguous fallback matches escalate to `needs_operator` (`ambiguous_target_match`) without source deletion.
- **Auditability.** Every transition (`begin`, `fetched`, `staged`, `deleted`, `commit`, `aborted`, `escalated`) writes a chained audit entry ([ADR 0048](0048-pre-effect-audit-and-test-boundary.md)).

## Alternatives Considered

- **4 KiB prefix hash for deduplication ([ADR 0008](0008-idempotency-via-message-id-and-hash.md)).** Rejected: templated PDFs share headers and first 4 KiB while differing in line items.
- **Opportunistic recovery inside `get_transaction_status`.** Rejected: turns a meta read tool into a hidden write trigger.
- **Unqualified `EXPUNGE` fallback without `UIDPLUS`.** Rejected: destroys unrelated `\Deleted` messages in the folder.

## References

- Supersedes [ADR 0006](0006-cross-account-move-via-saga.md), [ADR 0007](0007-sqlite-as-wal-store.md), and [ADR 0008](0008-idempotency-via-message-id-and-hash.md).
- [ADR 0045](0045-write-visibility-floors-and-flow-monotonicity.md) — write visibility and flow monotonicity.
- [ADR 0048](0048-pre-effect-audit-and-test-boundary.md) — pre-effect audit intent.
- RFC 4315 (`UIDPLUS`), RFC 6851 (`MOVE`).
