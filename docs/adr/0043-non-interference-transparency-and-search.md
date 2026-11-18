# ADR 0043: Non-Interfering Response Projection

- **Status:** Accepted
- **Date:** 2026-11-17
- **Deciders:** Randy Nel Gupta

## Context

[ADR 0002](0002-linear-visibility-levels.md) defined the seven-level linear visibility scale (`NONE < COUNT < METADATA < ENVELOPE < HEADERS < BODY < FULL`) with `NONE` meaning *"the folder or message does not exist from the caller's perspective."* However, [ADR 0017](0017-response-transparency-for-filtered-data.md) and [ADR 0036](0036-search-window-transparency-and-calendar-dates.md) emitted pre-policy hidden counts (`matched_total`, `filtered_out`, `outside_scope_total`, `hidden_accounts_count`, `hidden_folders_count`, `hidden_count`), `matched_rule_index`, `sender_rules_count`, and raw `list_folders.message_count`.

Combining caller-chosen `criteria` with pre-policy counts created an adaptive predicate oracle that reconstructed recipients, senders, subjects, sizes, and dates of `NONE` messages while logging `ALLOW / OK`. Callers at `METADATA` could also probe `Subject` or `has_attachment` via search criteria or read `subject` in `list_drafts`.

## Decision

We preserve the **seven-level linear visibility scale (`NONE < COUNT < METADATA < ENVELOPE < HEADERS < BODY < FULL`)** while enforcing **strict non-interference for `NONE` objects and above-grant fields**, static completeness signalling, and per-level field projection.

### 1. Seven-level scale and removal of data-dependent hidden counts

- The monotone visibility order remains `NONE < COUNT < METADATA < ENVELOPE < HEADERS < BODY < FULL`.
- `hidden_accounts_count`, `hidden_folders_count`, `hidden_labels_count`, `hidden_count`, `list_folders.message_count`, `matched_total`, `filtered_out`, `outside_scope_total`, `matched_rule_index`, and `sender_rules_count` are removed across all tools.

### 2. Static completeness signalling (including Google multi-label caps)

Responses carry a static `completeness` value derived solely from the caller's policy and requested `scope`, never from mailbox contents:

- On `imap-standard` accounts, a folder has `may_hide == true` iff `mode == "whitelist"` or (`mode == "blacklist"` and `rules` contains a rule with `cap < default`).
- On `google` accounts ([ADR 0040](0040-message-centric-policy-and-gmail-composition.md)), `may_hide == true` additionally holds whenever **any** `FolderPolicy` on that account defines a `cap` rule below `default` (account-wide cap-override) **or** the addressed folder is `[Gmail]/All Mail` or `[Gmail]/Starred` (primary-label precedence).
- `search` and `list_messages` emit `total_visible`, `returned_count`, `applied_scope` (`"recent_7d" | "explicit_window" | "all_time"`), and `completeness`:
  - `"complete"` (`applied_scope != "recent_7d"` and `not may_hide`)
  - `"policy_may_hide"` (`applied_scope != "recent_7d"` and `may_hide`)
  - `"window_limited"` (`applied_scope == "recent_7d"` and `not may_hide`)
  - `"window_and_policy_limited"` (`applied_scope == "recent_7d"` and `may_hide`)
- `folder_stats` emits `visible_count` (`eff_vis >= COUNT`) and `completeness` (`"complete" | "policy_may_hide"`). `describe_policy` emits per visible folder: `path`, `mode`, `default_visibility`, `max_visibility`, `may_hide`, `capabilities`, and `has_draft_recipient_restrictions`.

### 3. Per-message predicate gating and deterministic projection

1. **Predicate floor:** Let `req_vis(criteria)` be the highest floor among predicates in `criteria` ([ADR 0041](0041-direction-aware-rule-grammar-and-provenance.md); `METADATA` when empty). Folder `max_visibility < max(tool_min_vis, req_vis(criteria))` denies before IMAP I/O (`visibility_below_<LEVEL>`); per-message evaluation includes `m` iff `eff_vis(m) >= max(tool_min_vis, req_vis(criteria))` and `criteria(m)` holds.
2. **Deterministic field projection and ordering:** `search` and `list_messages` order entries by ascending `uid` (default page `limit = 50`). Attachment metadata entries carry 0-based `index` (`0..k-1`, matching `part_id` on `fetch_attachment`), `filename`, `mime_type`, and `size_bytes`. `redacted_fields` depends solely on `eff_vis(m)`:
   - `METADATA`: `uid`, `uidvalidity`, `object_id` (Google), `date`, `size_bytes`, `tags`; in `list_messages`: `from: null`, `subject: null`, `has_attachment: null`; in `list_drafts`: `draft_id`, `revision`, `editable`, `subject: null`; `redacted_fields: ["envelope", "headers", "body", "attachments"]`.
   - `ENVELOPE`: + `from`, `to`, `cc`, `subject`, `message_id`, policy-filtered `labels` (Google); in `fetch_envelope`: `body: null`, `attachments: null`; in `list_messages`: `has_attachment: null`; `redacted_fields: ["headers", "body", "attachments"]`.
   - `HEADERS`: + RFC 5322 `headers` (`fetch_headers`); `fetch_envelope` sets `redacted_fields: ["body", "attachments"]`.
   - `BODY`: + `text_body`, `html_body`, `has_attachment: bool`, `attachments` metadata; `redacted_fields: ["attachment_bytes"]`.
   - `FULL`: + attachment byte delivery via `fetch_attachment`; `redacted_fields: []`.

## Consequences

### Positive

- **Provable non-interference.** Adding, editing, or deleting `NONE` messages leaves every caller-observable listing, search, count, and introspection output byte-for-byte unchanged.
- **Accurate Google completeness.** `[Gmail]/All Mail` and account-wide caps never falsely claim `"complete"`.

### Negative

- Callers cannot learn exact counts of hidden messages; they see static `completeness` instead.

### Neutral

- Calendar bounds (`since`, `before`) and `scope: "recent" | "all"` remain available over visible mail.

## Security Implications

- **Attack surface.** Closes pre-policy count/predicate oracles, `METADATA`-to-`ENVELOPE`/`BODY` probing, and rule-index enumeration.
- **Trust boundaries.** Projection and `redacted_fields` depend strictly on `(eff_vis(m), tool)`, never on hidden MIME structure.
- **Data exposure.** Zero bits of information about `NONE` objects cross the MCP response boundary.
- **Failure modes.** Malformed dates or mixed window types fail closed (`-32602`) before IMAP I/O.
- **Auditability.** Suppressing one or more messages during search/list sets `suppressed_by_policy: true` in the operator audit log ([ADR 0048](0048-pre-effect-audit-and-test-boundary.md)).

## Alternatives Considered

- **Emit `filtered_out` only when `criteria` is empty.** Rejected: time-delta polling still leaks arrivals of `NONE` mail.
- **Emit coarse hidden-count buckets (`"0"`, `"1-10"`, `"10+"`).** Rejected: the `0 -> 1-10` step remains a 1-bit existence oracle for any targeted predicate.
- **Optional per-folder `disclose_hidden_counts: true` toggle.** Rejected: re-opens the predicate oracle whenever enabled on a folder with caller criteria.

## References

- Supersedes [ADR 0002](0002-linear-visibility-levels.md), [ADR 0017](0017-response-transparency-for-filtered-data.md), and [ADR 0036](0036-search-window-transparency-and-calendar-dates.md).
- [ADR 0040](0040-message-centric-policy-and-gmail-composition.md) — Google multi-label composition.
- [ADR 0041](0041-direction-aware-rule-grammar-and-provenance.md) — predicate floors.
