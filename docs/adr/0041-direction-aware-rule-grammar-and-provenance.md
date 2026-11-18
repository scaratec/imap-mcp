# ADR 0041: Direction-Aware Rule Predicate Grammar

- **Status:** Accepted
- **Date:** 2026-11-17
- **Deciders:** Randy Nel Gupta

## Context

Earlier revisions ([ADR 0004](0004-sender-rule-matcher-grammar.md), [ADR 0024](0024-duration-grammar-single-source.md)) designed predicates around inbound mail:

1. Outbound recipients had only `to` and substring `to_contains`, allowing `to_contains: allianz.de` to match `attacker@evil-allianz.de` and `x@allianz.de.attacker.com`, mixing `To` and `Cc`, and offering no `all_recipients_in_domains` quantifier.
2. Relative durations (`newer_than`, `older_than`) evaluated the sender-spoofable `Date:` header, whereas calendar bounds (`since`, `before`) evaluated `INTERNALDATE`.
3. Rounding `older_than` to `BEFORE today - floor(seconds/86400)` dropped same-day messages earlier than `now - seconds` before post-filtering.
4. Durations and byte sizes lacked upper bounds, and no predicate matched caller-authored messages (`provenance: "own"`).

## Decision

We enforce a **single closed, direction-aware predicate grammar** across policy rules and search/bulk criteria, evaluated over parsed RFC 5322 `addr-spec` addresses, server-verified provenance, and UTC `INTERNALDATE`.

### 1. Closed predicate table and `criteria` visibility floors

| Predicate | Type | Min `criteria` floor | Matches when |
|---|---|---|---|
| `from_address` | `addr-spec` string | `ENVELOPE` | Parsed `From:` address equals value (case-folded, IDNA ASCII domain) |
| `from_domain` | DNS domain string | `ENVELOPE` | Parsed `From:` domain equals value (trailing-dot stripped, IDNA ASCII; never substring) |
| `to_address` / `to_domain` | address / domain | `ENVELOPE` | Any parsed `To:` address / domain equals value |
| `cc_address` / `cc_domain` | address / domain | `ENVELOPE` | Any parsed `Cc:` address / domain equals value |
| `recipient_address` / `recipient_domain` | address / domain | `ENVELOPE` | Any parsed `To:`, `Cc:`, or `Bcc:` address / domain equals value |
| `all_recipients_in_domains` | non-empty domain list | `ENVELOPE` | `To ∪ Cc ∪ Bcc` is non-empty and **every** parsed recipient's domain is in the list |
| `subject_contains` | non-empty string | `ENVELOPE` | Decoded RFC 2047 `Subject:` (Unicode NFC, case-folded) contains value |
| `has_attachment` | boolean | `BODY` | Message contains at least one MIME attachment part ([ADR 0047](0047-per-caller-symlink-safe-attachment-delivery.md)) |
| `flagged` | boolean | `METADATA` | IMAP `\Flagged` flag is present (`true`) or absent (`false`) |
| `provenance` | `"own"` | `METADATA` | Message carries verified provenance for the requesting `caller_id` ([ADR 0042](0042-cryptographic-and-wal-provenance.md)) |
| `newer_than` | `^[1-9][0-9]{0,5}[smhdwy]$` (`<= 36500d`) | `METADATA` | `now_utc - internaldate_utc <= duration` (inclusive at boundary) |
| `older_than` | `^[1-9][0-9]{0,5}[smhdwy]$` (`<= 36500d`) | `METADATA` | `now_utc - internaldate_utc > duration` (strict at boundary) |
| `since` | ISO date `YYYY-MM-DD` | `METADATA` | `internaldate_utc_date >= since` (inclusive; mutually exclusive with durations) |
| `before` | ISO date `YYYY-MM-DD` | `METADATA` | `internaldate_utc_date < before` (exclusive; `since < before` required) |
| `size_gt` / `size_lt` | integer `1..1073741824` | `METADATA` | `size_bytes > value` / `size_bytes < value` |

Predicates within one rule or `criteria` object combine via logical **AND**; rules in a folder list combine via logical **OR**. Legacy keys (`from`, `to`, `to_contains`) are rejected at policy load and at JSON-RPC validation (`-32602`).

### 2. RFC 5322 address normalization and `INTERNALDATE` superset pre-filtering

- Display names and comments are discarded during RFC 5322 address parsing; malformed headers without `@` never match domain predicates. Domains are lowercased, stripped of trailing dots, and normalized to IDNA ASCII.
- All time predicates compare UTC `INTERNALDATE`. IMAP `SEARCH` pre-filters pad whole-day `SINCE` by `-1 day` and `BEFORE` by `+1 day` to form a timezone-independent superset before exact UTC post-filtering.

## Consequences

### Positive

- **Inbound/outbound symmetry.** Exact recipient domains (`to_domain`, `all_recipients_in_domains`) eliminate substring spoofing on `Sent Mail`.
- **Sound time filtering.** Same-day `older_than` queries never drop matching mail, and spoofed `Date:` headers cannot evade age rules.
- **Draft-to-sent continuity.** `provenance: "own"` authorizes sent copies of caller-authored drafts cleanly.

### Negative

- Policy files and callers using `from`, `to`, or `to_contains` must migrate to `3.0.0` keys.
- 1-day IMAP `SEARCH` padding inspects slightly more boundary-day metadata.

### Neutral

- Regular expressions and body-text predicates remain excluded.

## Security Implications

- **Attack surface.** Bounded numeric/duration ranges and RFC 5322 `addr-spec` extraction block resource exhaustion and display-name spoofing.
- **Trust boundaries.** Every grant-capable predicate evaluates immutable transport/MIME metadata, human-only `\Flagged`, or server-signed provenance.
- **Data exposure.** Per-predicate `criteria` floors prevent `METADATA` callers from probing `ENVELOPE` or `BODY` fields ([ADR 0043](0043-non-interference-transparency-and-search.md)).
- **Failure modes.** Unknown keys or out-of-range bounds fail closed before IMAP traffic.
- **Auditability.** Invalid criteria emit `invalid_arguments` audit records ([ADR 0048](0048-pre-effect-audit-and-test-boundary.md)).

## Alternatives Considered

- **Retain `to_contains` alongside `to_domain`.** Rejected: substring address matching invites domain-suffix spoofing.
- **Keep `Date:` for `newer_than` and `INTERNALDATE` for `since`/`before`.** Rejected: inconsistent time bases let external senders spoof `Date:`.
- **Support legacy `from`/`to` as silent aliases.** Rejected: `to` previously conflated `To` and `Cc`; explicit field names prevent policy ambiguity.

## References

- Supersedes [ADR 0004](0004-sender-rule-matcher-grammar.md) and [ADR 0024](0024-duration-grammar-single-source.md).
- [ADR 0042](0042-cryptographic-and-wal-provenance.md) — `provenance: "own"`.
- [ADR 0043](0043-non-interference-transparency-and-search.md) — predicate floor gating.
- RFC 3501 §6.4.4, RFC 5322 §3.4, RFC 5891.
