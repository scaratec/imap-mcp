# ADR 0018: Non-Goal Tool Surface

- **Status:** Accepted
- **Date:** 2026-04-20
- **Deciders:** Randy Nel Gupta

## Context

[ADR 0016] defines the sixteen tools offered in V1. Equally important
to security is the explicit list of tools *not* offered, and the
reasons.

Without such a list, feature requests accumulate as open questions:
"Can we add a tool for X?" becomes a recurring debate. A positive
list of non-goals lets reviewers classify new ideas immediately: is
this an addition to the tool set (new ADR required, positive case),
or is it explicitly on the non-goal list (closed topic unless the
ADR is superseded)?

## Decision

The following tools are **explicitly not part of the MCP surface**.
Each is grouped by the reason. Every entry in this list is closed
for V1; reopening requires a dedicated ADR that supersedes or amends
this one.

### Group 1 — Destructive (irreversible operations)

- **`delete(uid)` / `hard_delete`** — explicit deletion. Superseded by
  `move` to a trash folder whose policy models retention. A direct
  delete tool would require introducing a separate "delete"
  capability with no cross-check (unlike move, which requires two
  policies agreeing).
- **`expunge`** — unconditional `EXPUNGE`. Never exposed; the server
  issues `EXPUNGE` only as the terminal step of a saga-controlled
  move.
- **`store_deleted_flag`** — setting `\Deleted` without a move.
  Produces split-brain states that are a known source of recovery
  ambiguity; we avoid them entirely.

### Group 2 — Structural (outside policy scope)

- **`create_folder` / `rename_folder` / `delete_folder`.** Folder
  management is an operator concern; any new folder must receive a
  policy entry before it is visible, and that cannot be accomplished
  from a caller tool without collapsing the separation between
  policy authorship and policy enforcement.
- **`create_account` / `delete_account`.** Likewise, plus these
  operations would require OAuth bootstrap, credential handling,
  and secret-store interaction — all squarely operator territory.

### Group 3 — Policy bypass (would defeat the server's purpose)

- **`raw_imap_command(cmd)`.** A passthrough tool would let a caller
  issue arbitrary IMAP commands, rendering the policy layer
  advisory. Absolutely excluded.
- **`fetch_raw_rfc822`.** Delivering the raw message bytes would
  bypass the visibility-level-based field filtering. The only
  lawful way to retrieve message content is via the structured
  fetch tools.
- **`impersonate(other_caller)` / `execute_on_behalf`.** Privilege
  elevation has no legitimate use case here and would render
  caller-bound policy meaningless. Caller identity is immutable
  for the duration of a connection ([ADR 0015]).
- **`bypass_redaction(uid)`** or any equivalent "just this once"
  escape. The principled way to lift a redaction is a policy
  change under review ([ADR 0014]), never a per-call override.

### Group 4 — Scope creep (not in the project's brief)

- **`subscribe_to_new_mail`** and other MCP resource-subscription
  tools. Push is addressed out-of-band per the project's design;
  MCP remains a tool bus, not an event bus.
- **`search_across_accounts(criteria)`.** Would collapse per-account
  policy evaluation into a single result set, muddying audit
  attribution. Callers issue one call per account.
- **`fetch_next_unseen` / `iterate_unseen`.** Conveniences that do
  not offer anything beyond `search` + `fetch_envelope` and introduce
  stateful cursors with their own lifecycle.
- **`batch_fetch`.** Batching is a transport optimization, not a
  caller concern. If latency becomes a problem, the MCP transport
  or HTTP/2 pipelining addresses it; the tool surface stays simple.

### Group 5 — Administrative (wrong audience)

- **`reload_policy`, `rotate_tokens`, `drain_pools`.** Operator
  actions, accessible through a separate operator CLI, never over
  MCP. A caller should not be able to trigger server-global state
  changes; an attacker who obtains caller credentials should not
  gain administrative reach.
- **`get_audit_log`, `get_server_logs`, `get_server_metrics`.** Log
  and metric consumption is operator-side. A caller's self-view is
  constrained to `describe_policy` ([ADR 0017]).
- **`get_server_config`.** Would reveal other callers' policies and
  cross-tenant information. Structurally excluded from the tool
  surface.

## Consequences

### Positive

- **Clean triage of feature requests.** A request to add any of the
  above is immediately classified and redirected to the ADR process
  if truly warranted.
- **Threat-model clarity.** The classes of operation an attacker
  cannot request via MCP are enumerated. Review scope shrinks.
- **Protection against prompt-injection.** A prompt-injected agent
  cannot execute destructive, structural, bypass, scope-creep, or
  administrative operations because the tools do not exist, not
  because the policy refused.

### Negative

- **Some operator tasks require a separate tool.** A planned
  operator CLI (out of V1 scope for this project) handles
  reload/rotate/audit; a minor ergonomics cost we accept.
- **"Why can't the agent just X?" comes up.** Mitigated by having
  this ADR to point at.

### Neutral

- A tool listed here is not banned forever — it is banned until a
  superseding ADR argues for it. That is the right bar.

## Security Implications

- **Attack surface minimization by omission.** The cheapest
  defensive measure is not offering a capability. Every tool in
  this list is one we do not have to harden.
- **Consistent authorization story.** No tool in the V1 surface
  sidesteps PDP or redaction; the list above enumerates the shapes
  we considered and rejected.
- **No privilege elevation primitives.** Impersonation, raw
  commands, and bypass are explicitly absent. A caller cannot
  escalate; the remaining attacks are misuse of legitimate tools
  within policy, which is exactly what the policy layer exists to
  constrain.
- **Administrative isolation.** The operator plane and the caller
  plane are cleanly separated. A compromised caller cannot drain
  pools or rotate tokens; a compromised operator environment is a
  different threat with a different response.

## Alternatives Considered

- **Leave non-goals implicit.** Rejected; every recurring feature
  request would rehash the same discussion without a clear
  reference.
- **Offer some of these tools under a "privileged" caller class.**
  Rejected: the caller/operator split is a hard boundary, and
  creating a third class dilutes it.
- **Offer raw-IMAP behind an "expert mode".** Rejected categorically:
  any raw-IMAP primitive is a policy bypass by construction.

## References

- [ADR 0014] — policy changes go through review, not through tools.
- [ADR 0015] — caller identity is immutable.
- [ADR 0016] — the positive tool surface.
- [ADR 0017] — how legitimate tools communicate refusal.

[ADR 0014]: 0014-policy-as-git-versioned-yaml.md
[ADR 0015]: 0015-caller-identity-and-authentication.md
[ADR 0016]: 0016-mcp-tool-set.md
[ADR 0017]: 0017-response-transparency-for-filtered-data.md
