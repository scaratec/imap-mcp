# ADR 0047: Isolated Symlink-Safe Attachment Delivery

- **Status:** Accepted
- **Date:** 2026-11-17
- **Deciders:** Randy Nel Gupta

## Context

[ADR 0028](0028-attachment-file-sink-delivery.md) wrote attachments into one shared directory under predictable `<base>_<md5_8hex>.<ext>` names, embedded the host path into `list_tools`, and logged attachment filenames in `saved_to_absolute`:

1. Predictable names without no-follow / exclusive-create descriptor semantics exposed server-writable files (`policies/*.yaml`, `wal.db`) to pre-planted symlink attacks when `/sink` was caller-writable.
2. Sharing one flat sink directory broke caller isolation ([LIM 0013](../limitations/0013-single-attachment-sink-path.md)).
3. 32-bit MD5 truncation collided under birthday bounds, and `list_tools` disclosed host layout before authorization ([LIM 0016](../limitations/0016-attachment-sink-path-disclosed-in-tool-description.md)) while `saved_to_absolute` leaked filenames into audit logs.

## Decision

We classify MIME attachments uniformly and deliver `FULL`-authorized parts into **per-caller `0700` subdirectories under 128-bit UUIDv4-prefixed filenames created relative to no-follow directory descriptors with exclusive `0400` permissions**, omitting host paths from `list_tools` and filenames from audit records.

### 1. Uniform MIME attachment classification

Walking the full MIME tree (including nested `multipart/alternative` and `multipart/related` containers), a non-multipart leaf part is an attachment iff (a) its `Content-Disposition` starts with `attachment`, (b) its `Content-Disposition` starts with `inline` and carries a filename parameter, or (c) its main MIME type is not `text` and it carries a filename parameter. Parts are indexed `0..k-1` in tree traversal order across `has_attachment`, `list_attachments`, `fetch_body.attachments`, `fetch_attachment(part_id)`, and quote exclusion in `create_reply_draft` ([ADR 0057](0057-immutable-received-mail-and-structured-drafts.md)).

### 2. Symlink-safe descriptor chain and exclusive `0400` creation

- `accounts.yaml` -> `attachment_sink.directory` configures an absolute base path (`~` expanded).
- `<base_dir>` and `<base_dir>/<caller_id>` (`0700`) are opened as directory file descriptors with no-follow semantics, verifying directory type and current-UID ownership; symlinks, missing base directories, or permission errors fail closed as `ALLOW + ERROR` (`sink_not_writable`, or `sink_not_configured` when unset).
- Each call generates a random 128-bit hex `artifact_id` (`32` lowercase hex chars), RFC 2047-decodes and sanitizes the MIME filename to `[A-Za-z0-9._-]` (collapsing `..+`, stripping leading dots, capping total leaf name to `<= 255` UTF-8 bytes as `<artifact_id>_<sanitized_base>.<ext>`), and creates the file relative to the caller directory descriptor with exclusive-create, no-follow, and mode `0400` semantics. Partial writes on `ENOSPC` unlink the incomplete inode before returning `sink_not_writable`.

### 3. Response, `list_tools`, and audit hygiene

- `list_tools` publishes a static contract description for `fetch_attachment` without any host filesystem path.
- `fetch_attachment` returns `account`, `folder`, `uid`, `uidvalidity`, `part_id`, `mime_type`, `size_bytes`, `content_hash` (SHA-256 hex), `artifact_id`, `saved_to` (`"<caller_id>/<filename>"`), and `saved_path` (canonical absolute path).
- Audit records log `artifact_id`, `artifact_sha256`, and `size_bytes` — never `saved_to_absolute` or the MIME filename ([ADR 0048](0048-pre-effect-audit-and-test-boundary.md)).

## Consequences

### Positive

- **Symlink, hardlink, and collision hazards eliminated.** Descriptor-relative no-follow opens plus 128-bit random prefixes and exclusive `0400` creation prevent redirection and collisions.
- **Zero host-path disclosure in `list_tools` and zero filename leak in audit.** Resolves [LIM 0013](../limitations/0013-single-attachment-sink-path.md) and [LIM 0016](../limitations/0016-attachment-sink-path-disclosed-in-tool-description.md).

### Negative

- Repeated fetches create distinct artifact files; operators clean aged files via OS timers or pod `emptyDir` lifecycle ([LIM 0015](../limitations/0015-attachment-sink-cleanup-out-of-scope.md)).

### Neutral

- Sidecar deployments mount `/sink` `readOnly: true` in the agent container ([ADR 0051](0051-loopback-kubernetes-sidecar-topology.md)).

## Security Implications

- **Attack surface.** Closes symlink overwrite attacks against `/state` and `policies/*.yaml`.
- **Trust boundaries.** Isolates each caller in a `0700` directory with `0400` read-only artifacts.
- **Data exposure.** Discloses `saved_path` only after `FULL` visibility is authorized, and keeps document titles out of audit JSONL.
- **Failure modes.** Symlinks, permission errors, and disk-full writes unlink partial files and fail closed (`sink_not_writable`).
- **Auditability.** Forensic correlation uses `artifact_id` and `artifact_sha256`.

## Alternatives Considered

- **Deterministic `<base>_<sha256_32hex>.<ext>` overwrite-in-place.** Rejected: predictable names allow pre-planted file/FIFO collisions when a directory is shared.
- **Inline base64 blobs in MCP responses.** Rejected: overflows LLM context windows on multi-megabyte PDFs.
- **Dedicated HTTP artifact download server.** Rejected: adds network attack surface unnecessary with descriptor-safe file delivery.

## References

- Supersedes [ADR 0028](0028-attachment-file-sink-delivery.md).
- Resolves [LIM 0013](../limitations/0013-single-attachment-sink-path.md) and [LIM 0016](../limitations/0016-attachment-sink-path-disclosed-in-tool-description.md).
- [LIM 0014](../limitations/0014-attachment-sink-requires-caller-filesystem-access.md), [LIM 0015](../limitations/0015-attachment-sink-cleanup-out-of-scope.md).
