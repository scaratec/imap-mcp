# ADR 0027: Error Envelope and Tool-Surface Versioning

- **Status:** Accepted
- **Date:** 2026-06-05
- **Deciders:** Randy Nel Gupta

## Context

Every tool's response is a JSON object today. The shape of that object
diverges in two ways the original surface design did not constrain.

**The error envelope is per-handler.** A DENY response carries
`{decision: "DENY", reason: "<code>", ...}` with optional extras like
`missing_capability`, `forbidden_tags`, `required_scope`,
`granted_scope`. An ALLOW + ERROR response (e.g. `folder_not_found`
from `folder_stats`, `append_rejected` from `create_draft`) carries
`{decision: "ALLOW", result: "ERROR", error_type: "...", reason: "...",
imap_response: "..."}`. Per-handler variation extends to the *names*
of detail fields: `imap_response`, `missing_capability`,
`required_scope`. There is no single document that lists what an
error response can contain, and no compiler check that prevents a
handler from inventing a new field name.

When `folder_not_found` ([ADR 0025]) is replaced by three more
specific codes, the `error.detail` payload becomes more meaningful and
the per-handler ad-hocery becomes more painful: every handler that
opens a folder will have to learn the new shape, and the chance that
one of them gets it slightly wrong grows.

**The tool-set version is invisible.** [ADR 0016] declared a
`tool_set_version` and said callers could "inspect the version via a
standard MCP capability exchange". The implementation places
`TOOL_SET_VERSION = "1.0.0"` in `_common.py` but never advertises it:
no field in `serverInfo`, no meta-tool that returns it,
no field in `list_tools` output. The
`mcp_tool_discovery.feature` test asserts on `tool_set_version` and
will fail today against a real server because the field does not
exist (the test currently mocks the metadata).

A caller cannot pin a tool surface; a client library cannot detect a
1.0.0 hard cut at handshake time. Both have to fall back on probing
the tool list and inferring.

A decision is needed now because ADR 0026 is a Hard Cut to a 1.0.0
surface. Hard Cut without an exposed version means clients break with
no diagnostic; the right shape of the version field has to be settled
first.

## Decision

We define a **normalized error envelope** used by every tool, and we
expose `tool_set_version` through both the MCP `serverInfo.metadata`
and a new `tool_surface_info` meta-tool.

### 1. Normalized envelope

Every tool response conforms to one of three shapes:

```json
// DENY — policy refusal, no business payload
{
  "decision": "DENY",
  "reason":   "<reason_code>",
  "account":  "...",
  "folder":   "...",
  ...tool-specific identifying fields...
}

// ALLOW + OK — business payload follows
{
  "decision": "ALLOW",
  "result":   "OK",
  "reason":   "<reason_code>",
  ...tool-specific payload...
}

// ALLOW + ERROR — authorized but operation failed
{
  "decision": "ALLOW",
  "result":   "ERROR",
  "reason":   "<reason_code>",
  "error": {
    "type":   "<error_type>",
    "detail": "<human-readable single line>"
  },
  ...tool-specific identifying fields...
}
```

The four guarantees:

- `decision` is always present and is one of `"ALLOW"`, `"DENY"`.
- `reason` is always present and drawn from the canonical
  reason-code table in [ADR 0017] (as amended by [ADR 0025]).
- `result` is present iff `decision == "ALLOW"` and is one of
  `"OK"`, `"ERROR"`.
- The `error` field is present iff `result == "ERROR"`. Its `type`
  is a closed enumeration per tool family (see below); its `detail`
  is a single human-readable line bounded to 256 characters and
  contains no caller-controlled echo nor server-internal paths.

#### Error types (closed enumeration)

| Family              | `error.type` values                                                              |
|---------------------|----------------------------------------------------------------------------------|
| Folder operations   | `folder_absent`, `select_failed`                                                 |
| Append (drafts)     | `append_rejected`, `append_timeout`, `append_failed`                             |
| Reply construction  | `uid_not_found`, `empty_reply_text`                                              |
| Attachment access   | `attachment_not_found`                                                           |
| Attachment modify   | `uid_not_found`, `attachment_not_found`, `rewrite_failed`                        |
| Move/copy           | `saga_aborted`, `transient_imap_failure`                                         |

The enumeration is closed in the same sense as the reason-code table:
adding a value requires an ADR amendment. Per-handler ad-hoc fields
like `imap_response`, `required_scope`, `granted_scope`,
`forbidden_tags`, `missing_capability` are dropped in favour of
either the `reason` code (for purely categorical signals) or the
`error.detail` line (for triage strings).

#### Sender-blacklisted special case

The existing private `_matched_sender` field used by
`dispatch.py::_audit_tool_call` to hash the sender domain stays
internal; it never appeared in the response envelope and that does
not change. It is documented here so the rule "the envelope contains
exactly the fields listed above" is unambiguous.

### 2. `TOOL_SET_VERSION` is exposed

The constant moves from `handlers/_common.py` to `dispatch.py` and is
advertised in two places:

1. **MCP `serverInfo.metadata`** at handshake time:

   ```json
   "metadata": {
     "tool_set_version": "1.0.0",
     "package_version":  "0.16.0"
   }
   ```

   A client can pin or refuse at connection time without issuing any
   `tools/call`.

2. **`tool_surface_info` meta-tool** (new), returning:

   ```json
   {
     "decision": "ALLOW",
     "result":   "OK",
     "reason":   "folder_default_applied",
     "tool_set_version":   "1.0.0",
     "package_version":    "0.16.0",
     "protocol_revision":  "2024-11-05",
     "breaking_changes_since": [
       { "version": "1.0.0", "summary": "criteria + folder-path + envelope refactor (ADR 0024-0027)" }
     ]
   }
   ```

   The `breaking_changes_since` list is append-only across the project's
   lifetime and lets a client decide whether the current version is
   "close enough" to a pinned baseline.

The version follows SemVer: an additive change (new tool, new
reason-code) bumps minor; a breaking change (renamed tool, removed
reason-code, envelope-shape change, schema-tightening that rejects
previously-accepted input) bumps major.

`TOOL_SET_VERSION` jumps to `1.0.0` with the ADR 0024–0027 bundle.

## Consequences

### Positive

- **One envelope, one mental model.** A caller-side handler that
  reads `decision` and `result` covers every tool. Switch on
  `reason` for categorical handling, switch on `error.type` for
  recovery decisions.
- **Schema-level rejection of malformed responses, too.** A
  contract test that fuzzes the response shape can fail fast when a
  handler emits an unexpected key; the per-handler ad-hocery made
  this test impossible.
- **Clients can negotiate.** A client refusing 2.x can fail closed
  at the MCP handshake instead of running into surprise behaviour
  on the third tool call.
- **`tool_surface_info` is debuggable.** A caller (or a human at a
  prompt) can ask "what server am I talking to" without parsing the
  `list_tools` output.
- **Audit log is denser.** The `error_type` field becomes a closed
  enumeration; existing log queries that grepped strings continue
  to work.

### Negative

- **Existing callers break.** Any client that parsed
  `imap_response` directly must migrate to `error.detail`. The
  1.0.0 version bump signals this and `tool_surface_info` lets a
  client detect it before the first tool call.
- **The envelope is slightly larger.** Three nested fields where
  one flat field used to live. The size cost is constant and
  irrelevant against payload sizes; the readability win pays it
  back.
- **One more meta-tool to maintain.** `tool_surface_info` is tiny
  (no IMAP I/O, no PDP gate beyond "the caller is authenticated")
  but it is one more entry in the dispatch table.

### Neutral

- The reason-code closure rule from [ADR 0017] is preserved. The
  error-type closure rule mirrors it on the orthogonal axis
  (categorical refusal vs operational failure).

## Security Implications

- **Attack surface.** The closed `error.type` enumeration eliminates a
  vector where a future handler could leak server-internal strings
  in a custom field name. Every operational error is one of N known
  shapes; a security review enumerates them by reading the table.
- **Trust boundaries.** Unchanged. The version field is metadata
  about the surface, not about the caller's policy; it crosses no
  boundary that the surface itself does not already cross.
- **Data exposure.** `error.detail` is bounded to 256 characters
  and is sanitized at the `error_envelope` helper before emission:
  no caller-controlled echo of input strings (a sender's email
  address, a folder name from the caller) is permitted; only
  server-classified strings (IMAP status lines, the names of
  parts the server itself derived) appear. This eliminates a
  reflection vector where a malicious caller might try to ferry
  content out via the error line.
- **Failure modes.** A handler that forgets to use the
  `error_envelope` helper produces an envelope that fails the
  contract test in the BDD suite (which asserts the envelope shape
  on a per-tool basis). The CI gate catches drift before it ships.
- **Auditability.** The audit-log schema (ADR 0021) already records
  `decision`, `reason`, `result`, and `latency_ms`. It is extended
  to record `error.type` when present. The schema change is
  additive-only; existing log readers continue to work.

## Alternatives Considered

- **Keep per-handler envelopes, fix the version exposure in
  isolation.** Rejected: leaves the bigger problem unfixed and
  drives up the migration cost of every future bug fix that touches
  the response shape.
- **Define the envelope but skip the closed `error.type`
  enumeration ("any string is fine").** Rejected: indistinguishable
  from today in practice — the value is in the closure rule, not
  the field name.
- **Use HTTP-style codes (`error.code: "FOLDER_ABSENT"` instead of
  `reason` + `error.type`).** Rejected: confuses two axes. `reason`
  is the policy/categorical decision (closed by [ADR 0017]);
  `error.type` is the operational outcome (closed here). Collapsing
  them muddies which kind of recovery the caller should attempt.
- **Embed the version in every tool response (`tool_set_version` on
  every payload).** Rejected: bloat. A caller that wants to verify
  pins once does so at handshake; a caller that wants to query
  on-demand uses `tool_surface_info`.
- **Expose the version through a new MCP capability rather than a
  meta-tool.** Rejected: MCP capability strings are global and
  shape-free. A meta-tool that returns structured data is more
  forward-compatible (we can add fields without renegotiating
  capabilities).
- **Bump only to 1.1.0 ("this is additive, not breaking, the old
  envelope just doesn't appear anymore").** Rejected: removing
  `imap_response`, `missing_capability`, and `forbidden_tags` from
  the response is a breaking change for any client that looks at
  them. SemVer requires a major bump; calling it 1.1.0 would lie.

## References

- [ADR 0016] — original tool-set declaration; the versioning
  paragraph is extended here.
- [ADR 0017] — reason-code table; this ADR adds the orthogonal
  `error.type` enumeration but does not touch the reason-code
  closure.
- [ADR 0021] — audit-log format; the additive `error.type`
  recording is compatible with the existing schema.
- [ADR 0024] — duration grammar; shipped in the same 1.0.0 bundle.
- [ADR 0025] — folder-path contract and error taxonomy; the new
  `folder_absent` and `select_failed` codes are emitted via the
  envelope defined here.
- [ADR 0026] — tool-surface consistency; shipped in the same
  1.0.0 bundle.
- SemVer 2.0.0 — <https://semver.org/spec/v2.0.0.html>

[ADR 0016]: 0016-mcp-tool-set.md
[ADR 0017]: 0017-response-transparency-for-filtered-data.md
[ADR 0021]: 0021-audit-log-format.md
[ADR 0024]: 0024-duration-grammar-single-source.md
[ADR 0025]: 0025-folder-path-contract-and-error-taxonomy.md
[ADR 0026]: 0026-tool-surface-consistency.md
