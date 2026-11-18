# ADR 0063: Structured Own-Draft Composition and Revision

- **Status:** Accepted
- **Date:** 2026-11-17
- **Deciders:** Randy Nel Gupta

## Context

Earlier draft ADRs ([ADR 0031](0031-draft-revision-via-rewrite-saga.md), [ADR 0034](0034-discard-draft-by-handle.md), [ADR 0035](0035-draft-body-format-choice.md)) introduced structured draft composition, handle-based revision, and handle-based discard, but lacked:

1. Structured attachment composition/replacement on own drafts once received-mail attachment rewrite was retired ([ADR 0057](0057-immutable-received-mail-and-structured-drafts.md)).
2. Explicit `From:` derivation, paragraph-preserving HTML-to-text conversion, and CR/LF header-injection rejection across all structured fields.
3. Per-message `BODY` visibility and MIME-attachment quote isolation on `create_reply_draft`.
4. Explicit specification of `X-SC-Draft-Change-Hash` replay semantics and deterministic `discard_draft` gate ordering under `3.0.0`.

## Decision

We provide **structured and RFC 5322 composition (`\Draft \Seen`), attachment editing, MIME-isolated reply quoting, idempotent `X-SC-Draft-Change-Hash` field-wise revision, and deterministic `\Trash` discard exclusively on provenance-verified own drafts in `\Drafts` folders**.

### 1. Composition, `From:`, attachments, and flags (`create_draft`)

- `create_draft` accepts either structured fields (`to`, `cc`, `bcc`, `subject`, `body_text`, `body_html`, `in_reply_to`, `attachments`) or `rfc822` (never both, requiring at least one body field in structured mode; `-32602` otherwise), appends with IMAP flags `\Draft` and `\Seen`, and reports stored `format` (`"text/plain" | "text/html" | "multipart/alternative" | "multipart/mixed"`).
- Any `\r` or `\n` in header-valued structured fields (`to`, `cc`, `bcc`, `subject`, `in_reply_to`, attachment `filename`/`mime_type`) fails with `-32602` before opening any IMAP connection.
- Structured drafts derive `From:` from `account.user` if it contains `@`, else `account.id` when email-shaped.
- HTML-to-text fallback (in `create_draft`, `create_reply_draft`, and `fetch_body`) converts block tags (`<p>`, `<br>`, `<div>`, `<li>`, `<tr>`, `<h1..h6>`) into line breaks before stripping tags.
- `attachments` (`[{filename, mime_type, content_base64}]`) on `create_draft` and `update_draft` (`changes.attachments`) composes, replaces, or clears (`[]`) attachments on own drafts.

### 2. Reply quote isolation (`create_reply_draft`)

- Requires `draft_append: true` on `\Drafts`, `eff_source(m) >= BODY` on the source message (`not_found` if `< METADATA`; `DENY / visibility_below_BODY` if `METADATA..HEADERS`), source `Message-ID` (`ALLOW + ERROR / missing_message_id`), and non-empty reply text/HTML (`ALLOW + ERROR / empty_reply_text`).
- Derives `To` (`Reply-To` else `From`), `Cc` (`(To ∪ Cc) \ self`), `In-Reply-To`, `References`, `"Re: "` prefix, and local wall-clock attribution `"On YYYY-MM-DD HH:MM, <sender> wrote:"` (omitting date prefix if `Date:` is absent).
- Quotes **only** non-attachment inline `text/plain` / `text/html` body parts (never attachment parts per [ADR 0047](0047-per-caller-symlink-safe-attachment-delivery.md) or `message/rfc822` sub-messages), stripping `<script>`/executable tags inside HTML `<blockquote>`.

### 3. Idempotent revision (`update_draft`) and discard (`discard_draft`)

- `update_draft` requires `draft_edit: true` on `\Drafts` and verified caller provenance ([ADR 0042](0042-cryptographic-and-wal-provenance.md)), preserving `\Draft` and `\Seen`, regenerating `Message-ID`, `Date`, `X-SC-Provenance`, and injecting `X-SC-Draft-Change-Hash = hex(SHA-256(UTF-8(canonical_json(changes))))` (sorted keys, compact `","`/`":"` separators, explicit `null` preserved).
- If `current_revision == base_revision + 1` and stored `X-SC-Draft-Change-Hash` matches, `update_draft` returns `ALLOW + OK` (`idempotent_replay: true`, no `tx_id`) without writing; any other `base_revision != current_revision` returns `ALLOW + ERROR` (`draft_revision_conflict`, `current_revision`). Duplicate copies sharing `(draft_id, revision)` return `ALLOW + ERROR` (`draft_ambiguous`) and record `needs_operator` in `wal.db`.
- `discard_draft` checks gates in deterministic order: source `\Drafts` role + `move_out: true` (`DENY / capability_missing`) -> account `\Trash` special-use existence (`ALLOW + ERROR / trash_unavailable`) -> `\Trash` `accept_incoming: true` (`DENY / capability_missing`) -> caller provenance verification (`not_found` if unknown/foreign), then moves all verified revisions in `\Drafts` to `\Trash`.

## Consequences

### Positive

- **Complete, safe draft lifecycle.** Agents compose, attach files to, revise, reply to, and discard their own drafts cleanly without UID guessing or received-mail mutation.
- **Quote-washing blocked.** Attachment parts and `< BODY` messages can never be quoted into `\Drafts`.

### Negative

- External edits to an agent's draft invalidate `X-SC-Provenance` and surface `draft_not_editable` ([LIM 0022](../limitations/0022-externally-edited-drafts-lose-their-handle.md)).

### Neutral

- `create_draft`'s description presents plain text vs. HTML neutrally and instructs callers to ask when user preference is unknown.

## Security Implications

- **Attack surface.** Rejects CR/LF header injection (`-32602`) and strips `<script>` in HTML reply blockquotes.
- **Trust boundaries.** Enforces caller provenance ([ADR 0042](0042-cryptographic-and-wal-provenance.md)), `draft_recipients` ([ADR 0045](0045-write-visibility-floors-and-flow-monotonicity.md)), and per-message source `BODY` visibility.
- **Data exposure.** Excludes attachments and `message/rfc822` parts from reply quotes; write-only (`NONE`) `\Drafts` revisions never echo draft bodies.
- **Failure modes.** Ambiguous duplicate revisions stop safely at `needs_operator`; missing `\Trash` returns `trash_unavailable` without hard-deleting.
- **Auditability.** Pre-effect `intent` and post-effect `outcome` audit records cover every draft write ([ADR 0048](0048-pre-effect-audit-and-test-boundary.md)).

## Alternatives Considered

- **Accept raw `rfc822` only (`create_draft` V1).** Rejected: hand-building multipart MIME and RFC 2047 headers caused agents to avoid HTML and attachments.
- **Discard drafts via UID-based `move`.** Rejected: callers holding only `draft_id` risked moving a human's draft sharing the same subject.
- **Quote attached `text/plain` or `message/rfc822` parts in `create_reply_draft`.** Rejected: leaks attachment contents into drafts under `BODY` visibility without `FULL`.

## References

- Supersedes [ADR 0031](0031-draft-revision-via-rewrite-saga.md), [ADR 0034](0034-discard-draft-by-handle.md), and [ADR 0035](0035-draft-body-format-choice.md).
- [ADR 0042](0042-cryptographic-and-wal-provenance.md), [ADR 0045](0045-write-visibility-floors-and-flow-monotonicity.md), [ADR 0046](0046-content-verified-sagas-and-wal-leases.md), [ADR 0057](0057-immutable-received-mail-and-structured-drafts.md).
