# ADR 0037: Trace Payload Modes

- **Status:** Accepted
- **Date:** 2026-11-03
- **Deciders:** Randy Gupta

## Context

Each tool call produces a span `tool.<name>`. Today it carries the
complete request arguments, draft bodies and recipients included, and a
response filtered by a short key list that still lets subjects and
addresses of `fetch_envelope` and `fetch_body` through. The audit log,
by contrast, never records content ([ADR 0021]).

The company observability standard forbids personal data in telemetry.
At the same time, an operator who alone reads the trace store gains a
great deal from complete payloads when finding out why an agent did what
it did. The package is public; other operators may share their trace
store.

## Decision

**The environment variable `IMAP_MCP_TRACE_PAYLOADS` selects `off`,
`redacted` or `full`; the default is `redacted`.**

- **`off`:** tool spans carry no `mcp.request` and no `mcp.response`.
- **`redacted`:** `mcp.request` holds the arguments exactly as the audit
  log records them; `mcp.response` holds the response without message
  content, addresses, subjects, headers, bodies or attachment data.
- **`full`:** both attributes hold the complete JSON, each cut at
  `IMAP_MCP_TRACE_PAYLOAD_MAX_CHARS` characters (default 16384) and then
  marked `…[truncated <n> chars]`.
- Every tool span carries `imap_mcp.trace.payloads` with the active
  mode, so absence can be told from redaction.
- Secrets from the secret store never appear in any mode.
- An unknown value stops the server at startup with a message naming the
  variable and the allowed values.

`full` is a recorded, deliberate deviation from the observability
standard: it is opt-in per process, visible on every span, and meant
for trace stores only the mailbox owner can read.

## Consequences

### Positive

- Safe by default for every installation.
- Complete payloads on demand, without a code change.

### Negative

- Operators who relied on the old request payloads must set `full`.

### Neutral

- Spans, timings and structure are identical in all modes.

## Security Implications

- **Data exposure:** `redacted` and `off` keep mail content out of
  telemetry; `full` puts it into the trace store, which then must be
  protected like the mailbox itself.
- **Failure modes:** a typo cannot silently widen or narrow exposure;
  the server refuses to start.
- **Trust boundaries:** the trace store becomes part of the mailbox's
  trust boundary only in `full` mode.

## Alternatives Considered

- **Remove payloads entirely.** Rejected by the owner: they are the most
  useful debugging signal for a private trace store.
- **Default `full`.** Rejected: unsafe for a public package.
- **Fall back to `redacted` on an unknown value.** Rejected: a typo in
  `full` would go unnoticed.

## References

- [ADR 0021]
- `.scaratec/handbuch/observability.md` §1, §7
- `bdd/features/observability/tracing_payload_mode.feature`

[ADR 0021]: 0021-audit-log-format.md
