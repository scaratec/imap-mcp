# ADR 0021: Audit Log Format

- **Status:** Accepted
- **Date:** 2026-04-20
- **Deciders:** Randy Nel Gupta

## Context

Every PDP decision ([ADR 0001]) and every saga transition
([ADR 0006]) must be recorded so that:

- Operators can retroactively answer "what did caller X do between
  times T1 and T2?".
- A forensic reviewer can reconstruct policy application without
  re-running the server.
- Post-incident analysis can distinguish "caller was within policy"
  from "policy was wrong" from "server was misconfigured".

Competing concerns:

- The audit log must not itself be a leak vector. Logging message
  bodies or subject text would defeat the very data-protection
  posture the server exists to enforce.
- Manipulation of an audit log by an attacker who obtains the
  server's user account would be devastating; the log must be
  tamper-evident.
- Structural choices (format, rotation, access) shape everything
  that consumes the log downstream.

## Decision

The server writes a **structured JSONL audit stream** with a
per-record **hash chain**, one file per UTC day, rotated at day
boundaries, under a closed-vocabulary schema. Field selection is
governed by a strict **no-content-leak rule**.

### Format

- One JSON object per line (JSONL). No wrapping array, no pretty
  printing.
- UTF-8. Unix line endings.
- One file per UTC calendar day: `audit/YYYY-MM-DD.jsonl`.
- Files opened with `O_APPEND`. Every record written with `fsync`
  before acknowledging the originating operation.
- Permissions: directory `0700`, current file `0600`, closed files
  `0400`. Owned by the server's user.

### Record schema

```json
{
  "ts":                  "2026-04-20T13:45:12.842Z",
  "seq":                 1294,
  "prev_hash":           "sha256:7a8f…",
  "caller_id":           "invoice-agent",
  "caller_addr":         "stdio:pid=48211",
  "tool":                "fetch_body",
  "args_summary":        {"account": "…", "folder": "…", "uid": 1234},
  "decision":            "ALLOW",
  "reason":              "rule_matched",
  "visibility_granted":  "BODY",
  "redacted_fields":     ["attachment_parts"],
  "tx_id":               null,
  "result":              "OK",
  "latency_ms":          87
}
```

Field semantics:

- `ts`: RFC 3339, UTC, millisecond precision.
- `seq`: monotone within a single daily file; resets at day roll.
- `prev_hash`: SHA-256 over the canonical serialization of the
  *previous* record (deterministic JSON canonicalisation; fields
  sorted). The first record of a day chains to the `final_hash`
  of the previous day's file.
- `caller_id`: as resolved in [ADR 0015].
- `caller_addr`: transport-shaped descriptor. stdio uses
  `stdio:pid=<n>`; HTTP uses `http:<ip>:<port>`. Never contains
  user-supplied data unmodified.
- `tool`: MCP tool name or an internal event name
  (`token_refresh`, `pool_drain`, `policy_reload`,
  `saga_transition`).
- `args_summary`: a policy-relevant subset only. **Never** contains
  body bytes, subject text, attachment filenames, or any sender
  address belonging to a DENY record (see below).
- `decision`, `reason`, `visibility_granted`, `redacted_fields`: as
  defined by [ADR 0017].
- `tx_id`: saga transaction reference, when applicable.
- `result`: `OK` or `ERROR`. Errors carry an `error_type` sibling
  ("imap_timeout", "target_server_unavailable", ...) but no stack
  traces.
- `latency_ms`: wall-clock duration from request receipt to
  response emission.

### No-content-leak rule

The following never appear in audit records under any tool:

- Message body text, HTML, raw RFC822 bytes.
- Subject text. A hash `subject_sha256` may appear in saga records
  for idempotency forensics, never the text itself.
- Attachment filenames, MIME types in full, or byte content.
- Cleartext sender addresses in DENY records caused by sender
  filtering (this would leak the blacklist or the whitelist gap).
  Such records substitute `from_domain_sha256` if a hash is
  useful for diagnostics; otherwise the sender is omitted entirely.
- OAuth tokens, refresh tokens, `shared_token` values, or any
  derived material.
- Search query text. A `search_query_digest` (SHA-256 of the
  canonicalized predicate set) may appear for pattern analysis;
  the raw query does not.

### Rotation

At UTC midnight:

1. The current day's last record is followed by a terminal
   `eof_day` record containing the `final_hash`.
2. The file is closed and its mode changed to `0400`.
3. A new file `audit/<new-date>.jsonl` is created; its first
   record's `prev_hash` is the just-written `final_hash`.

The chain therefore spans day boundaries; offline verification
processes every file in order.

### Internal events

In addition to tool-call records, the server emits:

- `policy_reload` — outcome, validation summary (success/failure
  with error count), files changed.
- `token_refresh` — account, outcome, new expiry.
- `saga_transition` — tx_id, old state, new state, step detail.
- `pool_drain` — account, reason, in-flight count at drain.
- `auth_failed` — caller_addr, reason code, no content.

These events share the common record shape; `tool` carries the
event name.

## Consequences

### Positive

- **Structured, greppable, pipe-friendly.** Standard Unix tools
  (`jq`, `grep`, `awk`) handle it. Log aggregators ingest
  without translation.
- **Tamper-evident.** Any modification of an old record breaks
  the hash chain from that point forward. Offline verification
  is deterministic.
- **Minimal content exposure.** The schema is the contract; an
  implementation that logs more than the schema permits fails
  schema validation in CI.
- **Cross-cutting uniformity.** Every tool and every internal
  event emits the same shape; downstream consumers need one
  parser.

### Negative

- **`fsync` per record has a throughput cost.** Measurable under
  very heavy load; acceptable because audit throughput is bounded
  by tool-call throughput. Batching is a future optimization with
  its own ADR.
- **Hash chain complicates log rotation tooling.** Operators who
  move old files to archival storage must preserve ordering.
  Documented in the operator manual.

### Neutral

- The log format is stable — a new field requires an ADR-level
  change because downstream tooling parses it.

## Security Implications

- **Audit cannot betray what the server protects.** The
  no-content-leak rule is rigid: no feature request justifies
  relaxing it without superseding this ADR.
- **Every PDP outcome is recorded.** Coverage is total for
  policy-relevant events; partial logging would leave audit gaps
  that could mask misuse.
- **Tamper-evidence is real but bounded.** An attacker with
  continuous write access to the log and enough time can
  re-hash the entire file and replace the tail. Defence requires
  off-host copies, covered under [ADR 0022].
- **Correlation identifiers are hashed where they could leak.**
  Sender domains in DENY records, subject text in saga forensics,
  search queries in analytics — all as hashes. Reversible only
  for known plaintexts.
- **Caller addresses are normalized.** `caller_addr` cannot be
  arbitrary user-supplied strings; it is server-constructed from
  trusted transport metadata.
- **`fsync` per record** is a choice in favour of durability over
  throughput. An attacker who crashes the server cannot cause a
  record to be silently lost.

## Alternatives Considered

- **Plaintext free-form logs.** Rejected; unparseable, prone to
  content leakage, impossible to validate against a schema.
- **Protocol Buffers / Arrow for efficiency.** Rejected; a
  compactness gain that buys us nothing given audit volume and
  costs us Unix-tool compatibility.
- **Log to syslog / journald only.** Rejected; both are host-
  global with ambiguous rotation and permission semantics. JSONL
  under the server's state directory is a cleaner deployment
  contract.
- **No hash chain.** Rejected; tamper-evidence is cheap here and
  a real incident-response capability.
- **Log message content when the operator opts in.** Rejected
  categorically; operator convenience does not justify removing a
  core property of the system.

## References

- [ADR 0001] — every PDP evaluation produces an audit record.
- [ADR 0006] — saga transitions this log records.
- [ADR 0015] — caller identity resolution.
- [ADR 0017] — reason-code vocabulary shared with transparency
  fields.
- [ADR 0022] — retention, access, and (future) off-host
  externalization of the log.
- RFC 3339 — timestamp format.

[ADR 0001]: 0001-default-deny-hierarchical-policy.md
[ADR 0006]: 0006-cross-account-move-via-saga.md
[ADR 0015]: 0015-caller-identity-and-authentication.md
[ADR 0017]: 0017-response-transparency-for-filtered-data.md
[ADR 0022]: 0022-audit-retention-and-access-model.md
