# ADR 0028: Attachment File-Sink Delivery

- **Status:** Accepted
- **Date:** 2026-06-08
- **Deciders:** Randy Nel Gupta

## Context

`fetch_attachment` in the 1.0.0 surface delivers the raw attachment
bytes inline in the MCP `CallToolResult`, wrapped as an
`EmbeddedResource(BlobResourceContents(...))` next to the JSON text
payload. The bytes are base64-encoded into the resource's `blob`
field; the resource carries a `attachment://<account>/<folder>/<uid>/
<filename>` URI that no part of the system actually resolves.

Two independent failure modes have surfaced in practice:

1. **Caller-side context exhaustion.** A typical PDF attachment is
   single-digit megabytes; base64-encoded it inflates to ~1.35×.
   Claude Code, which renders `EmbeddedResource` inline, accepts the
   data but it occupies the agent's chat context. A single
   `fetch_attachment` on a 4 MB PDF consumes ~5.4 MB of context — and
   the agent has no way to know the size before issuing the call.

2. **Caller-side protocol reject.** Gemini-CLI forwards the MCP tool
   response into a `functionResponse.response` field on the Google
   API. The schema validator on that endpoint does not accept the
   `EmbeddedResource(type: "resource")` content variant; the request
   fails with HTTP 400 before the model sees anything. The agent
   reports a stack trace from inside `streamWithRetries`, not a
   usable diagnostic from the IMAP server.

Both failures are symptoms of one design gap: the server inlines a
blob into the tool result without a contract for how the caller's
host environment can consume it. The MCP surface did not pick a
delivery channel — the channel emerged from `_emit` packing whatever
fields the handler put into `_blob`, `_blob_mime_type`, and `_blob_uri`.

A decision is needed because (a) the Gemini-CLI failure makes
`fetch_attachment` non-functional for a real second client, (b) the
context-exhaustion problem stops the Claude-Code workflow from
processing multi-attachment messages, and (c) the gap was never
debated in an ADR — it was inherited from an early implementation
that no decision document anchors.

## Decision

We replace inline blob delivery with a **server-side file sink**:
`fetch_attachment` writes the decoded bytes to a configured
directory on the server's filesystem and returns the file name in
the response. The caller reads the file directly. The inline
`_blob`/`_blob_mime_type`/`_blob_uri` fields are removed; the
`EmbeddedResource` content block is no longer emitted.

This is a Hard Cut. `fetch_attachment` cannot be invoked without a
configured, writable sink — there is no inline fallback. The change
ships in tool-set version `2.0.0`.

### 1. Configuration

A single new configuration setting, `attachment_sink_directory`, is
read from the server config at startup. It is an absolute path. No
other path is accepted — see [LIM-0013].

Resolution order:

- `attachment_sink_directory` set in the server config → use it.
- not set → `fetch_attachment` becomes non-functional but remains
  listed; calls return ALLOW + ERROR with `error.type:
  "sink_not_configured"` and a `detail` that names the missing
  setting. The tool description on `list_tools` reflects the
  unconfigured state so the agent learns the situation before its
  first call.

The caller cannot override the directory via a tool argument. This
is the most important security boundary in this ADR: a caller-
chosen path would let an LLM agent write attachment bytes to
arbitrary filesystem locations — straight into the operator's
process boundary. The setting is operator-only.

### 2. Filename construction

The on-disk filename is

  `<sanitized_base>_<8hex>.<sanitized_extension>`

with the following rules:

- **Hash**: lowercase hex, first 8 characters of `md5(bytes)`. The
  hash is computed over the **decoded attachment bytes** — the same
  bytes the caller will read. The choice makes the operation
  idempotent: a re-fetch of the same attachment produces the same
  filename and overwrites the same file. Two different attachments
  that happen to share the original `Content-Disposition` filename
  end up as two distinct files because their hashes differ.

- **Sanitization**: any character outside `[A-Za-z0-9._-]` in the
  caller-supplied filename is replaced with `_`. Leading dots are
  stripped (no hidden files). The extension is taken from the
  trailing `.<ext>` of the sanitized name; if absent, the file
  has no extension. Path separators (`/`, `\`) are stripped along
  with the rest of the unsafe set, so directory traversal is
  structurally impossible regardless of what the MIME envelope
  asserts.

- **Length cap**: the full filename (base + `_` + 8 hex + `.` +
  extension) is bounded to 255 bytes — the per-name limit on ext4,
  ext3, XFS, and NTFS. If the sanitized base name exceeds 200 bytes
  the base is truncated to 200 bytes before the hash + extension
  are appended; this leaves at least 55 bytes of headroom for the
  hash, the dot, the extension, and the underscore. Truncation is
  byte-based, not character-based; pathological UTF-8 names are
  bounded correctly even if some glyphs disappear.

  Example: `The_LINK_Family_Manual.pdf` →
  `The_LINK_Family_Manual_6508477c.pdf`.

### 3. Sink health check

The server stat's the configured directory at two moments and
treats failure as user-visible state, not as a stack trace:

- **On every `list_tools` request.** The tool description for
  `fetch_attachment` is computed dynamically from the live sink
  state. If the directory is missing or not writable, the
  description names the exact problem ("sink directory
  /path/to/x does not exist", "sink directory /path/to/x is not
  writable by user uid=…"). The agent learns the failure mode
  before its first call.

- **On every `fetch_attachment` invocation, after the full
  authorization chain.** Same stat, same diagnostic. The call
  returns ALLOW + ERROR with `error.type: "sink_not_writable"`
  (or `sink_not_configured`) and the same `detail` string. The
  agent never has to read server logs to understand why a fetch
  failed.

  Ordering is deliberate: folder PDP, envelope fetch, sender-rule
  decision, and the visibility >= FULL check all run before the
  sink is consulted. A caller who is not authorized at any of
  those layers gets the same `folder_hidden` / `sender_not_*` /
  `visibility_below_FULL` response they get for every other
  tool, regardless of how the sink is configured. Sink
  diagnostics surface only to callers that have already proved
  their right to the bytes.

A `stat()` plus an `os.access(..., os.W_OK)` is single-digit
microseconds; running it on each call costs nothing relative to
the IMAP fetch that follows.

### 4. Response shape

The successful response carries:

  - `account`, `folder`, `uid`, `part_id`: unchanged
  - `mime_type`, `size_bytes`, `content_hash`: unchanged (the
    hash here remains the full sha256 of the bytes, distinct
    from the 8-hex md5 prefix in the filename)
  - `saved_to`: the **filename only**, no path component. Example:
    `"The_LINK_Family_Manual_6508477c.pdf"`.

The absolute sink path is **not** repeated in the response. It is
in the tool description, which the agent learned once at
`list_tools` time. Repeating it on every fetch wastes tokens, and
this matters for the bulk-shaped use cases: an agent walking 30
attachments through `list_attachments` + `fetch_attachment` pays
30× the path length on top of 30× the filename. The same argument
applies on the server side to audit-log sanitization and on the
caller side to model context.

The audit log is the exception. The full absolute path is written
to the audit record (`saved_to_absolute`) so a forensic reviewer
can answer "what file was created" without having to also know
the server's sink config at the time of the call. The audit
record is operator-side; token-budget concerns do not apply.

### 5. Removed fields

`_blob`, `_blob_mime_type`, `_blob_uri` are gone. The `EmbeddedResource`
content block is no longer emitted. `dispatch.py::_emit` is
simplified to text-only. Any caller that read the inline blob bytes
must migrate to reading from the sink filesystem.

## Consequences

### Positive

- **Gemini-CLI works again.** The tool response is a plain JSON
  object with string fields; no exotic content variants. Google's
  endpoint accepts it without schema-validation rejection.
- **Agent context stays small.** A `fetch_attachment` call costs
  ~150 bytes of response text instead of ~5 MB of inline blob.
  Multi-attachment workflows that were impossible before now fit.
- **The agent is never surprised by size.** `list_attachments`
  already returns `size_bytes` per part at BODY visibility; the
  agent decides whether to fetch based on the size before paying
  the I/O. With the sink, the size cost is moved out of the
  context entirely.
- **Idempotent re-fetch.** md5-of-bytes in the filename means a
  second `fetch_attachment` on the same UID/part overwrites the
  same file. The sink does not grow on retry.
- **Single security boundary.** The sink directory is operator-
  controlled, not caller-influenced. A prompt-injected agent
  cannot write attachment bytes to `/etc/cron.d/` because the
  output path is not in its grammar.
- **Filename-level sanitization is structural.** Path separators,
  leading dots, and Unicode control characters all collapse to
  `_` before they touch the filesystem. Directory traversal via
  a malicious `Content-Disposition` is impossible.

### Negative

- **Caller and server must share a filesystem.** The agent reads
  the written file; the server writes it. In any deployment
  where the two run on different hosts (Docker without volume
  mount, remote SSH, containerized agent), this design does not
  work. See [LIM-0014].
- **The sink directory accumulates files.** No retention is
  managed by the server. The operator is responsible for cleanup.
  See [LIM-0015].
- **The sink path is visible to every caller via the tool
  description.** A multi-tenant deployment cannot hide the
  filesystem path from any caller that can list tools. See
  [LIM-0016].
- **Hard Cut.** Existing 1.0.0 callers that consumed the inline
  blob break. The `tool_set_version` bump to 2.0.0 is the signal;
  there is no compatibility mode.

### Neutral

- The `attachment://...` URI scheme that was used as the
  `BlobResourceContents.uri` is gone. It was never resolvable
  anyway — no `resources/read` handler was registered.
- `list_attachments`, `fetch_envelope`, `fetch_headers`,
  `fetch_body` are unchanged. The Sink is `fetch_attachment`'s
  delivery channel only.

## Security Implications

- **Attack surface.** The caller cannot influence the output path
  in any way — neither directory nor filename. The operator picks
  the directory; the server picks the filename from the (decoded,
  sanitized) MIME envelope plus a content hash. The blast radius
  of any caller-side compromise is bounded to writes inside that
  one directory, with names that are guaranteed to match the
  `[A-Za-z0-9._-]_hex.ext` pattern.
- **Trust boundaries.** Crossing from "data the server holds" to
  "data the caller can access" now goes through the filesystem.
  The operator's filesystem permissions on the sink directory
  ARE the access control for the data the directory holds. This
  is a different posture from the inline-blob world, where
  access was bound to the MCP session.
- **Data exposure.** Anything written to the sink is then readable
  by every process with read access to the directory. If the
  caller is one process and the operator's user is another, both
  see all attachments any caller fetches. Tightening this
  requires per-caller subdirectories or per-call temp files —
  out of scope for this ADR.
- **Failure modes.** Sink missing, sink not writable, sink full
  (ENOSPC), sink path is actually a regular file — every one
  surfaces as `sink_not_writable` with the underlying errno in
  `error.detail`. No path silently falls back to inline-blob;
  there is no inline-blob mode any more.
- **Audit.** The audit record gets the full absolute path of every
  file written, plus the byte size and content hash that
  `fetch_attachment` already records. A forensic reviewer can
  reconstruct what each caller pulled and where it ended up
  without server logs.

## Alternatives Considered

- **MCP `resources` API with a real `resources/read` handler.**
  The server keeps blobs in memory or in a server-side cache,
  returns a `ResourceLink` from `fetch_attachment`, and serves
  bytes when the client calls `resources/read` on the link.
  Rejected: requires a per-resource session lifecycle, TTL
  management, and out-of-context-window plumbing in every MCP
  client. Two real clients (Claude Code, Gemini-CLI) handle
  `resources/read` very differently; the simple-and-portable
  story is a file on disk.
- **Per-call HTTP sink endpoint** (server runs a small HTTP
  server that exposes blobs at one-time-token URLs). Rejected
  for V1: needs auth model, port management, TLS for any
  cross-host story, and turns an IMAP-tools project into a
  general-purpose blob delivery system. Larger than the problem.
- **Per-caller subdirectories under the sink.** Rejected:
  multiplies the operator's mkdir/chmod burden, and the
  authorization model is currently caller-identity-driven at
  the MCP layer, not filesystem-identity-driven. Mixing the
  two creates a second source of truth for "who can see what".
- **Inline blob with a server-side size threshold** (small
  blobs inline, large ones to sink). Rejected: two modes the
  caller must reason about, and the threshold is wrong for
  every caller (Gemini rejects every size; Claude Code accepts
  every size up to context exhaustion).
- **Per-call sink path argument.** Rejected categorically: the
  prompt-injection threat model says a caller-chosen path is
  unbounded write to anywhere the server user has access. The
  whole point of the operator-only setting is to take this
  vector off the table.
- **Return both `saved_to` (filename) and `sink_path` (absolute)
  on every call.** Rejected on token-budget grounds for bulk
  workflows; the path is constant across all calls in a
  session and belongs once in the tool description, not 30×
  in 30 responses.

## References

- [ADR 0016] — original tool set; the `fetch_attachment` entry
  there said "Single attachment part" without specifying the
  delivery channel. This ADR fills that gap.
- [ADR 0018] — non-goal tool surface; `raw_imap_command` and
  `fetch_raw_rfc822` remain forbidden. The sink is for legitimate
  attachment bytes inside the FULL visibility envelope, not a
  raw-bytes backdoor.
- [ADR 0026] — `list_attachments` is the discovery tool the agent
  consults for size before deciding to fetch. The sink works
  because the size question is answered before the fetch.
- [ADR 0027] — error envelope; `sink_not_configured` and
  `sink_not_writable` join the closed `error.type` enumeration.
- [LIM-0013] — only one sink path is supported.
- [LIM-0014] — caller must have filesystem access to the sink.
- [LIM-0015] — sink cleanup is the operator's responsibility.
- [LIM-0016] — sink path is exposed in the tool description.

[ADR 0016]: 0016-mcp-tool-set.md
[ADR 0018]: 0018-non-goal-tool-surface.md
[ADR 0026]: 0026-tool-surface-consistency.md
[ADR 0027]: 0027-error-envelope-and-tool-surface-versioning.md
[LIM-0013]: ../limitations/0013-single-attachment-sink-path.md
[LIM-0014]: ../limitations/0014-attachment-sink-requires-caller-filesystem-access.md
[LIM-0015]: ../limitations/0015-attachment-sink-cleanup-out-of-scope.md
[LIM-0016]: ../limitations/0016-attachment-sink-path-disclosed-in-tool-description.md
