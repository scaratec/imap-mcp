# ADR 0015: Caller Identity and Authentication

- **Status:** Accepted
- **Date:** 2026-04-20
- **Deciders:** Randy Nel Gupta

## Context

Policy is bound to caller identity ([ADR 0001]). Identity therefore
must be:

- **Stable.** A rule written for `invoice-agent` must continue to
  apply to the same logical caller across reconnects.
- **Verifiable.** The server must decide whose rules to apply without
  trusting the caller's self-assertion in the general case.
- **Transport-independent.** stdio, HTTP/SSE, and future mTLS
  transports must all resolve to the same identity concept.

MCP clients most commonly launch the server as a local stdio
subprocess from within an orchestrator (Claude Code, Claude Desktop).
In that deployment the orchestrator is the source of truth for *which*
caller is running; cryptographic proof is redundant with the operating-
system trust boundary.

At the other end, a remote HTTP/SSE transport with multiple callers
demands real authentication, because the orchestrator boundary no
longer exists.

A design that forces every deployment into the same authentication
discipline sacrifices the local case's ergonomics or the remote case's
security.

## Decision

**Caller identity is a first-class configuration concept**, declared
in `callers.yaml`. Every caller carries an `auth.type` field selecting
one of two V1 authentication mechanisms:

```yaml
callers:
  - id: invoice-agent
    policy: policies/invoice-agent.yaml
    auth:
      type: shared_token
      token_ref: secret://callers/invoice-agent/token

  - id: overview-agent
    policy: policies/overview-agent.yaml
    auth:
      type: stdio_trusted
```

**`stdio_trusted`:**
The caller identifies itself via either `--caller-id <id>` on the
server subprocess command line or `IMAP_MCP_CALLER_ID=<id>` in the
subprocess environment. No cryptographic check; the orchestrator is
trusted to set it correctly. Permitted only when the server was
launched with a stdio transport.

**`shared_token`:**
The caller presents a bearer token in the MCP `Initialize` metadata
(or an equivalent header for HTTP/SSE). The server looks up the
caller's token in the secret store ([ADR 0011]) and verifies it using
**constant-time comparison** (`hmac.compare_digest`). Required for any
non-stdio transport; also permitted on stdio for additional discipline.

**Mid-connection identity is immutable.** Once the MCP session is
initialized under a caller identity, subsequent tool calls cannot
declare a different one. There is no impersonate/delegate mechanism.

**Every call is audited** ([ADR 0021]) with the resolved
`caller_id`. Authentication failures (`auth.type` unavailable for
transport, token mismatch, missing env var) produce an explicit DENY
record with `reason: auth_failed`.

**Unauthenticated access is not supported.** A session that cannot
resolve a caller is terminated before any tool dispatch.

## Consequences

### Positive

- **Uniform policy binding.** Every code path downstream of the
  initialize handshake knows there is a `caller_id`; policy evaluation
  and audit logging share that one identifier.
- **Deployment-appropriate rigour.** Local stdio does not pay a token-
  management tax; remote deployments cannot skip one.
- **Trivial migration.** Moving a caller from `stdio_trusted` to
  `shared_token` is a `callers.yaml` edit plus a token mint.
- **No anonymous access.** The category of bug where "forgot to set
  identity" silently ran under a default role does not exist here.

### Negative

- **`stdio_trusted` relies on the orchestrator.** A compromised
  orchestrator can claim any caller identity. This is the correct
  trust boundary for local deployment but must be stated explicitly.
- **Two authentication mechanisms to test.** Acceptable; both are
  small.
- **Per-caller token management.** When `shared_token` is used,
  operators maintain one token per caller, in the secret store.
  Rotation is a standard secret-store operation.

### Neutral

- mTLS is not in V1. The `auth.type` field is extensible; a future
  ADR may introduce `mtls` with the client-certificate common name as
  the caller-id, without breaking existing configurations.

## Security Implications

- **No self-assertion without proof** on remote transports. A
  network attacker who can reach the HTTP/SSE port cannot obtain any
  authorization merely by sending an `Initialize`.
- **Constant-time comparison.** Token equality checks use
  `hmac.compare_digest`; naïve `==` comparison is a CI review gate.
- **Transport-appropriate defaults.** The server refuses to start in
  a configuration where HTTP/SSE is enabled and any caller uses
  `stdio_trusted`. Misconfigurations of this shape are rejected at
  startup, not at the first request.
- **Audit trail for auth events.** Every `auth_failed` decision is
  logged; brute-force attempts against `shared_token` are visible in
  the audit stream and, at the operator's discretion, trigger rate
  limiting (not V1).
- **Token storage reuses the secret store.** No new cryptographic
  code paths are introduced by this ADR; verification and persistence
  ride the existing abstraction ([ADR 0011]).
- **No token in logs.** Audit and structured logs record the
  `caller_id` and the authentication outcome, never the token bytes
  or a partial token.
- **No impersonation.** The absence of a "switch identity" mechanism
  means a prompt-injected caller has no in-band method of pretending
  to be another caller.

## Alternatives Considered

- **mTLS-only.** Too much ceremony for local stdio deployments where
  the orchestrator is the trust anchor.
- **No authentication at all; policy is just advisory.** Rejected;
  inverts the entire project premise.
- **OAuth for caller authentication.** Rejected; reuses the OAuth
  abstraction for a purpose (authenticating clients to the server) it
  was not designed for, and adds an external provider to the trust
  path with no compensating benefit.
- **API keys in request headers, no per-caller mapping.** Rejected;
  strips identity from audit and policy binding.
- **Implicit caller inferred from stdio process ancestry.** Rejected
  as fragile: Unix process trees do not carry enough semantics to
  distinguish two instances of the same orchestrator.

## References

- [ADR 0001] — policy is bound to caller identity.
- [ADR 0011] — secret store holds `shared_token` material.
- [ADR 0021] — audit log records caller id and auth outcome.
- RFC 6750 — Bearer token usage (reused transport shape).

[ADR 0001]: 0001-default-deny-hierarchical-policy.md
[ADR 0011]: 0011-pluggable-secret-store-backend.md
[ADR 0021]: 0021-audit-log-format.md
