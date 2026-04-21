# ADR 0002: Linear Visibility Levels

- **Status:** Accepted
- **Date:** 2026-04-20
- **Deciders:** Randy Nel Gupta

## Context

[ADR 0001] establishes that every folder-level rule grants a caller some
*level* of visibility into the messages it matches, but leaves open what the
set of levels actually is and how they relate to each other.

Two families of designs are possible:

- A **linear, monotone scale** where each level includes everything below it
  (e.g. `BODY` implies `ENVELOPE` implies `METADATA` ...).
- A **set of orthogonal flags** where each field (envelope, headers, body,
  attachments, raw) can be enabled independently.

This question must be decided before sender-rule semantics, response shapes,
or audit fields can be finalized.

[ADR 0001]: 0001-default-deny-hierarchical-policy.md

## Decision

We use a **linear, monotone scale** of seven levels:

```
NONE  <  COUNT  <  METADATA  <  ENVELOPE  <  HEADERS  <  BODY  <  FULL
```

| Level | What the caller sees |
|-------|----------------------|
| `NONE`     | Nothing. The folder or message does not exist from the caller's perspective. |
| `COUNT`    | Aggregate counts only (e.g. "folder has N messages"). |
| `METADATA` | Per-message: UID, date, flags, size. No sender, no subject. |
| `ENVELOPE` | + From, To, Cc, Subject, Message-ID. |
| `HEADERS`  | + All RFC 5322 headers. |
| `BODY`     | + Plain-text and HTML body parts. |
| `FULL`     | + Attachments and any remaining MIME parts. |

A rule grants exactly one level. A higher level implicitly includes everything
lower. A caller's effective level at a folder/message is derived by the PDP
([ADR 0001]) from the folder's mode ([ADR 0003]) and the matching sender rules.

Fields above the granted level are omitted from responses and flagged in the
transparency fields ([ADR 0017]).

[ADR 0003]: 0003-whitelist-blacklist-folder-modes.md
[ADR 0017]: 0017-response-transparency-for-filtered-data.md

## Consequences

### Positive

- **Single value per rule.** A policy-review diff that moves a rule from
  `ENVELOPE` to `BODY` is immediately understandable. A diff in a flag set is
  not.
- **Audit records a single token**, not a bitmask. Greppable, sortable,
  dashboard-friendly.
- **Natural mental model.** Policy authors think "how deep may this agent
  look", which maps one-to-one to the scale.
- **Deterministic comparison.** `granted >= required` is a total order. No
  surprises.

### Negative

- **Cannot express odd combinations** like "headers yes, envelope no" or "body
  yes, subject no". We judge these to be constructed and not worth the
  complexity cost.
- **Attachments cannot be policy-controlled independently of body.** A rule
  that grants body text also implicitly grants attachment *existence*
  (attachment metadata is part of MIME structure, not body); only `FULL`
  grants attachment *content*. This is the one split the scale honours.

### Neutral

- The level set is fixed. Adding a new level would be a new ADR, not a config
  change — on purpose.

## Security Implications

- **Fail-safe default.** The lowest level (`NONE`) is the absence of any grant.
  A policy hole in any higher level defaults to the lower, not the reverse.
- **Attack surface minimization.** Each level opens a specific set of
  response fields. A bug in `fetch_body` cannot leak attachment content,
  because attachment content requires `FULL` and a different tool.
- **Audit clarity.** Every allow/deny decision carries exactly one level. A
  forensic reviewer can answer "what did this caller see of this message"
  without reconstructing a flag combination.
- **Redaction determinism.** For a given `(level, message)` pair the set of
  visible fields is fully determined. No field-by-field policy evaluation,
  which historically is where leaks live.

## Alternatives Considered

- **Orthogonal flag set** (`envelope`, `headers`, `body`, `attachments`,
  `raw`, each boolean). Rejected. It gives 2^5 = 32 combinations, most of
  which are nonsensical; policy authors would quickly invent informal
  conventions that re-create a linear scale. Audit and reasoning both suffer.
- **Per-field allow-list** (caller lists exactly which headers, body parts,
  attachment MIME types they may see). Rejected as an implementation
  nightmare disguised as flexibility; the matching logic becomes a
  Turing-complete filter that is not statically auditable.
- **Two levels (read / no-read).** Rejected as too coarse for the stated use
  cases (e.g. "metadata only for a banking folder, full body for invoices").

## References

- [ADR 0001] — hierarchical policy model that depends on this level set.
- [ADR 0017] — transparency-field contract that references the level vocabulary.
- RFC 3501 §6.4.5 — IMAP `FETCH` data items; informs where the splits sit
  naturally in the protocol.
