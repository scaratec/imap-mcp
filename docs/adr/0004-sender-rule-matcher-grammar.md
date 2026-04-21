# ADR 0004: Sender Rule Matcher Grammar

- **Status:** Accepted
- **Date:** 2026-04-20
- **Deciders:** Randy Nel Gupta

## Context

[ADR 0001] and [ADR 0003] establish that folder policies carry a list of
rules, each of which pairs a *match expression* with a visibility grant or
cap. The expressiveness of the match language decides two opposing
properties:

- **Too narrow** → real use cases cannot be expressed in policy. Workarounds
  migrate into code or into a second informal policy layer.
- **Too wide** → policy itself becomes an attack surface. Catastrophic regex
  backtracking, expression languages with side effects, and body-content
  predicates that force pre-policy message download all fail the
  "statically auditable" test.

A concrete grammar must be fixed before policy files can be validated and
before the PDP implementation is stable.

## Decision

V1 supports exactly these **core predicates**. A rule's `match` field is an
object whose keys are the predicates below; all listed predicates must hold
(implicit AND) for the rule to match. A message that fails any predicate
fails the whole rule.

| Predicate            | Value type                 | Matches when |
|----------------------|----------------------------|--------------|
| `from`               | RFC 5321 mailbox string     | Envelope `MAIL FROM` or `From:` header (normalized, case-insensitive) equals the value |
| `from_domain`        | DNS domain string           | Sender's domain equals the value (trailing dots normalized) |
| `to`                 | RFC 5321 mailbox string     | Any `To:` or `Cc:` recipient matches exactly |
| `to_contains`        | substring                   | Any `To:`/`Cc:` recipient string contains the substring (case-insensitive) |
| `subject_contains`   | substring                   | `Subject:` header contains the substring (case-insensitive, Unicode NFC normalized) |
| `has_attachment`     | boolean                     | Message has at least one `Content-Disposition: attachment` part |
| `newer_than`         | ISO 8601 duration           | `Date:` header newer than `now() - duration` |
| `older_than`         | ISO 8601 duration           | `Date:` header older than `now() - duration` |
| `size_gt`            | integer (bytes)             | RFC822 size greater than value |
| `size_lt`            | integer (bytes)             | RFC822 size less than value |

Rules combine through the folder's rule list (OR across rules, AND within
one rule). There is no per-rule `not` operator; the whitelist/blacklist
mode distinction ([ADR 0003]) carries the polarity.

Predicates run against **envelope and header data only** (plus RFC822 size
and MIME structure summary). No body-content predicates. No full-text
search. Envelope and headers are already fetched as part of any policy
evaluation, so these predicates add no I/O.

## Consequences

### Positive

- **Statically auditable.** The grammar has a finite vocabulary; a validator
  can enumerate every predicate used in every rule and verify reachability.
- **No ReDoS.** Regular expressions are not part of the core grammar; a
  malicious or malformed pattern cannot pin a CPU.
- **Pre-fetch evaluation.** All predicates can be evaluated from envelope +
  headers + MIME-structure summary, which IMAP exposes without fetching the
  body. Policy decides *before* any body bytes hit the server.

### Negative

- **Some real use cases are not expressible in V1.** "Block anything with
  `[CONFIDENTIAL]` as a subject prefix" is expressible (`subject_contains`).
  "Block anything matching `INV-\d{8}` in the subject" is not. For those
  cases, operators either accept coarser rules, or wait for the extended
  grammar (deferred).
- **Mailing-list detection** without `list_id` is approximate (operators can
  fall back to `from_domain` or `to_contains`). A dedicated `list_id`
  predicate is listed in §Future Work.

### Neutral

- The grammar is closed. Adding a predicate is a policy-breaking change (may
  invalidate existing rules' meaning) and therefore a new ADR.

## Security Implications

- **No code execution in policy.** Match expressions are pure data over a
  fixed predicate set. A compromised policy file cannot exfiltrate,
  side-channel, or pin CPU.
- **No body leak via policy.** Because no predicate reads the body, the PDP
  never has a reason to fetch body content for policy purposes. A later
  addition of body predicates would reintroduce this risk and must pass a
  security review.
- **Regex deferred, not banned.** When added later, it will be RE2-backed
  (linear-time guaranteed), pattern-length-capped, and opt-in per folder.
  V1 simply does not have it.
- **Header spoofing.** `from` / `from_domain` predicates match the `From:`
  header, which is forgeable. DMARC/SPF/DKIM verification is out of scope
  for V1; the policy author must accept that envelope-based rules carry the
  same trust as the mail transport does.
- **Matching is case-insensitive and NFC-normalized**, which prevents the
  Unicode confusables class of bypass ("`rechnung`" vs "`rеchnung`" with
  Cyrillic `е`) *partially*. Full confusables defence needs IDNA/UTS #39
  handling and is future work.

## Alternatives Considered

- **Ship with `subject_regex` and `header_matches` in V1** (the "extended"
  set). Rejected for this release: ReDoS mitigation, RE2 dependency,
  pattern-length caps, and extensive test matrix are all non-trivial. Better
  to defer to a dedicated ADR.
- **Admit a small expression language** (jq, CEL, starlark). Rejected as
  disproportionate: all real use cases fit a predicate-AND structure, and
  an expression language imports a large attack surface for a benefit
  measured in single rules.
- **Allow `not` as a rule-level modifier.** Rejected; the whitelist /
  blacklist mode distinction is the intended way to express "anything not
  matching X". `not` inside a rule invites confusing nested polarities.
- **Body-content predicates** (`body_contains`, `body_regex`). Rejected.
  Would force the PDP to fetch body bytes before authorizing body access —
  a privilege inversion that leaks into the audit log.

## Future Work

Deferred predicates that may be added via later ADRs, each with its own
security review:

- `subject_regex`, `header_matches`, `header_regex` — with RE2 engine and
  hard pattern-length limits.
- `list_id` — specifically the `List-Id` RFC 2919 header.
- `attachment_mimetype`, `attachment_name_contains` — allows rules keyed on
  attachment properties without reading attachment bytes.
- `label` — Gmail-specific label predicate (see [ADR 0019]).
- `dmarc_pass`, `spf_pass` — once the server extracts Authentication-Results.

## References

- [ADR 0001] — default-deny model this grammar serves.
- [ADR 0002] — visibility levels the grants/caps reference.
- [ADR 0003] — folder mode that wraps these predicates.
- [ADR 0019] — Gmail semantics that motivate a future `label` predicate.
- RFC 5321 — SMTP; defines the envelope `MAIL FROM`.
- RFC 5322 — Internet Message Format; defines `From:`, `To:`, `Date:`.
- RFC 2919 — The List-Id Header Field.
- Google RE2 — linear-time regex engine intended for future regex predicates.

[ADR 0001]: 0001-default-deny-hierarchical-policy.md
[ADR 0003]: 0003-whitelist-blacklist-folder-modes.md
[ADR 0019]: 0019-gmail-label-semantics.md
