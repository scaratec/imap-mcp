# ADR 0053: Non-Interfering OpenTelemetry Tracing

- **Status:** Accepted
- **Date:** 2026-11-17
- **Deciders:** Randy Nel Gupta

## Context

[ADR 0037](0037-trace-payload-modes.md) and [ADR 0038](0038-traces-for-usage-efficiency.md) introduced OpenTelemetry payload modes (`off`, `redacted`, `full`) and usage-efficiency span attributes, but three issues remained:

1. `mcp.result.matched` recorded pre-policy match counts on spans even in `off` and `redacted` modes, leaking `NONE` message existence into shared trace stores.
2. `mcp.client.name` and `mcp.client.version` from `Initialize` were unbounded and unsanitized, and `redacted` mode did not scrub `saved_to`/`saved_path` attachment filenames across all 27 tools.
3. [ADR 0037](0037-trace-payload-modes.md) referenced an internal company handbook path and lacked an `Auditability` security analysis.

## Decision

We emit **OpenTelemetry `tool.<name>` and `imap.<cmd>` spans with three operator-chosen payload modes (`off`, `redacted` default, `full` opt-in), post-policy visible counts only (`mcp.result.visible`, `mcp.result.returned`, `mcp.result.has_more`), 128-character printable-ASCII `clientInfo` bounds, exporter-failure resilience, and deterministic flush on stdin EOF and `SIGTERM`**.

### 1. Payload modes (`IMAP_MCP_TRACE_PAYLOADS`)

- `redacted` (default when unset): `mcp.request` and `mcp.response` scrub all message bodies, HTML, raw RFC822, addresses, subjects, headers, attachment filenames/paths (`saved_to`, `saved_path`), and nested draft/attachment payloads across all 27 tools.
- `full` (explicit opt-in for owner-only trace stores): records complete JSON truncated at `IMAP_MCP_TRACE_PAYLOAD_MAX_CHARS` (default `16384`) with marker `"…[truncated <n> chars]"`.
- `off`: omits `mcp.request` and `mcp.response` completely.
- Secrets from `SecretStore` never appear in any mode; every span records `imap_mcp.trace.payloads`; unknown mode values refuse server startup.

### 2. Non-interfering span attributes and lifecycle

- Resource attributes: `service.name="imap-mcp"`, `service.version`, `service.instance.id`, plus `OTEL_RESOURCE_ATTRIBUTES`.
- Client identity: `mcp.client.name` and `mcp.client.version` stripped of non-printable ASCII characters and truncated to at most 128 characters.
- Tool span attributes: `mcp.tool`, `mcp.caller_id`, `mcp.account`, canonical `mcp.folder`, `mcp.decision`, `mcp.reason`, `mcp.latency_ms`, `mcp.response.size_bytes`, and on listing/search tools `mcp.result.visible`, `mcp.result.returned`, and `mcp.result.has_more`. Pre-policy `mcp.result.matched` is **never** emitted; adding or removing `NONE` messages leaves span attributes in `off` and `redacted` modes unchanged.
- Outcomes: `DENY` sets `domain.outcome="rejected"`, `domain.rejection.reason`, and span status `UNSET`; `ALLOW + ERROR` sets `error.type` and span status `ERROR`; `ALLOW + OK` sets `domain.outcome="accepted"`.
- Child spans (`imap.connect`, `imap.authenticate`, `imap.select`, `imap.uid_search`, `imap.uid_fetch`, `imap.uid_store`, `imap.append`, `imap.uid_expunge`, `imap.move`, `imap.logout`) record only command name and tagged status. Exporter failures never fail tool calls, and queued spans flush on stdin EOF and `SIGTERM`.

## Consequences

### Positive

- **Strict trace non-interference in `off` and `redacted` modes.** Shared observability backends never learn hidden match counts or attachment filenames.
- **Standalone public specification.** Replaces internal handbook links with self-contained public rules.

### Negative

- Dashboards reading `mcp.result.matched` must switch to `mcp.result.visible`.

### Neutral

- `full` mode remains available for isolated local usability debugging ([ADR 0060](0060-usability-matrix-evaluation-boundaries.md)).

## Security Implications

- **Attack surface.** Truncates untrusted `clientInfo` strings to 128 printable ASCII chars and isolates OTLP exporter errors from MCP execution.
- **Trust boundaries.** Aligns `redacted` span scrubbing with [ADR 0043](0043-non-interference-transparency-and-search.md) and [ADR 0047](0047-per-caller-symlink-safe-attachment-delivery.md).
- **Data exposure.** Zero personal data, attachment filenames, or hidden-object counts appear in `off` or `redacted` traces; secrets never appear in any mode.
- **Failure modes.** Unknown `IMAP_MCP_TRACE_PAYLOADS` values refuse startup; unreachable OTLP collectors drop spans without failing requests.
- **Auditability.** OpenTelemetry traces complement — and never replace — the tamper-evident local JSONL audit chain ([ADR 0048](0048-pre-effect-audit-and-test-boundary.md)); `imap_mcp.trace.payloads="full"` explicitly marks spans carrying opt-in payloads.

## Alternatives Considered

- **Keep `mcp.result.matched` in `redacted` traces.** Rejected: leaks existence of `NONE` messages into shared telemetry stores.
- **Emit bucketed hidden counts on spans.** Rejected: `0` vs `>0` still leaks a 1-bit predicate oracle in traces.
- **Remove `full` payload mode completely.** Rejected: local synthetic usability debugging ([ADR 0060](0060-usability-matrix-evaluation-boundaries.md)) benefits from inspecting exact JSON payloads when no personal data exists.

## References

- Supersedes [ADR 0037](0037-trace-payload-modes.md) and [ADR 0038](0038-traces-for-usage-efficiency.md).
- [ADR 0043](0043-non-interference-transparency-and-search.md) — non-interference invariants.
- [ADR 0060](0060-usability-matrix-evaluation-boundaries.md) — usability matrix harness boundaries.
