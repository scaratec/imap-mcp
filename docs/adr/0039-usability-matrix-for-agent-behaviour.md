# ADR 0039: A Usability Matrix for Agent Behaviour, Apart from BDD

- **Status:** Accepted
- **Date:** 2026-11-03
- **Deciders:** Randy Gupta

## Context

The BDD suite proves what the server does. It cannot show how agents
use it: whether they find the right tool, read errors correctly, finish
a search, or clean up a draft. Those outcomes depend on the agent
harness and the model, change from day to day, and differ between runs
of the same task. Recurring complaints (leftover drafts, incomplete
search results, plain-text mails nobody chose) are exactly such
outcomes.

A test whose result depends on a model's behaviour is nondeterministic
by construction. Making it a hard failure would make the suite flaky
and teach everyone to ignore it.

## Decision

**Agent behaviour is measured in a separate usability matrix under
`usability/`, which grades runs instead of failing them and never gates
CI.**

- Axes: harness (Antigravity CLI, Copilot CLI, Claude Code, OpenCode),
  model slot (one reference model available in all harnesses, the
  harness's native model, a small model), server variant, repetitions.
- A run ends in one grade: `fulfilled`, `partial`, `missed`,
  `misreported`, `harmful` or `not-assessable`. No grade aborts the
  matrix. The runner exits non-zero only when it cannot run at all.
- Evidence: audit log, traces ([ADR 0038]), fixture mailbox state,
  harness transcript, and a structured feedback form the agent fills
  in after each task; self-reports are cross-checked, never trusted.
- Fixtures are synthetic and generated from the feature files, so every
  run starts from the same data; no real mailbox is ever used.
- The server behaviour a usability scenario relies on must be specified
  and pass in `bdd/` first.

## Consequences

### Positive

- Recurring agent problems become measurable and comparable across
  harnesses, models and server versions.
- Server changes meant to help agents can be checked for effect.

### Negative

- Runs cost model quotas and time; the matrix is run on demand.
- Small samples show tendencies, not statistics.

### Neutral

- The BDD suite is unchanged in role and strictness.

## Security Implications

- Harnesses run with automatic tool approval; each run executes in an
  isolated environment against fixtures only.
- Traces use `full` payloads ([ADR 0037]); fixtures contain no personal
  data.

## Alternatives Considered

- **Agent tests inside BDD with retries.** Rejected: hides flakiness
  instead of measuring behaviour.
- **Manual spot checks only.** Rejected: not comparable over time.

## References

- [ADR 0037], [ADR 0038]
- `usability/README.md`

[ADR 0037]: 0037-trace-payload-modes.md
[ADR 0038]: 0038-traces-for-usage-efficiency.md
