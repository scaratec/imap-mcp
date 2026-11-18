# ADR 0048: Pre-Effect Hash-Chained Audit Trail

- **Status:** Accepted
- **Date:** 2026-11-17
- **Deciders:** Randy Nel Gupta

## Context

Review of the audit subsystem ([ADR 0021](0021-audit-log-format.md), [ADR 0022](0022-audit-retention-and-access-model.md), [ADR 0032](0032-shared-audit-chain-across-local-instances.md)) identified six defects:

1. Write handlers mutated IMAP or the attachment sink before writing to the audit log, leaving unlogged side-effects if auditing failed.
2. JSON-RPC `-32602` schema rejections and unhandled runtime exceptions wrote no audit entries.
3. HTTP requests logged `stdio:pid=<pid>` instead of `http:<ip>:<port>` in `caller_addr`.
4. Retention rotation was never invoked in production, and `warm_days` was redundant alongside `hot_days` and `delete_after_days`.
5. Unsalted SHA-256 `search_query_digest` allowed offline dictionary guessing of domains and keywords.
6. `external_root_hook` executed through a shell inheriting process environment secrets.

## Decision

We serialize all local instances onto **one exclusive-locked daily JSONL hash chain that fsyncs an `intent` record before every write/artifact side-effect, audits all schema and runtime errors, computes keyed HMAC digests, and executes retention and shell-free root hooks automatically on UTC day seal**.

### 1. Pre-effect `intent`, post-effect `outcome`, and total request coverage

- **Write and artifact tools** (`mark_seen`, `bulk_mark_seen`, `mark_tagged`, `bulk_mark_tagged`, `move`, `copy`, `create_draft`, `create_reply_draft`, `update_draft`, `discard_draft`, and `fetch_attachment` once `FULL` is verified) write two chained records sharing `request_id` (`uuid4`):
  1. **Before any IMAP write or file creation:** `phase: "intent"`, `decision: "ALLOW"`, `result: "PENDING"`. If auditing fails, the handler aborts immediately with **zero external side-effects**.
  2. **After completion:** `phase: "outcome"`, `decision`, `reason`, `result` (`"OK" | "ERROR"`), `error_type` (when `ERROR`), `latency_ms`.
- **Read/meta tools and pre-execution `DENY`s** write one record (`phase: "single"`). Unwritable audit storage fails read and write calls closed.
- **Schema and runtime errors:** JSON-RPC `-32602` rejections emit `decision: "DENY"`, `reason: "invalid_arguments"`, `result: "ERROR"`; unexpected exceptions emit `decision: "ALLOW"`, `reason: "internal_error"`, `result: "ERROR"`, `error_class: "<ExceptionType>"` (never the exception message).

### 2. No-content-leak schema and keyed HMAC digests

- Message bodies, HTML, raw RFC822, cleartext subjects, attachment filenames/paths, cleartext addresses on `DENY`s, and secrets are forbidden in every record.
- Using the 32-byte key from `provenance_hmac_key` ([ADR 0042](0042-cryptographic-and-wal-provenance.md); hex-decoded when stored as 64 hex characters), `search_query_digest` is `"hmac-sha256:" + hex(HMAC-SHA256(key, canonical_json_utf8(criteria)))` (with sorted keys and compact separators `","` / `":"` over the `criteria` object only), and `from_domain_digest` is `"hmac-sha256:" + hex(HMAC-SHA256(key, lowercase_domain_utf8))`.
- `caller_addr` records `stdio:pid=<pid>` on stdio and `http:<client_ip>:<client_port>` on HTTP.

### 3. Shared lock, automatic day-seal retention, and scrubbed root hook

- Every append acquires an exclusive file lock on `<audit_dir>/.audit.lock` (`0600` in `0700` directory), reads `seq` and `prev_hash` (`"sha256:" + hex(SHA-256(raw_utf8_line_with_lf))`, genesis `"sha256:" + "0"*64`), and seals past UTC day files idempotently with one `eof_day` trailer (`0400`).
- Whenever past UTC days are sealed (at startup or first write after UTC midnight), retention runs automatically under the lock using two thresholds (`hot_days`, default `90`; `delete_after_days`, default `365`, `delete_after_days > hot_days`): plain `.jsonl` files with `age_days > hot_days` are gzipped to `.jsonl.gz` (`0400`), and archives with `age_days > delete_after_days` are unlinked with a `retention_delete` record (`reason: "retention_delete"`). Optional legacy `warm_days` in config is accepted only when `hot_days + warm_days == delete_after_days`.
- `external_root_hook` executes directly as an argument vector without a shell under a minimal scrubbed environment (`PATH`, `LC_ALL`) with a 5-second timeout.

## Consequences

### Positive

- **Zero unlogged mutations.** Unwritable audit storage aborts writes before IMAP or filesystem changes occur.
- **Automatic retention in production.** Aged files compress and expire on UTC day roll without external cron jobs or test hooks.
- **Dictionary-resistant digests and secret-safe hooks.** Keyed HMACs protect query terms, and root hooks never inherit secrets.

### Negative

- Write/artifact calls perform two `fsync` operations (`intent` + `outcome`), adding ~1–2 ms on local SSDs.

### Neutral

- Test-mode control hooks are separated and gated in [ADR 0058](0058-closed-test-mode-control-boundary.md).

## Security Implications

- **Attack surface.** Removes shell invocation and secret inheritance from `external_root_hook`.
- **Trust boundaries.** Enforces durable audit `intent` ahead of every external mutation.
- **Data exposure.** Keyed HMAC digests prevent offline brute-forcing of `search_query_digest` and `from_domain_digest`.
- **Failure modes.** Clock rollback onto a sealed `0400` file or unwritable storage fails closed; out-of-band active-file deletion logs critically and emits `audit_file_missing`.
- **Auditability.** Covers 100 % of MCP traffic including `-32602`, `auth_failed`, and `internal_error`.

## Alternatives Considered

- **Post-handler single audit write ([ADR 0021](0021-audit-log-format.md)).** Rejected: leaves unlogged IMAP mutations when disk/permissions fail.
- **Separate background retention daemon.** Rejected: running retention during day sealing under `.audit.lock` needs zero extra processes.
- **Unsalted SHA-256 digests.** Rejected: trivially reversible for domain names and short search keywords.

## References

- Supersedes [ADR 0021](0021-audit-log-format.md), [ADR 0022](0022-audit-retention-and-access-model.md), and [ADR 0032](0032-shared-audit-chain-across-local-instances.md).
- [ADR 0042](0042-cryptographic-and-wal-provenance.md) — server HMAC key.
- [ADR 0047](0047-per-caller-symlink-safe-attachment-delivery.md) — `artifact_id` / `artifact_sha256`.
- [ADR 0058](0058-closed-test-mode-control-boundary.md) — test-mode boundary.
