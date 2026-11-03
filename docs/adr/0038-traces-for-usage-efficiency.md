# ADR 0038: Traces Carry What Usage Efficiency Needs

- **Status:** Accepted
- **Date:** 2026-11-03
- **Deciders:** Randy Gupta

## Context

To learn how efficiently agents use the server we need, per agent run:
the number of tool calls, the amount of response text the agent had to
read, the IMAP connections and commands behind it, and where the time
went. The audit log covers tool, decision and latency. Today's spans
cannot cover the rest: nothing ties them to a run, the MCP client is
not recorded although the handshake names it, response size and result
counts are missing, only connect and authenticate exist below the tool
span, and the batch exporter is not flushed when a harness ends the
stdio server, so the last calls of a run are lost.

## Decision

**Tool spans carry run correlation, client identity, size, counts and a
standard outcome, IMAP commands are child spans, and spans are flushed
on shutdown.**

- **Resource attributes** come from `OTEL_RESOURCE_ATTRIBUTES` (the SDK
  reads it) plus `service.name=imap-mcp`, `service.version` and
  `service.instance.id`.
- **Client identity:** `mcp.client.name` and `mcp.client.version` from
  the Initialize `clientInfo`.
- **Size and counts:** `mcp.response.size_bytes` is the UTF-8 length of
  the text content returned to the client. Listing tools add
  `mcp.result.matched`, `mcp.result.visible`, `mcp.result.returned` and
  `mcp.result.has_more`.
- **Outcome:** a policy denial sets `domain.outcome=rejected` and
  `domain.rejection.reason`, status unset. An error envelope sets
  `error.type` and status `ERROR`.
- **IMAP child spans:** `imap.connect`, `imap.authenticate`,
  `imap.select`, `imap.uid_search`, `imap.uid_fetch`, `imap.uid_store`,
  `imap.append`, `imap.expunge`, `imap.logout` and similar, one per
  command, under the tool span.
- **Flush:** the tracer provider is shut down, flushing the batch, when
  stdin closes and on `SIGTERM`.

## Consequences

### Positive

- An efficiency report can attribute every call, byte and round trip to
  a run, harness and model.
- IMAP round trips become visible, which the connection pool decision
  ([ADR 0013]) needs for a before/after comparison.

### Negative

- More spans per call; a trace store sees roughly one span per IMAP
  command.

### Neutral

- Attribute names extend the existing `mcp.*` attributes; aligning them
  with OpenTelemetry semantic conventions for MCP is left for later.

## Security Implications

- **Data exposure:** sizes, counts and client names are not personal
  data. Payload content follows [ADR 0037].
- **Failure modes:** a failing exporter never fails a tool call.

## Alternatives Considered

- **Derive everything from the audit log.** Rejected: it has no sizes,
  no IMAP commands and no client identity.
- **Metrics instead of span attributes.** Rejected for now: per-run
  analysis needs per-call data.

## References

- [ADR 0013], [ADR 0037], [ADR 0039]
- `.scaratec/handbuch/observability.md` §2
- `bdd/features/observability/tracing_spans.feature`

[ADR 0013]: 0013-hybrid-connection-pool.md
[ADR 0037]: 0037-trace-payload-modes.md
[ADR 0039]: 0039-usability-matrix-for-agent-behaviour.md
