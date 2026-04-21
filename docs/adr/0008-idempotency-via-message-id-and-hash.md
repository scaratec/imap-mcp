# ADR 0008: Idempotency via Message-ID with Content-Hash Witness

- **Status:** Accepted
- **Date:** 2026-04-20
- **Deciders:** Randy Nel Gupta

## Context

The cross-account saga in [ADR 0006] must recover correctly after a
crash, network failure, or server error. The critical question on
recovery is *"did this specific message already land in the target?"*.

Answering it requires an identifier that:

1. The server can store in the WAL at transaction begin.
2. The server can, on recovery, use to query the target IMAP
   server and determine presence.
3. Is robust to server-side modifications of the message that do not
   constitute a content change (e.g. Gmail prepends `X-Gm-*` headers
   on APPEND).

Three identifier strategies are obvious candidates:

- **Message-ID only** (`Message-Id:` header, RFC 5322 §3.6.4).
- **Content hash only** (SHA-256 over the RFC822 bytes).
- **Message-ID primary, content hash as a forensic witness.**

Message-ID has the great virtue that IMAP lets us search for it
natively (`SEARCH HEADER Message-Id`). Content hashes cannot be
searched server-side; we would have to fetch candidate messages and
rehash them. But Message-IDs can be missing, duplicated across
messages, or spoofed.

Content hashes are deterministic over bytes, but many real servers
mutate the bytes on APPEND — most notably Gmail, which adds its own
`X-Gm-*` headers. A hash computed pre-APPEND no longer matches a hash
re-computed from the target after APPEND.

## Decision

The idempotency scheme uses **Message-ID as the primary lookup key**,
with a **content hash stored in the WAL as a forensic witness**, and a
**fallback query** for the rare case of a message without a usable
Message-ID.

At transaction begin the WAL records:

- `message_id` — value of the `Message-Id:` header from the source
  FETCH, normalized per RFC 5322 (angle-bracket-stripped, case-folded
  domain part).
- `content_hash` — SHA-256 over the source RFC822 bytes. Not used for
  lookup; stored for offline integrity checks and dispute resolution.
- `fallback_key` — a deterministic 5-tuple
  `(from, sent_on_date, subject_prefix_sha256, size, first_4kb_sha256)`
  for use when `message_id` is absent or ambiguous.

At recovery, the target is queried in this order:

1. `SEARCH HEADER Message-Id "<value>"`. If exactly one match, the
   message is present. If zero matches, proceed to APPEND-retry.
2. If step 1 returns multiple matches, `FETCH` each candidate's
   header/size, compare against the `fallback_key`. If exactly one
   candidate matches, treat as present.
3. If still ambiguous, or if `message_id` was absent at transaction
   begin, issue `SEARCH FROM <from> SENTON <date> SUBJECT <subject>`
   (using a short, safe subject prefix) and compare candidates against
   `fallback_key`.
4. If ambiguity persists, the transaction moves to `needs_operator`.
   The server does not guess; human judgement is required.

The content hash is consulted only by offline tools (e.g. an operator
CLI that compares two versions of a message to confirm they are
semantically the same despite differing bytes).

## Consequences

### Positive

- **Standard IMAP lookup.** The server-side search uses `HEADER
  Message-Id`, which every IMAP server in practical use supports with
  good index performance.
- **Immune to server header injection.** Gmail's `X-Gm-*` headers do
  not affect the Message-ID. Our lookup is therefore reliable across
  the providers we intend to support.
- **Fallback is deterministic.** The 5-tuple never requires body
  content (size and first-4KB hash are computed at fetch time from
  bytes we already have). No extra fetch on recovery.
- **Forensic hash is available.** If a dispute arises later ("did the
  server change the message content?"), the stored hash is an
  authoritative reference point.
- **Explicit escape hatch.** Unresolvable ambiguity surfaces as an
  operator-visible state rather than being silently mis-committed.

### Negative

- **Three lookup branches to test.** More code than Message-ID-only;
  each branch requires test coverage including crafted duplicate
  cases.
- **Fallback has grey zones.** Two messages from the same sender, on
  the same date, with the same subject, same size, same first-4KB —
  while extraordinarily rare — lead to `needs_operator`. This is
  correct behaviour but requires documentation.
- **Hash computation cost.** Hashing happens once at fetch time on
  the source. Streaming SHA-256 over multi-megabyte attachments is
  measurable but well under one second even on modest hardware.

### Neutral

- Message-ID normalization follows RFC 5322 and treats
  `<abc@EXAMPLE.com>` identically to `<abc@example.com>`. The local
  part is kept case-sensitive, per the specification.

## Security Implications

- **Spoofed Message-IDs.** An attacker who controls an inbound
  message can set any Message-ID. If they reuse a Message-ID already
  present in the target, the saga could conclude the message is
  already delivered and skip APPEND — leading to a lost message.
  Mitigation: the fallback 5-tuple includes size and content-prefix
  hash; the server requires *all* components to match (not only the
  Message-ID) before treating a candidate as "already there". A
  mismatch downgrades to `needs_operator`.
- **Content-hash leakage.** Hashes stored in the WAL are not secret
  but do reveal the fact that a specific byte sequence was moved.
  Protected under the same hygiene as the rest of the WAL ([ADR 0007]).
- **No body-content logging.** Neither the audit log nor the WAL
  stores message bodies or subjects in cleartext beyond the short
  prefix used for the fallback hash (hashed, not stored raw).
- **Timing side-channel.** The lookup order means that
  Message-ID-only hits return faster than fallback hits. An observer
  with network access *and* WAL-recovery timing could infer something
  about a failed transaction's identifier structure. The value of
  this channel is essentially nil for the threat model; documented
  for completeness.

## Alternatives Considered

- **Message-ID only, no fallback.** Rejected; messages without a
  Message-ID would cause every crash-recovery to stall on them.
- **Content-hash only.** Rejected; Gmail and other providers mutate
  bytes on APPEND, breaking the hash comparison at the target. A
  post-APPEND recomputed hash no longer matches the pre-APPEND hash
  and cannot be used for presence detection.
- **Client-generated opaque key** (e.g. UUID injected as an
  `X-Imap-Mcp-Tx:` header at APPEND). Rejected; this requires
  mutating the stored message, which exposes caller metadata to any
  later reader of that folder and couples the idempotency key to the
  stored artefact forever.
- **UIDPLUS `APPENDUID` alone.** The target UID returned by a server
  supporting UIDPLUS ([RFC 4315]) is sufficient on the happy path, but
  is not enough on crash: if the APPEND succeeded on the server but
  the response was lost to the client, there is no UID in the WAL to
  consult. Use of `APPENDUID`, when available, is a fast-path
  optimization and not the correctness guarantee.
- **Rely on de-duplication by the target server.** Some servers
  (notably Gmail) silently de-duplicate identical APPENDs. Others
  don't. Correctness cannot depend on behaviour that is not
  standardised.

## References

- RFC 5322 §3.6.4 — `Message-Id:` semantics and uniqueness intent.
- RFC 4315 — IMAP `UIDPLUS` and `APPENDUID`.
- [ADR 0006] — saga that consumes this idempotency scheme.
- [ADR 0007] — WAL that stores Message-ID, content hash, fallback key.
- [ADR 0021] — audit log records that cite `tx_id`, not these keys.

[RFC 4315]: https://www.rfc-editor.org/rfc/rfc4315
[ADR 0006]: 0006-cross-account-move-via-saga.md
[ADR 0007]: 0007-sqlite-as-wal-store.md
[ADR 0021]: 0021-audit-log-format.md
