# LIM 0007: HTTP transport deferred

- **Status:** Resolved
- **Resolution intent:** must-resolve (technical debt)
- **Date proposed:** 2026-04-21
- **Date approved:** 2026-04-21
- **Date resolved:** 2026-04-29
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

## Resolution

Resolved 2026-04-29 by closing all 12 residual `@pending_LIM_0007`
scenarios across `caller_authentication.feature` (4),
`secret_store_backends.feature` (7), and `reason_code_contract.feature`
(1). Concrete changes:

1. **Stdio Initialize-failure path** — `__main__.main()` no longer
   `SystemExit`s when `IMAP_MCP_CALLER_ID` is missing or unknown.
   Validation moves to a new `_stdio_deny_initialize` helper in
   `server.py` that reads the JSON-RPC `initialize` request, audits
   `tool=auth_failed` with `auth_failure_reason=no_caller_identity`
   or `unknown_caller_id`, writes a JSON-RPC error response keyed to
   the request id, and exits cleanly. The MCP client sees a
   structured error, not a broken pipe.

2. **HTTP identity-immutability** — `BearerAuthMiddleware` now
   detects "this bearer matches a different configured caller than
   the one being claimed" and emits `error: identity_immutable`
   (audit reason `identity_immutable`) instead of the generic
   `wrong_token` / `unknown_caller_id`. Constant-time scan over all
   configured callers preserves timing properties.

3. **HTTP `auth_failed` audit** — already-implemented path, just
   activated by removing the `@pending` tag.

4. **`env_var` SecretStore backend** — new class `EnvVarSecretStore`
   in `secrets.py`. Maps `secret://callers/X/token` →
   `IMAP_MCP_SECRET__CALLERS__X__TOKEN`. Read-only (`put`
   raises `NotImplementedError`).

5. **`gpg_file` SecretStore backend** — new class
   `GpgFileSecretStore` in `secrets.py`. Subprocess-based decryption
   via `gpg --decrypt --batch --yes --quiet --no-tty`. A custom
   `SecretDecryptionFailed` exception keeps gpg's stderr off the
   audit channel; the `BearerAuthMiddleware` maps it to
   `auth_failure_reason=secret_decryption_failed` (audit `reason`
   field surfaces it as a distinct top-level category to make
   operator triage easier).

6. **`imap-mcp-oauth-bootstrap` CLI stub** — new
   `server/src/imap_mcp/auth/oauth_bootstrap.py`. Validates the
   secret store backend; aborts with `env_var backend is read-only;
   bootstrap requires a writable secret store` when run against an
   `env_var`-configured deployment. Full interactive bootstrap stays
   gated by LIM-0003.

BDD-side additions: ~7 new given-/then-steps in `policy_steps.py`
(secret_store config block parser, env-var manipulation, GPG
keypair fixture with a real generated key whose fingerprint is
substituted for the feature-file's hardcoded label), plus the stdio
Initialize-handshake-without-arguments step in `mcp_steps.py`. The
HTTP harness now auto-promotes inline-Given stdio_trusted callers to
shared_token for HTTP scenarios that don't explicitly test the
ADR-0015 fatal-startup case.

Suite-Total: **174 passed / 0 failed / 18 skipped** (the remaining
18 skipped are LIM-0002 Mock-Gmail and LIM-0003 Mock-OAuth).

## References

- Scenarios: see list above (now all green).
- Server: `server/src/imap_mcp/secrets.py`, `server/src/imap_mcp/server.py`
  (`_stdio_deny_initialize`, `BearerAuthMiddleware`),
  `server/src/imap_mcp/auth/oauth_bootstrap.py`.
- ADR 0015, ADR 0018, ADR 0021.
- Plan: `/home/randy/.claude/plans/noble-prancing-glacier.md` (Phase D-Rest)
