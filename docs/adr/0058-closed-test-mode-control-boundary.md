# ADR 0058: Closed Test-Mode Control Boundary

- **Status:** Accepted
- **Date:** 2026-11-17
- **Deciders:** Randy Nel Gupta

## Context

[ADR 0023](0023-test-only-control-surface.md) documented an initial set of test-only primitives (`IMAP_MCP_CRASH_AT`, `_test_run_recovery`), but later additions (`IMAP_MCP_FAKE_NOW_UTC`, `IMAP_MCP_FAKE_NOW_FILE`, `IMAP_MCP_SAGA_PAUSE_MARKER`, `IMAP_MCP_SAGA_RESUME_FILE`, `_test_run_audit_rotation`) grew outside the gate:

1. `IMAP_MCP_FAKE_NOW_UTC`, `IMAP_MCP_FAKE_NOW_FILE`, and `IMAP_MCP_CRASH_AT` were honoured even when `IMAP_MCP_TEST_MODE` was unset, allowing a stray environment variable in production to skew audit day boundaries, policy age predicates, or crash mid-saga.
2. Operator runtime settings (such as `IMAP_MCP_APPEND_TIMEOUT`) were blurred with test-only hooks.

## Decision

We restrict all test-only fault, clock, pause, and recovery hooks to a **closed inventory that is completely inert without `IMAP_MCP_TEST_MODE=1` and causes fatal server startup refusal if any test-hook environment variable is set when `IMAP_MCP_TEST_MODE != "1"`**.

### 1. Closed inventory of test-only primitives

1. **Six test-hook environment variables (forbidden without `IMAP_MCP_TEST_MODE=1`):**
   - `IMAP_MCP_CRASH_AT` (hard process exit at named saga step)
   - `IMAP_MCP_FAKE_NOW_UTC` (pinned UTC clock timestamp)
   - `IMAP_MCP_FAKE_NOW_FILE` (path to shared mutable UTC clock file)
   - `IMAP_MCP_SAGA_PAUSE_MARKER` (file path written when saga pauses at named step)
   - `IMAP_MCP_SAGA_RESUME_FILE` (file path polled to release paused saga)
   - `IMAP_MCP_TEST_INJECT_INTERNAL_ERROR` (raises synthetic runtime exception on named tool to verify `internal_error` audit coverage)
2. **Two hidden MCP tools (absent from `tools/list`; return `-32601 Unknown tool` when `IMAP_MCP_TEST_MODE != "1"`):**
   - `_test_run_recovery`
   - `_test_run_audit_rotation`
3. **Internal underscore-prefixed handler-to-audit metadata keys** (`_audit_detail_reason`, `_from_domain`, `_suppressed_by_policy`, `_artifact_id`, `_artifact_sha256`), unconditionally stripped before any MCP response is serialized.

*(Note: `IMAP_MCP_APPEND_TIMEOUT` and `IMAP_MCP_POOL_*` are legitimate operator timeout/tuning variables documented in `README.md`, not test hooks.)*

### 2. Fatal startup gate

At process startup, if `IMAP_MCP_TEST_MODE != "1"` and **any** of the six test-hook environment variables listed in §1.1 is set to a non-empty value, the server logs a fatal configuration error naming `IMAP_MCP_TEST_MODE` and the offending variable and exits non-zero before opening any listener or mailbox connection.

## Consequences

### Positive

- **Zero production contamination.** No stray fake-clock, crash, pause, or fault variable can silently alter production behaviour.
- **Complete auditability of runtime exceptions.** `IMAP_MCP_TEST_INJECT_INTERNAL_ERROR` lets BDD scenarios verify `internal_error` auditing ([ADR 0048](0048-pre-effect-audit-and-test-boundary.md)) deterministically under `@test_hooks`.

### Negative

- Synthetic harnesses that pin server time via `IMAP_MCP_FAKE_NOW_UTC` (including the local usability runner, [ADR 0060](0060-usability-matrix-evaluation-boundaries.md)) must pass `IMAP_MCP_TEST_MODE=1` explicitly.

### Neutral

- Core discovery and non-goal BDD features execute without `IMAP_MCP_TEST_MODE` to verify the exact production surface.

## Security Implications

- **Attack surface.** Prevents `_test_run_*` tools from existing on production MCP endpoints and blocks un-gated clock manipulation of `newer_than`/`older_than` rules.
- **Trust boundaries.** Separates operator tuning variables (`IMAP_MCP_POOL_*`, `IMAP_MCP_APPEND_TIMEOUT`) cleanly from test-only fault hooks.
- **Data exposure.** Underscore-prefixed internal audit fields are stripped before JSON response serialization in all modes.
- **Failure modes.** Misconfigured test-hook variables fail closed at startup (`exit != 0`).
- **Auditability.** Calling `_test_run_*` without `IMAP_MCP_TEST_MODE=1` writes a chained `unknown_tool` `DENY` record ([ADR 0048](0048-pre-effect-audit-and-test-boundary.md)).

## Alternatives Considered

- **Silently ignore test-hook environment variables when `IMAP_MCP_TEST_MODE` is unset.** Rejected: failing fast catches leaked test variables in deployment manifests immediately instead of masking misconfiguration.
- **Separate test-only build binary.** Rejected: violates the BDD principle of testing the exact artefact shipped to production.
- **Allow `IMAP_MCP_FAKE_NOW_*` without `IMAP_MCP_TEST_MODE=1`.** Rejected: clock overrides alter security-critical age caps and audit rotation dates.

## References

- Supersedes [ADR 0023](0023-test-only-control-surface.md).
- [ADR 0048](0048-pre-effect-audit-and-test-boundary.md) — audit log format and `internal_error` record.
- [ADR 0060](0060-usability-matrix-evaluation-boundaries.md) — usability runner clock pinning.
