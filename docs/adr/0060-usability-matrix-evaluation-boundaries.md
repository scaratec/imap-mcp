# ADR 0060: Usability Matrix Evaluation Boundaries

- **Status:** Accepted
- **Date:** 2026-11-17
- **Deciders:** Randy Nel Gupta

## Context

[ADR 0039](0039-usability-matrix-for-agent-behaviour.md) introduced `usability/` to measure how real LLM CLI harnesses (`agy`, `copilot`, `claude`, `opencode`) behave against `imap-mcp`, but contained two inconsistencies:

1. Its Security Implications claimed every run executed *"in an isolated environment"*, whereas several CLI harnesses require the invoking user's `$HOME` or background service for provider login tokens and are restricted via best-effort CLI flags rather than OS containers.
2. Rubrics had minor gaps (missing ideal-path counts on `R3`, missing variant policy visibility in scenario Backgrounds, and missing `IMAP_MCP_TEST_MODE=1` documentation alongside `IMAP_MCP_FAKE_NOW_UTC`).

## Decision

We evaluate non-deterministic LLM agent behaviour in **`usability/` strictly outside CI gating against ephemeral synthetic `mock-gmail` fixtures, with explicit per-variant policies and ideal-path call counts in every scenario, `IMAP_MCP_TEST_MODE=1` scoped exclusively to the per-run synthetic server subprocess, and honest documentation of best-effort CLI tool-flag isolation**.

### 1. Grading, ground truth, and non-gating execution

- Each run produces one grade (`fulfilled`, `partial`, `missed`, `misreported`, `harmful`, `not-assessable`), efficiency measurements (`tool_calls`, `ideal_ratio`, `response_bytes`, `imap_connections`), and a cross-checked post-task feedback JSON record.
- Every task scenario (`01`–`05`) declares its synthetic ground-truth mailbox, variant policy rules, and per-variant ideal call count so `ideal_ratio` always has a valid denominator.
- The usability runner exits `0` once runs complete regardless of agent grades and is never wired into deterministic CI workflows.

### 2. Synthetic server environment and honest CLI isolation posture

- Each usability run spawns a fresh `imap-mcp` subprocess pointed at an ephemeral loopback `mock-gmail` instance containing synthetic data only. Because scenarios pin calendar dates via `IMAP_MCP_FAKE_NOW_UTC`, the runner passes `IMAP_MCP_TEST_MODE=1` and `IMAP_MCP_TRACE_PAYLOADS=full` **only** to that synthetic per-run server subprocess ([ADR 0058](0058-closed-test-mode-control-boundary.md)); `_test_run_*` tools remain omitted from `tools/list`, and any unexpected call to them is recorded in the run's audit log.
- Harness isolation is **best-effort CLI tool restriction, not OS sandboxing**: drivers disable unrelated MCP servers, shell, file-write, and web tools via each CLI's flags and abort runs as `not-assessable` (`isolation_breach`) if runtime guards detect forbidden tool invocations. Operators requiring kernel-level isolation run the matrix inside a dedicated VM or container.

## Consequences

### Positive

- **Honest isolation contract.** ADR and `usability/README.md` agree on exact per-harness boundaries.
- **Self-contained rubrics.** Every scenario states complete ground truth, policy grants, and ideal call counts.

### Negative

- Running the live matrix consumes LLM provider quotas and requires operator invocation.

### Neutral

- Deterministic unit self-tests for the usability runner (`usability/tests/`) run offline in seconds without LLM calls.

## Security Implications

- **Attack surface.** Never connects usability harnesses to real mailboxes or production `IMAP_MCP_CONFIG_DIR` paths.
- **Trust boundaries.** Treats CLI flag restrictions as observational guardrails rather than security sandboxes, reinforced by transcript/audit breach checks.
- **Data exposure.** `IMAP_MCP_TRACE_PAYLOADS=full` and `IMAP_MCP_TEST_MODE=1` run solely against synthetic test data in git-ignored result directories.
- **Failure modes.** Harness crashes, timeouts, or isolation breaches mark the run `not-assessable` without corrupting other runs.
- **Auditability.** Every run archives its chained `audit/*.jsonl`, OTLP `spans.jsonl`, mailbox snapshot, and raw harness transcript for human verification.

## Alternatives Considered

- **Gate CI merges on live LLM usability runs.** Rejected: model nondeterminism and provider rate limits make CI flaky.
- **Require Docker-in-Docker containerization for every CLI harness.** Rejected: CLI harnesses (`claude`, `opencode`, `agy`) depend on interactive desktop keyrings and provider sessions that do not port cleanly into ephemeral containers.
- **Grade agents solely from their self-reported feedback JSON.** Rejected: agents frequently misreport tool calls and outcomes; grading must check mailbox state, audit logs, and traces.

## References

- Supersedes [ADR 0039](0039-usability-matrix-for-agent-behaviour.md).
- [ADR 0053](0053-non-interfering-telemetry-and-usability-isolation.md) — OpenTelemetry span attributes.
- [ADR 0058](0058-closed-test-mode-control-boundary.md) — `IMAP_MCP_TEST_MODE=1` requirement for `IMAP_MCP_FAKE_NOW_UTC`.
