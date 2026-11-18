# ADR 0042: Cryptographic Draft Provenance

- **Status:** Accepted
- **Date:** 2026-11-17
- **Deciders:** Randy Nel Gupta

## Context

[ADR 0030](0030-draft-identity-and-revisions.md) identified server-created drafts solely by unsigned `X-SC-Draft-Id` and `X-SC-Draft-Rev` headers:

1. Any message carrying `X-SC-Draft-Id` was treated as editable/discardable regardless of which caller created it.
2. External recipients of a sent draft learned `X-SC-Draft-Id` and could forge messages with higher `X-SC-Draft-Rev`.
3. Once a human sent an agent-composed draft, the copy in `Sent Mail` lost verifiable caller ownership.
4. External mail clients that edited draft text while preserving `X-SC-*` headers ([LIM 0022](../limitations/0022-externally-edited-drafts-lose-their-handle.md)) went undetected, risking silent overwrite of human edits.

## Decision

We bind every server-authored draft and its sent successor to `(caller_id, account_id, draft_id, revision, canonical_digest)` via **both an HMAC-SHA256-signed `X-SC-Provenance` header and a SQLite `provenance_records` registry in `wal.db`**.

### 1. Server HMAC key and injected headers

- A 256-bit key is stored in `SecretStore` at `secret://server/provenance_hmac_key` (auto-generated as 32 random bytes stored as 64 lowercase hex characters on writable stores if absent, or supplied via `IMAP_MCP_SERVER_HMAC_KEY` on `env_var`). When the stored secret is a 64-character hex string, the raw 32-byte key decoded from hex is used as the HMAC key.
- Before appending a draft, the server strips all inbound `X-SC-*` headers case-insensitively and injects:
  - `X-SC-Draft-Id: <uuid4>`
  - `X-SC-Draft-Rev: <positive integer>`
  - `X-SC-Provenance: v1:<64hex>` where the MAC signs `"v1|" || caller_id || "|" || account_id || "|" || draft_id || "|" || str(rev) || "|" || canonical_digest`, and `canonical_digest` hashes normalized `From`/`To`/`Cc`/`Bcc` addresses, normalized `Subject`, and ordered MIME leaf digests.
  - On revisions (`r >= 2`), `X-SC-Draft-Change-Hash` ([ADR 0057](0057-immutable-received-mail-and-structured-drafts.md)) is also injected by the server after stripping inbound `X-SC-*` headers.

### 2. WAL provenance registry and opaque non-interference verification

- `create_draft`, `create_reply_draft`, and `update_draft` record `(draft_id, revision, caller_id, account_id, message_id, canonical_digest, state)` in `wal.db`; `discard_draft` marks all revisions `'discarded'`.
- **Ownership & editability (`update_draft`, `discard_draft`, `list_drafts.editable`):**
  1. If no `provenance_records` row exists for `(draft_id, account_id)` owned by the requesting `caller_id` (including unknown UUIDs, sibling-caller drafts, or externally injected messages with forged/unsigned `X-SC-Draft-Id`), `update_draft` and `discard_draft` return opaque **`ALLOW + ERROR` with `error.type: "not_found"`** (preventing any existence oracle on foreign handles).
  2. If `provenance_records` confirms `caller_id` owns `draft_id`, but the message in `\Drafts` has missing, duplicate, or invalid `X-SC-Provenance` (for example, a human edited the body text in a client that kept `X-SC-Draft-Id`), `list_drafts` marks `editable: false` and `update_draft` returns **`ALLOW + ERROR` with `error.type: "draft_not_editable"`**.
- **Sent-mail continuity (`provenance: "own"`):** A message matches `provenance: "own"` for `caller_id` iff either its `X-SC-Provenance` MAC verifies for `(caller_id, account_id, draft_id, rev, canonical_digest)` **or** its normalized `Message-ID` and `canonical_digest` match a `provenance_records` row owned by `(caller_id, account_id)` (covering mail clients that strip `X-SC-*` headers on send).

## Consequences

### Positive

- **Unforgeable per-caller ownership and zero handle oracle.** Foreign or unsigned handles always return `not_found`, identical to non-existent handles.
- **Human edits protected.** External body changes invalidate `canonical_digest` and surface `draft_not_editable` on own handles instead of overwriting human text.
- **Draft-to-sent continuity.** `provenance: "own"` works both when `X-SC-Provenance` survives SMTP and when `Message-ID` + `canonical_digest` match `wal.db`.

### Negative

- Rotating or losing `provenance_hmac_key` degrades older header-only drafts safely to `editable: false`.

### Neutral

- Draft handles remain opaque UUIDv4 strings; callers never see MAC keys or registry internals.

## Security Implications

- **Attack surface.** Inbound `X-SC-*` stripping prevents header smuggling in `rfc822` drafts.
- **Trust boundaries.** Anchors provenance in operator key material and `wal.db`, never untrusted message headers.
- **Data exposure.** Keyed HMAC digests reveal no `caller_id` or policy data to external email recipients, and foreign handles leak no existence bit.
- **Failure modes.** Missing, tampered, or foreign provenance fails closed (`not_found` or `draft_not_editable`).
- **Auditability.** Draft operations record `draft_id`, `revision`, and forensic `audit_detail_reason` (`foreign_caller` when applicable; [ADR 0048](0048-pre-effect-audit-and-test-boundary.md)).

## Alternatives Considered

- **Unsigned `X-SC-Draft-Id` headers ([ADR 0030](0030-draft-identity-and-revisions.md)).** Rejected: trivially forgeable by external senders or sibling callers.
- **WAL mapping only without in-message headers.** Rejected: loses unambiguous per-revision IMAP `HEADER` lookup across `UIDVALIDITY` resets.
- **Return `draft_not_editable` for unsigned foreign messages.** Rejected: creates an existence oracle distinguishing foreign handles from absent handles in write-only `\Drafts` folders.

## References

- Supersedes [ADR 0030](0030-draft-identity-and-revisions.md).
- [ADR 0041](0041-direction-aware-rule-grammar-and-provenance.md) — `provenance: "own"` predicate.
- [ADR 0050](0050-secret-stores-and-oauth-posture.md) — `SecretStore` key storage.
- [ADR 0057](0057-immutable-received-mail-and-structured-drafts.md) — `X-SC-Draft-Change-Hash` and draft revision rules.
- [LIM 0022](../limitations/0022-externally-edited-drafts-lose-their-handle.md).
