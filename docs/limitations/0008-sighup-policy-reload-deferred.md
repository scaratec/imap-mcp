# LIM 0008: SIGHUP policy reload deferred

- **Status:** Accepted
- **Resolution intent:** must-resolve (technical debt)
- **Date proposed:** 2026-04-21
- **Date approved:** 2026-04-21
- **Proposed by:** claude (imap-mcp BDD phase E)
- **Approved by:** Randy N. Gupta
- **Related ADRs:** [ADR-0014](../adr/0014-policy-reload.md)
- **Related Guidelines:** BDD Guidelines §4.5

## Resolution intent

`must-resolve`. ADR 0014 specifies a SIGHUP-triggered atomic policy
reload: re-parse configuration into a swap buffer, replace
in-memory state under a lock, drain removed accounts' connection
pools, move scope-changed accounts to `needs_rebootstrap`. Seven
scenarios under `features/policy/policy_reload.feature` exercise the
contract and are deferred until the implementation lands.

## Context

Seven scenarios in `policy_reload.feature`:

- :32 New rule becomes effective after SIGHUP
- :46 YAML parse error preserves the previous policy
- :76 Semantic validation error preserves the previous policy
- :95 Removing an account drains its pool and denies subsequent calls
- :106 Adding a folder policy makes a hidden folder visible
- :119 Changing an account's OAuth scope → needs_rebootstrap
- :128 SIGHUP during in-flight saga applies only from the next tx

All require the server to accept SIGHUP, re-parse configuration, and
atomically replace in-memory state. The current server reads
configuration once at startup and holds a frozen reference in
`ServerContext.configuration`.

## Nature of the weakness

The seven scenarios are skipped and uncovered. Policy changes made
after startup are not seen by the running server; operators must
restart the service to apply changes.

## Why the clean solution is not chosen

Scope-bounded. Requires:

- `asyncio.add_signal_handler(SIGHUP, …)` wired at the server's
  asyncio loop.
- An `atomic_load()` helper on the Configuration that re-parses the
  YAML tree, runs all invariants, and returns a new immutable
  Configuration instance.
- All handlers must read from a live configuration reference
  (`context.configuration` → replaced atomically) rather than a
  captured value.
- Pool drain: the hybrid connection pool (ADR 0013) needs a close
  method that respects in-flight operations.
- The `accounts` table needs a per-row `needs_rebootstrap` boolean,
  written when OAuth scope changes and reported by `list_accounts`.

These are several orthogonal sub-changes and are scheduled as a
single Phase E task.

## Mitigations in place

- Configuration invariants are checked at startup; a YAML typo
  refuses to boot rather than silently reverting (fail-closed).
- Policy edits in deployment are rare in practice today
  (most tenants ship one invoice-agent config and keep it).
- Operators can restart the service to apply changes — downtime of
  seconds, acceptable outside business hours.

## Residual risk

A missed policy change (operator edits `callers.yaml` but does not
restart) leaves the server serving the previous policy. No security
regression per se — the previous policy was already approved — but
an apparent change is invisible until restart.

## Triggers for revisit

- The first deployment that edits policy at runtime ships.
- An incident report attributes a stale-policy decision to the
  missing reload path.
- ADR 0014 is superseded.

## References

- Scenarios: `bdd/features/policy/policy_reload.feature` (all 7)
- ADR 0014.
- Plan: `/home/randy/.claude/plans/noble-prancing-glacier.md` (Phase E)
