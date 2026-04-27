# LIM 0001: Reason-code symmetry in BDD contract tests

- **Status:** Mitigated
- **Resolution intent:** must-resolve (technical debt)
- **Date proposed:** 2026-04-21
- **Date approved:** 2026-04-21
- **Date mitigated:** 2026-04-27 — Mitigations 1 (canonical table in
  ADR-0017 §2.1), 3 (`reason_code_contract.feature`), and 6
  (`server/tests/policy/test_pdp_properties.py`) implemented. The
  symmetry is structurally bounded but the LIM remains open until the
  spec-audit cycles confirm steady-state coverage (Triggers for
  revisit unchanged).
- **Proposed by:** Claude (implementation agent)
- **Approved by:** Randy Nel Gupta
- **Related ADRs:** [ADR-0001](../adr/0001-default-deny-hierarchical-policy.md), [ADR-0017](../adr/0017-response-transparency-for-filtered-data.md)
- **Related Guidelines:** BDD Guidelines §13.2 Prüfung 2 (Herkunftsanalyse), §8.2

## Resolution intent

**Classification: must-resolve.**

The symmetry between BDD assertions and server emissions of reason
codes is accepted for the current implementation phase because no
fully clean alternative is available within the project's scope (see
"Why the clean solution is not chosen" below). The six mitigations
listed reduce the residual risk to an acceptable level for V1 but
do not eliminate the underlying symmetry.

This is explicitly a debt that must be paid back. A future iteration
is expected to:

- lift the reason-code vocabulary out of a shared-string contract into
  a form that is independently verifiable (candidates include property-
  based integration tests, a narrow formal specification of the PDP, or
  a runtime assertion that couples codes to typed situation records in
  a way BDD assertions cannot fake), and
- reduce the suite's reliance on string-equality assertions for
  semantic categories.

The resolution of this record is tracked in the project task list
under a dedicated task (to be created) and is expected to be revisited
by the next major version boundary of the server, at the latest.

## Context

[ADR 0017] defines a closed vocabulary of categorical reason codes
(`rule_matched`, `sender_not_whitelisted`, `sender_blacklisted`,
`folder_hidden`, `account_hidden`, `visibility_below_<level>`,
`capability_missing`, `auth_failed`, …) that the server attaches to
every PDP decision so that caller-side reasoning does not degrade to
hallucination.

The BDD suite asserts these codes as exact strings:

```gherkin
Then the response field reason equals "sender_not_whitelisted"
```

The identical string appears verbatim in three locations:

1. The feature file (as the Then-value).
2. The step implementation (as the comparison target).
3. The server implementation (as the literal emitted on the matching
   code path).

## Nature of the weakness

BDD Guidelines §13.2 Prüfung 2 demands that a concrete Then-value be
either (a) literally present in Given/When, or (b) derivable from a
business rule that is visible in the scenario. A reason-code string is
neither: it is a vocabulary token of the external API contract.

A server implementation that, for a given class of situations,
hardcodes the syntactically correct reason code alongside its correct
structural envelope (matching `decision`, `visibility_applied`,
`redacted_fields`) while internally failing to evaluate the policy
hierarchy, can pass every string-equality assertion in the BDD suite.
The test equality is symmetric — it cannot, on its own, prove that the
code arose from an honest evaluation.

## Why the clean solution is not chosen

API contract tests inherently share vocabulary with the API under
test. Removing that sharing would require one of:

- **Formal specification of the PDP** (XACML, TLA+, or a small custom
  specification language, with symbolic equivalence checking against
  the implementation). Rejected in ADR 0001 for deliberate scope
  reasons — "a policy language deliberately much narrower than XACML";
  a formal engine would invert that choice.
- **Replacing string assertions with semantic equivalence tests** that
  translate reason codes to situation categories at match time. This
  pushes Fachlogik into the step code, directly contradicting BDD
  Guidelines §1.3 and §5.1.
- **Generating reason codes at runtime from typed situation records**
  (discriminated unions serialized to strings). Technically possible,
  but the emitted strings would still end up in the BDD assertions
  as the same shared vocabulary. The symmetry is displaced, not
  eliminated.

The symmetry is structural to contract testing, not a defect of this
project's implementation.

## Mitigations in place

1. **Vocabulary as API-contract artefact.** [ADR 0017] is extended
   with a canonical table of every reason code, the condition under
   which it is emitted, and the caller-side reaction it is designed to
   enable. Feature files reference the table rather than inventing
   codes. The string is then a legitimate externally observable
   contract, not an internal constant.

2. **Structural cross-checks per scenario.** Every scenario asserting
   a reason code also asserts on the accompanying structural fields
   (`decision`, `visibility_applied`, `redacted_fields`,
   `hidden_folders_count`, etc.). A server that only hardcodes the
   reason code cannot simultaneously fake all accompanying fields for
   every situation class.

3. **Dedicated contract feature.** `bdd/features/tool_surface/
   reason_code_contract.feature` explicitly declares itself a
   vocabulary contract test: it asserts that every code the server
   emits is in the canonical set and that every canonical code is
   reachable by at least one documented condition. This scoped
   symmetry is accepted; the rest of the suite is freed to be about
   behaviour, not vocabulary.

4. **Variance discipline (BDD Guidelines §2.3).** Each reason code
   appears in at least two scenarios with different
   sender/folder/account combinations. Hardcoding becomes
   combinatorially harder to hide.

5. **Spec audit with reason-code-specific prüfauftrag (Task #19
   extension).** The independent audit agent inspects the server
   source and confirms, per reason code, that the code is derived
   from a condition expression rather than being a literal in a
   situation-specific branch.

6. **Property-based unit tests on the PDP (new server-side task).**
   `server/tests/policy/` uses `hypothesis` to generate random
   (policy, caller, message) triples and asserts that the emitted
   reason code is consistent with the triple's structural outcome,
   across many synthesized combinations the BDD suite does not
   enumerate.

## Residual risk

Concretely, the following scenario remains possible:

> A future change adds a new DENY category to [ADR 0017] and provides
> a BDD scenario that asserts the new reason code. A server
> implementation that hardcodes the new string along with a plausible
> structural envelope for the new category — and happens to cover the
> specific test situation — passes both the new BDD scenario and the
> contract feature, while getting the underlying condition wrong on
> adjacent situations that the BDD suite does not cover.

The risk is concentrated at the *introduction* of new reason codes,
not at steady state. Mitigations 4 (variance) and 5/6 (audit +
property-based testing) reduce but do not eliminate it; a regressive
change inside a new reason-code branch could still evade detection
for one audit cycle.

## Triggers for revisit

- The spec audit (Task #19) produces **≥ 3 findings** traceable to
  this symmetry across any two consecutive audit cycles.
- A production or staging incident is traced to a reason code that
  was syntactically correct but semantically wrong for the triggering
  situation.
- A new DENY category is added to [ADR 0017] without a corresponding
  expansion of the property-based unit tests at `server/tests/policy/`.
- A formal specification engine or an integration-level property-based
  testing harness becomes practical to adopt for this project's
  scope.

## References

- [ADR 0001] — policy hierarchy; narrower than XACML, formal engine
  explicitly out of scope.
- [ADR 0017] — reason code vocabulary and response transparency
  contract; the canonical table extension is a direct consequence of
  this record.
- BDD Guidelines §13.2 Prüfung 2 — Herkunftsanalyse, the source of
  the constraint being bounded here.
- BDD Guidelines §8.2 — "BDD does not replace formal verification";
  the residual risk is explicitly outside what BDD is expected to
  catch.
- `bdd/features/tool_surface/reason_code_contract.feature` — to be
  added as Mitigation 3.
- `server/tests/policy/` — to be added as Mitigation 6 (property-
  based PDP tests).

[ADR 0001]: ../adr/0001-default-deny-hierarchical-policy.md
[ADR 0017]: ../adr/0017-response-transparency-for-filtered-data.md
