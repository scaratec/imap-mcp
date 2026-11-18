# LIM NNNN: <Short Noun Phrase>

- **Status:** Proposed | Accepted | Mitigated | Resolved | Withdrawn | Superseded by [LIM-XXXX](XXXX-title.md)
- **Resolution intent:** must-resolve (technical debt) | permanent (architectural boundary)
- **Date proposed:** YYYY-MM-DD
- **Date approved:** YYYY-MM-DD (required before Accepted; n/a if Withdrawn)
- **Date resolved:** YYYY-MM-DD (required for Resolved)
- **Date withdrawn:** YYYY-MM-DD (required for Withdrawn)
- **Proposed by:** <name or agent id>
- **Approved by:** <project-owner name, required before Accepted; n/a if Withdrawn>
- **Resolution commit:** <git SHA, required for Resolved>
- **Withdrawal commit:** <git SHA, required for Withdrawn>
- **Scope:** <component this limitation applies to>
- **Related ADRs:** [ADR-XXXX](../adr/XXXX-title.md), …
- **Related Guidelines:** BDD Guidelines §N.N, §N.N

## Resolution intent

Mandatory classification for `Accepted` records. Every limitation is
one of two kinds:

- **`must-resolve`** — a technical debt that is accepted *now* but
  must be paid back. The record must list an explicit resolution plan
  and concrete paydown triggers in the "Triggers for revisit"
  section. A `must-resolve` record is tracked as an open debt item in
  the project's task list until its status becomes `Resolved`.
- **`permanent`** — an architectural or theoretical boundary that the
  project has decided to live with indefinitely. No resolution is
  expected. The record still lists "Triggers for revisit" so that
  future external advances (new tooling, new standards, changed
  threat model) can force a re-examination, but no paydown is owed.

## Context

Where in the system does this limitation arise? Which ADR or guideline
would dictate a different, cleaner outcome, and why can it not be
reached?

## Nature of the weakness

State the limitation precisely: which property is missing, under
which conditions, and with which observable consequence.

## Why the clean solution is not chosen

Justify the exception (theoretical limit, disproportionate cost vs.
residual risk with numbers, external dependency outside project
control, or explicit scope boundary).

## Mitigations in place

List every concrete, verifiable mitigation that reduces the residual
risk.

## Residual risk

Describe, in concrete terms, the worst case that can still happen.

## Triggers for revisit

Under which externally observable conditions must this record be
re-examined? Every limitation must have at least one.

## References

- ADR links, Guideline sections, RFCs, or issue references.
