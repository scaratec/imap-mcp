# LIM 0024: Structural reason-code symmetry in black-box contract tests

- **Status:** Mitigated
- **Resolution intent:** permanent (architectural boundary)
- **Date proposed:** 2026-11-17
- **Date approved:** 2026-11-17
- **Proposed by:** Architecture review (superseding [LIM-0001](0001-reason-code-symmetry-in-bdd.md))
- **Approved by:** Randy Nel Gupta
- **Scope:** bdd/features/tool_surface/reason_code_contract.feature, docs/contract/registry.yaml, server/tests/policy
- **Related ADRs:** [ADR-0044](../adr/0044-normalized-envelope-and-code-registry.md), [ADR-0062](../adr/0062-closed-reason-and-error-code-registry.md)
- **Related Guidelines:** BDD Guidelines §2.3 (variance), §8.2, §13.2 Prüfung 2

## Resolution intent

`permanent` (approved `Accepted` and transitioned to `Mitigated` on 2026-11-17 once all four mitigations below were active). Whenever a black-box specification pins exact categorical
reason and error tokens (`account_hidden`, `not_found`, etc.), both the
feature assertions and the server's contract registry name the same closed
token set. That string-level symmetry is inherent to vocabulary contract
testing rather than a removable bug, and is permanently bounded by four
independent mitigations below.

## Context

[LIM-0001](0001-reason-code-symmetry-in-bdd.md) originally classified
reason-code symmetry as `must-resolve` while noting in its prose that the
symmetry itself is structural to contract testing. Superseding `LIM-0001`
with this `permanent` `Mitigated` record resolves that classification
contradiction cleanly under `3.0.0`.

## Nature of the weakness

A test asserting `reason equals "folder_hidden"` and a handler returning
`"folder_hidden"` agree on the token spelling; without structural variance
and algebraic property checks, a hardcoded constant on one code path could
satisfy a single-example contract check.

## Why the clean solution is not chosen

Eliminating shared token names between black-box tests and the server is
logically impossible when the token strings themselves *are* the public
wire contract ([ADR-0062](../adr/0062-closed-reason-and-error-code-registry.md)).

## Mitigations in place

1. **Single machine-readable registry (`docs/contract/registry.yaml`, [ADR-0062](../adr/0062-closed-reason-and-error-code-registry.md)):** Checked against both emitted audit/response codes and feature tables.
2. **Every declared code exercised by active BDD scenarios:** All 3 caller `ALLOW` reasons, 17 caller `DENY` reasons, and 22 `error.type` codes have active scenarios with structural variance across tools, folders, and modes.
3. **Hypothesis algebraic property suite (`server/tests/policy/`):** Verifies PDP monotonicity, cap-overrides, non-interference, and reason-code consistency over synthesized `(policy, caller, message)` inputs.
4. **Independent Spec-Audit cycles (`docs/spec-audit/`):** Audit step definitions without reading `server/`.

## Residual risk

A newly added reason code whose two BDD scenarios share an overly narrow
setup could miss an edge-case branch not covered by the Hypothesis
generators.

## Triggers for revisit

- Any Spec-Audit cycle finding an unreachable or single-scenario caller
  reason code in `docs/contract/registry.yaml`.

## References

- Supersedes [LIM-0001](0001-reason-code-symmetry-in-bdd.md).
- [ADR-0044](../adr/0044-normalized-envelope-and-code-registry.md), [ADR-0062](../adr/0062-closed-reason-and-error-code-registry.md).
