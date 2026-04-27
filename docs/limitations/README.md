# Limitation Records

A Limitation Record documents an **accepted, bounded weakness** of the
system — a place where the project has consciously decided to live with
less than the guidelines or the ADRs would otherwise demand.

This directory is **not** a general-purpose debt log. It is a narrow,
high-friction mechanism with explicit governance.

## Default posture

> **Every problem is solved cleanly per the BDD Guidelines and the
> ADRs. A Limitation Record is the documented exception, never the
> norm.**

If an implementer finds that a clean solution is "hard", "expensive",
or "tedious", that is *not* cause for a Limitation Record. It is cause
to solve the problem. A Limitation Record applies only when there is a
demonstrable, substantive reason why the clean path cannot be taken —
for instance:

- A theoretical limit of the testing paradigm (e.g. API-contract tests
  inherently share vocabulary with the API under test).
- An external dependency that the project cannot control.
- A scope boundary explicitly ruled out by a superseded decision.

## Governance

Three rules, all mandatory:

1. **No self-approval.** An implementer — including any LLM agent —
   cannot unilaterally declare a limitation. A new record must be
   **reviewed and explicitly approved** by the project owner before
   it is merged. The approval is recorded in the "Approved by" field.

2. **No silent acceptance.** A limitation that is not in this
   directory does not exist. Code comments, commit messages, or
   off-band remarks do not substitute for a Limitation Record.
   Shortcuts that are not written down here are bugs, not
   limitations.

3. **Every field is filled.** The template below is the minimum
   content. "TBD", "N/A", or empty fields in a live record are a
   process violation and block merge.

A pull request that introduces a Limitation Record is held to a
higher bar than a pull request that solves the underlying problem.
That asymmetry is intentional — it makes clean solutions the cheaper
path.

## What a record must demonstrate

A convincing record answers, in order:

1. **What is the weakness, precisely?** Vague descriptions hide
   limitations inside other limitations.
2. **Why is the clean solution not chosen?** Cost/benefit,
   theoretical constraint, out-of-scope decision, external
   dependency. Hand-waving ("because BDD can't do it") is insufficient.
3. **What mitigations are already in place?** Structural cross-
   checks, redundancy, audits, complementary unit tests. Absence of
   mitigations is a red flag for the reviewer.
4. **What residual risk remains?** In concrete terms. "Minor
   inconvenience" is not a residual-risk statement; "a bug in the
   visibility-level mapping could escape BDD detection if it only
   affects the reason code and no other field" is.
5. **When must this be revisited?** A trigger condition that is
   observable from the outside. Without a trigger, a limitation is
   forever.

## Lifecycle

```
proposed -> Accepted -> (Mitigated -> Resolved)
                     -> Superseded by LIM-NNNN
```

- **Proposed** — drafted, not yet approved. Must not be merged to
  `main`.
- **Accepted** — approved by the project owner; known limitation in
  force. Accepted records carry a mandatory `Resolution intent`
  (see below).
- **Mitigated** — further mitigations have been added that materially
  reduce the residual risk. Record remains live.
- **Resolved** — the underlying problem has been solved. Record is
  kept for the history but no longer governs anything.
- **Superseded** — replaced by a later record that re-scopes the
  limitation. Both remain in the repository.

### Resolution intent

Every `Accepted` record declares itself as one of:

- **`must-resolve`** — a technical debt. Accepted *now* because the
  clean solution cannot yet be built, but a paydown is owed. A
  corresponding task is expected to exist in the project tracker,
  and the record references concrete triggers under which the debt
  becomes due.
- **`permanent`** — an architectural or theoretical boundary. No
  paydown is owed. Triggers for revisit exist for completeness (so
  that future external changes can force re-examination) but no
  active resolution work is expected.

A `must-resolve` record is **not** a softer version of `permanent`.
It is a commitment: the project owes the underlying problem a clean
solution, and the record is the IOU. Conversion from `must-resolve`
to `permanent` requires a new approval cycle and a superseding
record.

## Numbering & naming

- `NNNN-short-kebab-case-title.md`, numbered sequentially starting at
  `0001`. Numbers are never reused.
- `0000` is reserved for this meta-document (see template below).

## Format

Every record uses [`TEMPLATE.md`](TEMPLATE.md). No deviations.

## Index

| #    | Title | Status | Intent | Approved by |
|------|-------|--------|--------|-------------|
| 0001 | [Reason-code symmetry in BDD contract tests](0001-reason-code-symmetry-in-bdd.md) | Accepted | must-resolve | Randy Nel Gupta (2026-04-21) |
| 0002 | [Gmail scenarios not runnable against current fixture](0002-gmail-scenarios-not-runnable.md) | Accepted | must-resolve | Randy Nel Gupta (2026-04-21) |
| 0003 | [OAuth2 scenarios reference a nonexistent mock provider](0003-oauth2-scenarios-not-runnable.md) | Accepted | must-resolve | Randy Nel Gupta (2026-04-21) |

## Relationship to ADRs

ADRs record *decisions that shape the system*. Limitation Records
document *accepted weaknesses in the chosen direction*. They are not
interchangeable:

- An ADR can introduce a new capability.
- A Limitation Record cannot. It only admits a gap.

A Limitation Record typically references one or more ADRs (the
decisions whose clean implementation is being bounded) and, where
relevant, specific sections of the BDD Guidelines.
