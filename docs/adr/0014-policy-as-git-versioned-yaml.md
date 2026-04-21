# ADR 0014: Policy as Git-Versioned YAML with SIGHUP Reload

- **Status:** Accepted
- **Date:** 2026-04-20
- **Deciders:** Randy Nel Gupta

## Context

Policy in this project decides what a caller may see and what it may
do. A wrong policy either opens a data leak or blocks a legitimate
workflow. Either failure mode is serious, and both are more likely if
policy changes are informal.

Two storage shapes are commonly chosen:

- **Files under version control.** Changes go through code review,
  `git blame` attributes them, rollback is a revert, CI can validate.
- **A database table with an admin API.** Changes are dynamic, can be
  made through tooling, and do not require deployment. But there is no
  review channel, no diff history, no CI gate — each of which the
  database variant would have to replace.

A mixed approach (files as source, database as cache) adds operational
complexity without improving either of the above.

For a security-focused server the review channel is the non-negotiable
property. Policy changes through an unreviewed admin API invert the
relationship between operator intent and audit evidence.

## Decision

Policy and account configuration are **YAML files in a Git repository**
or Git-compatible working directory. The server reads them at startup
and on `SIGHUP`. There is no admin API for policy changes; all changes
go through the repository's review workflow.

Layout:

```
config/
  accounts.yaml                   # provider, auth, OAuth scope, token_cache,
                                  # secret_store refs. No secret values.
  callers.yaml                    # caller_id, auth type, policy file reference
  policies/
    invoice-agent.yaml            # folder-level policy for the invoice agent
    overview-agent.yaml
    archive-only.yaml
    ...
```

A `caller` declared in `callers.yaml` references one or more policy
files. Several callers may share a policy file (but do not share
identity).

**Reload semantics:**

- On `SIGHUP`, the entire configuration tree is re-read from disk in a
  temporary space, parsed, and validated. Only on full success is the
  in-memory state swapped atomically.
- On parse or validation failure, the existing state remains in
  effect. The failure is written to the audit log with enough detail
  to fix the file, but nothing else changes — the server continues to
  serve traffic on the previous config.
- A SIGHUP that arrives while a saga transaction is mid-flight waits
  until the WAL commit lock is free (a bounded, short wait). The
  currently-running transaction completes under its original policy;
  the next transaction starts under the new one.
- Pool invalidation: accounts whose auth or scope changed are treated
  as pool-drain events ([ADR 0013]); accounts no longer present are
  drained and their pools closed; accounts whose OAuth scope changed
  move to `needs_rebootstrap`.

**Validation before load** performs:

- Schema conformance via pydantic ([ADR 0012]).
- Consistency checks: every caller's policy reference exists; every
  policy's account references exist; folder mode matches its rules
  ([ADR 0003]); every rule uses only core predicates ([ADR 0004]); no
  capability appears on an account whose OAuth scope cannot support
  it ([ADR 0009]).
- Reachability checks: every rule in a whitelist folder must be
  reachable given the account's existence; a rule that can never
  match is a loader error, not a silent no-op.

**Explicitly absent:**

- No `update_policy` MCP tool.
- No HTTP admin endpoint for policy changes.
- No live-edit UI.

Operators who want tooling build it on top of the file workflow (a
policy-building CLI that emits YAML, for example), not on a server-side
mutation API.

## Consequences

### Positive

- **Every policy change is reviewable and attributable.** `git log`
  and `git blame` answer "what changed and why" definitively.
- **CI gate before merge.** The validator can run in CI, turning
  syntactically invalid or inconsistent policy into a blocked MR
  before it ever reaches production.
- **Rollback is a revert.** The commit graph is the rollback mechanism,
  with no special tooling.
- **Deployment is repository state plus a SIGHUP.** No separate admin
  artefact to keep aligned.
- **Validator is a library, not a service.** Same code runs in CI, in
  the server, and in an operator CLI.

### Negative

- **No dynamic policy updates from within the server process.** An
  operator cannot toggle a rule from an interactive session. This is
  a feature: toggling a rule must go through review.
- **SIGHUP is a blunt instrument.** It reloads everything; fine-grained
  partial reload is not supported and is unnecessary.
- **Requires operator discipline around working directory contents.**
  Running the server against an uncommitted working directory can
  cause history drift; the security manual prescribes that the server
  is always pointed at a clean, committed tree.

### Neutral

- Storage format is YAML. JSON would work; TOML would work. YAML has
  better support for multi-line rules, comments, and anchors for
  shared fragments. The choice is conventional, not architectural.

## Security Implications

- **Change control is a first-class feature, not an afterthought.**
  Security reviewers have a review surface that matches exactly the
  set of policy changes — no phantom dynamic API to also audit.
- **Failed reload never weakens enforcement.** A broken policy file
  does not disable policy; it preserves the last-known-good. This is
  the correct direction of failure.
- **Reload happens on a boundary.** Mid-transaction policy evaluation
  cannot be split across two policies. Either the saga finishes under
  the old one or starts under the new one — never a partial
  application.
- **No in-band escalation path.** Because there is no mutation API,
  there is also no way for a compromised caller, or for a prompt-
  injection that reaches a privileged agent, to widen its own
  policy. An attacker would need repository write access — a much
  higher bar — and even then would be visible in `git blame`.
- **CI gating substitutes for runtime checks.** Validations that run
  in CI (reachability, capability consistency, mode correctness) do
  not need to run at every PDP evaluation; the server assumes a
  validated config and short-circuits repeated checks.

## Alternatives Considered

- **Database-backed policy with admin API.** Rejected; absorbs the
  design cost of building a parallel review system to replace Git.
- **Hybrid (DB cache, YAML source).** Rejected; two sources of truth
  is one too many.
- **Per-caller policy via token claims** (e.g. OAuth scopes on
  inbound caller tokens). Rejected; those claims belong to OAuth
  providers, not to our internal policy, and mixing them invites
  confusion.
- **Hot reload on file-watch rather than SIGHUP.** Rejected for V1;
  introduces a race between "user is editing" and "server is
  reloading". SIGHUP is explicit operator intent and trivial to
  automate (`kill -HUP $(pidof imap-mcp)` from a git hook, say).

## References

- [ADR 0003] — folder mode validated on reload.
- [ADR 0004] — predicate grammar validated on reload.
- [ADR 0009] — per-account scope whose change triggers rebootstrap.
- [ADR 0012] — pydantic validation stack.
- [ADR 0013] — pool invalidation coupled to reload.
- [ADR 0021] — audit entries for reload and validation failure.

[ADR 0003]: 0003-whitelist-blacklist-folder-modes.md
[ADR 0004]: 0004-sender-rule-matcher-grammar.md
[ADR 0009]: 0009-oauth2-authorization-code-with-scope-minimization.md
[ADR 0012]: 0012-python-runtime-and-library-stack.md
[ADR 0013]: 0013-hybrid-connection-pool.md
[ADR 0021]: 0021-audit-log-format.md
