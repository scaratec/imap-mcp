# LIM 0007: HTTP transport deferred

- **Status:** Accepted
- **Resolution intent:** must-resolve (technical debt)
- **Date proposed:** 2026-04-21
- **Date approved:** 2026-04-21
- **Proposed by:** claude (imap-mcp BDD phase D)
- **Approved by:** Randy N. Gupta
- **Related ADRs:** [ADR-0015](../adr/0015-caller-identity.md),
  [ADR-0018](../adr/0018-non-goals.md),
  [ADR-0021](../adr/0021-audit-format.md)
- **Related Guidelines:** BDD Guidelines §4.5

## Resolution intent

`must-resolve`. The MCP SDK supports an HTTP/SSE transport variant;
wiring the server and the BDD harness to use it unblocks the
`shared_token` caller authentication path and the non-goal `/admin`
probe. Several scenarios across feature files are tagged
`@pending @pending_LIM_0007` and will be reopened once the transport
lands.

## Context

The whole `caller_authentication.feature` file is tagged
`@pending_LIM_0007` and skipped. Three of its scenarios are stdio-only
(known-caller accepted, unknown-caller rejected, no-caller-id) but
require a graceful Initialize-failure path that the current stdio
path does not implement (the server `SystemExit`s and the MCP client
sees a broken pipe rather than a structured error message). The
remaining six scenarios require the server to speak MCP over HTTP:

- `caller_authentication.feature:50` — shared_token correct token.
- `caller_authentication.feature:64` × 3 — shared_token wrong/missing.
- `caller_authentication.feature:68` — identity immutability.
- `caller_authentication.feature:75` — stdio_trusted on HTTP fatal.
- `tool_surface/non_goal_rejection.feature:61` — `/admin` is 404.
- `audit/audit_log_format.feature:103` — auth_failed JSONL via HTTP
  bearer-token mismatch.
- `auth/secret_store_backends.feature` (entire file) — exercises
  pluggable backends via `shared_token` caller auth, which needs
  HTTP. Until HTTP lands, the additional backends (`env_var`,
  `gpg_file`, plus the future-list `gcp_secret_manager`,
  `hashicorp_vault`, `keyring`) cannot be observed end-to-end. The
  `file_dir` backend is exercised implicitly by every BDD scenario
  via stdio_trusted today; the others are deferred along with the
  HTTP transport.

The server currently supports only the stdio transport. Making the
HTTP path work requires:

- Import `mcp.server.sse` / `mcp.server.streaming_http` (or the newer
  equivalent).
- Map the HTTP Initialize message into the existing
  `ServerContext.caller_id` wiring, with `shared_token` validation
  against the `callers.yaml` `token_secret_ref`.
- Ensure that `/admin` and any other non-MCP routes return 404.
- Harness: provide an `MCPClient.start_http(port)` path that uses the
  SDK's HTTP client.

## Nature of the weakness

The nine scenarios named above are skipped and uncovered. A bug in
the HTTP transport — including a missing rejection of a stdio_trusted
caller connected over HTTP (high-risk: stdio_trusted means "the
orchestrator signals identity", which has no meaning over HTTP) —
would not be caught.

## Why the clean solution is not chosen

Scope-bounded. Fits a single focused implementation sprint but is
orthogonal to the saga/crash/audit phases already delivered.

## Mitigations in place

- The audit record format is the same across transports; the
  `audit_log_format` suite covers every format-level invariant on
  stdio.
- The caller-identity contract is a pure function tested on stdio
  (known caller accepted / unknown rejected / absent rejected — all
  three pass on stdio). HTTP adds the token-comparison layer, which
  is a constant-time `hmac.compare_digest` call and easy to unit test.
- ADR 0015 requires the stdio_trusted-over-HTTP combination to fail
  at startup, and the intended implementation is a bootstrap check
  that cannot be silently bypassed.

## Residual risk

A regression in the HTTP handshake or token-comparison path could
allow unauthenticated MCP callers to connect; the effect depends on
deployment. The residual risk is therefore moderate but scoped to
any HTTP-transport deployment, of which there are none today.

## Triggers for revisit

- The first HTTP-transport deployment is planned.
- An MCP SDK release materially changes the HTTP transport API.
- A regression in the audit `auth_failed` event is reported.

## References

- Scenarios: see list above.
- ADR 0015, ADR 0018, ADR 0021.
- Plan: `/home/randy/.claude/plans/noble-prancing-glacier.md` (Phase D)
