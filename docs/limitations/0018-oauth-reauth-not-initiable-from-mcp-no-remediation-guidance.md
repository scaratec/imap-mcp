# LIM 0018: OAuth re-authentication cannot be initiated from the MCP surface and the deny carries no remediation guidance

- **Status:** Proposed
- **Resolution intent:** must-resolve (technical debt)
- **Date proposed:** 2026-06-18
- **Date approved:** — (pending owner approval)
- **Proposed by:** Claude (implementation agent)
- **Approved by:** — (pending; project owner)
- **Related ADRs:** [ADR-0009](../adr/0009-oauth2-authorization-code-with-scope-minimization.md), [ADR-0016](../adr/0016-mcp-tool-set.md), [ADR-0027](../adr/0027-error-envelope-and-tool-surface-versioning.md)
- **Related Guidelines:** BDD Guidelines §4.3 (persistence validation), §7.2 (mocks simulate real behaviour)

## Resolution intent

`must-resolve`. The re-authentication flow is a deliberate
out-of-band, human-in-the-loop console script ([ADR 0009], RFC 8252).
That the *flow* runs out of band is acceptable. That an MCP client
hitting a `needs_rebootstrap` deny receives **no machine-readable
pointer to that flow** is the debt this record owes a fix.

## Context

When an account is in `needs_rebootstrap`, every IMAP tool denies with
`{"decision": "DENY", "reason": "needs_rebootstrap", "account": ...}`
(`server/src/imap_mcp/handlers/accounts.py:89-90`, and equivalently in
the other handlers). Recovery requires running the separate console
entry point `imap-mcp-oauth-bootstrap`
(`server/pyproject.toml` `[project.scripts]`;
`server/src/imap_mcp/auth/oauth_bootstrap.py:main`), which prints a
Google authorization URL, waits on stdin for the pasted redirect URL,
exchanges the code (RFC 8252 PKCE), and writes the new refresh token to
the secret store (`oauth_bootstrap.py:176-200`).

The MCP surface ([ADR 0016]) exposes no tool that starts, advances, or
even *describes* this flow. An agent that encounters the deny has no
in-band path forward and no in-band instructions.

## Nature of the weakness

Two precise defects, each independently observable:

1. **No initiation from the MCP surface.** There is no tool in the
   [ADR 0016] tool set to begin re-authentication, return an
   authorization URL, or accept a redirect URL / authorization code.
   The only entry point is an interactive OS-level console command run
   by a human on the server host. Consequence: an autonomous agent
   that hits `needs_rebootstrap` cannot recover the account by any
   action available through the protocol it speaks; the workflow halts
   until a human runs a shell command.

2. **No remediation guidance in the deny.** The deny envelope carries
   only `reason: "needs_rebootstrap"` (`accounts.py:89-90`). It does
   not name the `imap-mcp-oauth-bootstrap` command, the required
   environment variables (`IMAP_MCP_CONFIG_DIR`,
   `IMAP_MCP_OAUTH_CLIENT_ID`, `IMAP_MCP_OAUTH_CLIENT_SECRET`), the
   redirect-URI precondition, or a documentation link. Consequence: a
   caller is told *that* it is blocked but not *how* to get unblocked.
   The knowledge lives only in source and in operator memory — exactly
   the "silent" failure the limitations README warns against, except
   here it is the remediation, not the limitation, that is silent.

The owner's stated bar is: re-auth should be initiable from inside the
MCP server; **and if that is not possible, the server must at minimum
give exact account of how the process runs.** Today neither half is
met: not initiable, and not described in-band.

## Why the clean solution is not chosen

- **Full in-band initiation is genuinely constrained, not merely
  hard.** [ADR 0009] adopts RFC 8252 authorization-code-with-PKCE,
  which requires a human at a browser to authenticate and grant
  consent. The MCP transport (stdio / HTTP request-response) is not a
  user-facing browser channel; the server cannot drive a consent
  screen. A tool *could* return the authorization URL and accept the
  pasted redirect URL back (mirroring the console script over MCP), but
  that introduces a stateful, multi-call OAuth ceremony into the tool
  surface, with PKCE-verifier lifecycle held across calls and a new
  audit/security review of exposing auth URLs through MCP. That is a
  real design increment deferred by [ADR 0016]'s current scope, not a
  triviality.
- **The remediation-guidance half, by contrast, has no such excuse.**
  Enriching the `needs_rebootstrap` deny with a structured remediation
  block (command, env vars, doc link) is cheap and is the lower bound
  the owner explicitly named. It is deferred here only to keep this
  record's two halves together; it should be the first paydown step.

## Mitigations in place

- **A working recovery path exists and is documented in code.**
  `imap-mcp-oauth-bootstrap` performs the full RFC 8252 flow and
  writes the refresh token (`oauth_bootstrap.py:80-200`); the bootstrap
  result is audited (`oauth_bootstrap.py` audit block:
  `tool: "oauth_bootstrap"`).
- **The blocked state is unambiguous.** The deny `reason` is the
  stable token `needs_rebootstrap`, so an operator who *knows* the
  procedure can map it to the bootstrap command reliably.
- **Error-envelope versioning exists** ([ADR 0027]), so a remediation
  field can be added to the deny envelope without breaking the surface
  contract.

## Residual risk

An autonomous agent runs unattended (e.g. a scheduled mailbox triage).
A refresh token is revoked. Every subsequent tool call returns
`needs_rebootstrap` with no further detail. The agent has no tool to
recover and no instructions to relay, so it either stalls silently or
emits an unactionable "access denied" to its own user. A human must
notice independently, recall that the fix is an undocumented-in-band
console command, locate the three required env vars (the OAuth client
id/secret live only in deployment config), and ensure the redirect URI
matches the Google client — all knowledge that exists nowhere the
agent or a first-time operator can see. On 2026-06-18 the recovery
required reverse-engineering the entry point from `pyproject.toml` and
the bootstrap source because no tool, deny payload, or README section
named it.

## Triggers for revisit

- The remediation half is paid down: the `needs_rebootstrap` deny
  gains a structured remediation block (command + env vars + doc link).
  At that point this record moves to `Mitigated`.
- A decision is taken to expose an OAuth re-auth ceremony over MCP
  (initiation tool returning the auth URL + a tool accepting the
  redirect URL); that would be a new ADR and would resolve defect (1).
- An incident report describes an unattended agent halting on
  `needs_rebootstrap` with no actionable guidance.
- [ADR 0016]'s tool set is next revised for any reason — re-auth
  initiation should be reconsidered in that revision.

## References

- [ADR-0009](../adr/0009-oauth2-authorization-code-with-scope-minimization.md)
  — RFC 8252 flow that constrains in-band initiation.
- [ADR-0016](../adr/0016-mcp-tool-set.md) — the tool surface that lacks
  a re-auth tool.
- [ADR-0027](../adr/0027-error-envelope-and-tool-surface-versioning.md)
  — versioned error envelope that can carry remediation guidance.
- [LIM-0017](0017-expired-oauth-token-not-proactively-or-durably-detectable.md)
  — the companion gap: detecting the dead token in the first place.
- `server/src/imap_mcp/auth/oauth_bootstrap.py` — the out-of-band
  recovery flow.
- `server/src/imap_mcp/handlers/accounts.py:89-90` — the bare deny.
- `server/pyproject.toml` `[project.scripts]` — the entry point not
  referenced anywhere the caller can see.
- RFC 8252 (OAuth 2.0 for Native Apps), RFC 7636 (PKCE).
