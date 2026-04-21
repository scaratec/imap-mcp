# ADR 0020: imap-agent as a Future Client, not a Component

- **Status:** Accepted
- **Date:** 2026-04-20
- **Deciders:** Randy Nel Gupta

## Context

An existing project, `imap-agent`, predates this one. It automates
invoice processing: it pulls mail via IMAP, classifies candidates
with Google Gemini, extracts invoice data with a LangGraph workflow,
and exports normalized artefacts. It includes an IMAP listener with
IDLE, a rule-based filter engine with REST CRUD, a PostgreSQL-backed
workflow checkpoint store, and a minimal MCP surface with a single
tool (`get_unread_emails`).

The scope of `imap-mcp` overlaps significantly with the IMAP and
filter portions of `imap-agent`. Four reasonable futures exist:

1. **Merge:** rewrite `imap-agent` on top of this server.
2. **Absorb:** move domain logic from `imap-agent` into this server.
3. **Parallel:** both continue independently against the same
   mailboxes.
4. **Client/server split:** `imap-mcp` is generic; `imap-agent`
   becomes one of its clients and keeps its domain logic.

Options 2 and 3 both expand the scope of this server in ways that
damage the project's core brief (generic, policy-driven mail
access). Option 1 discards significant investment in `imap-agent`'s
LangGraph workflow and invoice extraction work.

A decision is required now, because several other ADRs
([ADR 0012], [ADR 0016], [ADR 0018]) implicitly depend on this
project being a generic server rather than a domain-specific one.

## Decision

`imap-mcp` is a **generic, domain-agnostic server**. `imap-agent`
is treated as a **future client**, not a component. Concretely:

- **No invoice, PDF, OCR, or LLM-extraction logic ships in
  `imap-mcp`.** Not as a plugin, not as an optional module, not as
  a "helper" tool. Those responsibilities remain with `imap-agent`
  (or any other client).
- **No workflow-engine or state-machine** for business processing
  is hosted here. The WAL in [ADR 0007] serves saga transactions
  only, not agent workflows.
- **No domain-specific database.** The server persists exactly:
  configuration (read-only at runtime), secrets (via the secret
  store abstraction), and the saga WAL.
- **Migration path** for `imap-agent` is acknowledged but not
  scheduled as part of V1 of `imap-mcp`:
  1. `imap-mcp` reaches production readiness independently.
  2. `imap-agent` gains a `backend: mcp | native` configuration
     switch and is tested in parallel against the same mailboxes
     (with appropriate policy isolation).
  3. Once the `mcp` backend proves equivalent, the native IMAP
     code and the filter engine inside `imap-agent` are removed;
     `imap-agent` becomes an `imap-mcp` client with its own
     domain logic intact.
  4. IDLE-style listening on new mail moves either into a
     dedicated trigger service (as discussed for push events,
     out-of-band of MCP) or into the retained `imap-agent`
     process, depending on operator preference.

The migration itself is a matter for `imap-agent`'s repository and
release planning, not for this project's V1 scope. `imap-mcp`
proceeds assuming it will have *at least one* production client
(`imap-agent`) eventually, but designs no client-specific
affordances.

## Consequences

### Positive

- **Scope discipline.** Every feature request with a whiff of
  "invoice" or "extraction" is redirected to `imap-agent`.
- **No circular dependency.** `imap-mcp` does not depend on
  `imap-agent` for anything, not even for reference implementations.
- **`imap-agent` is not destabilized** by the existence of
  `imap-mcp`. Its production invoice pipeline continues on the
  native backend until an explicit migration event.
- **Open-source viability.** A domain-agnostic MCP server is far
  more publishable than one bundled with a specific invoicing
  workflow.

### Negative

- **Two IMAP-connection pools** exist during the parallel phase
  (one in each project). Tokens and auth events are duplicated
  briefly. Explicitly accepted as a transitional cost.
- **Duplicated work** on the filter engine vs. the policy engine
  until migration. `imap-agent`'s filter engine and this server's
  policy engine serve different purposes (imap-agent filters
  messages before workflow dispatch; the server filters access
  by caller), but they can appear similar and confuse observers.
- **Operator confusion during the transition.** Clear
  documentation of which system owns which concern will be
  required.

### Neutral

- The Python stack choice ([ADR 0012]) is aligned with
  `imap-agent` and eases eventual migration — a shared library of
  types, for example — but this is a convenience, not a design
  constraint of this ADR.

## Security Implications

- **Trust boundary clarity.** A single server-side policy point
  ([ADR 0001]) is the goal; during the transition, `imap-agent`
  still talks to IMAP natively under its own configuration. The
  policy guarantees of `imap-mcp` do not apply to `imap-agent`'s
  direct access until migration completes.
- **No cross-contamination of credentials.** `imap-mcp` and
  `imap-agent` hold separate OAuth bootstraps during the parallel
  phase. A credential compromise in one does not automatically
  propagate to the other.
- **Audit surfaces are separate.** `imap-mcp` emits the audit
  format of [ADR 0021]. `imap-agent` continues to emit whatever
  it emits today until migrated. A consolidated audit view across
  the two is out of scope for V1; operators who want one run both
  logs through the same aggregator.

## Alternatives Considered

- **Absorb `imap-agent` into `imap-mcp`.** Rejected for scope
  reasons; adding invoice-specific logic here would permanently
  damage the "generic, policy-driven" property and block open-
  source publication.
- **Parallel operation indefinitely.** Rejected as the long-term
  answer; two pools of IMAP connections per user is bad operational
  hygiene and would continue to duplicate auth work. Transitional
  acceptance (above) is different from a permanent state.
- **Cut `imap-agent` and rebuild on top of `imap-mcp` from
  scratch.** Rejected; throws away working LangGraph workflows,
  invoice-extraction prompts, and operational experience for a
  questionable gain.
- **Version `imap-mcp` as "imap-agent v2".** Rejected; they do not
  solve the same problem. `imap-agent` is a vertical workflow;
  `imap-mcp` is a horizontal mediator.

## References

- [ADR 0001] — caller-bound policy that `imap-agent` will eventually
  live under.
- [ADR 0007] — WAL scope excludes agent workflow state.
- [ADR 0012] — Python stack, convenient for future code sharing.
- [ADR 0016] — tool surface, deliberately generic.
- [ADR 0018] — non-goals, which include domain-specific tools.
- [ADR 0021] — audit format, not retrofitted onto `imap-agent`.
- `imap-agent` repository:
  <ssh://git@gitlab.scaratec.com:2222/randy/imap-agent.git>

[ADR 0001]: 0001-default-deny-hierarchical-policy.md
[ADR 0007]: 0007-sqlite-as-wal-store.md
[ADR 0012]: 0012-python-runtime-and-library-stack.md
[ADR 0016]: 0016-mcp-tool-set.md
[ADR 0018]: 0018-non-goal-tool-surface.md
[ADR 0021]: 0021-audit-log-format.md
