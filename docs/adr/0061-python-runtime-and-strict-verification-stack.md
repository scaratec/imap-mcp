# ADR 0061: Python 3.11+ Runtime and Strict Verification Stack

- **Status:** Accepted
- **Date:** 2026-11-17
- **Deciders:** Randy Nel Gupta

## Context

[ADR 0012](0012-python-runtime-and-library-stack.md) selected the Python 3.11+ runtime and core libraries (`mcp`, `aioimaplib`, `aiosqlite`, `pydantic` v2, `PyYAML`, `httpx`), but lacked an `Auditability` security analysis and predated the OpenTelemetry tracing extra and strict separation between `server/`, `bdd/`, and `usability/` environments.

## Decision

We implement `imap-mcp` on **Python 3.11+ (`3.11`–`3.14`) using `mcp` (`< 2`), `aioimaplib`, `aiosqlite`, strict `pydantic` v2, safe YAML loading (`PyYAML`), `httpx`, and optional `opentelemetry-sdk` (`[tracing]` extra), enforced by blocking `mypy --strict`, `ruff`, `pytest` (including Hypothesis property tests), and isolated black-box `behave` BDD suites**.

### Stack and verification invariants

1. **Minimal runtime dependencies:** No web frameworks beyond the `mcp` SDK's transport layer, no ORMs, no document/PDF parsers ([ADR 0054](0054-generic-mail-mediation-scope-boundary.md)), and no dynamic code evaluation (`eval`/`exec`, unsafe YAML loaders, or `shell=True` subprocesses).
2. **Strict schema & type enforcement:** Configuration and tool schemas use strict `pydantic` v2 validation (`extra="forbid"` / `additionalProperties: false`), and `server/src/imap_mcp` passes `mypy --strict` and `ruff` with zero warnings.
3. **Hermetic test isolation:** `server/.venv` and `bdd/.venv` are independent virtual environments; `bdd/` never imports from or reads `server/` source files.

## Consequences

### Positive

- **Small, auditable pure-Python footprint.** Avoids memory-unsafe native document codecs and heavy framework dependencies.
- **Compile-time and property-level safety.** `mypy --strict`, Hypothesis PDP properties, and black-box `behave` catch type, algebraic, and wire-protocol defects before release.

### Negative

- Upper-bounding `mcp < 2` requires a dedicated migration pass when adopting `mcp` 2.x.

### Neutral

- Standard `pipx` and `pip` packaging works identically on Linux workstations and slim container images ([ADR 0051](0051-loopback-kubernetes-sidecar-topology.md)).

## Security Implications

- **Attack surface.** Minimizes third-party transitive dependencies and forbids unsafe deserialization (`yaml.load`, `pickle`) and shell execution.
- **Trust boundaries.** Strict Pydantic v2 validation rejects malformed operator YAML and untrusted MCP JSON inputs (`-32602`) before domain logic executes.
- **Data exposure.** Type-checked response models and explicit projection helpers ([ADR 0043](0043-non-interference-transparency-and-search.md)) prevent accidental leakage of internal fields.
- **Failure modes.** Dependency or schema mismatches fail deterministically at startup or request validation rather than coercing types silently.
- **Auditability.** Keeping the runtime dependency tree small and deterministic makes supply-chain review, reproducible CI checks, and hash-chained JSONL audit verification ([ADR 0048](0048-pre-effect-audit-and-test-boundary.md)) straightforward.

## Alternatives Considered

- **Go (`emersion/go-imap`).** Rejected: weaker official MCP SDK alignment and slower iteration on rich policy/MIME test suites.
- **TypeScript / Node.js (`imapflow`).** Rejected: broader transitive `npm` supply-chain surface for a security-critical credential mediator.
- **Rust (`async-imap`).** Rejected: immature async IMAP/Gmail-extension ecosystem would require maintaining custom protocol parsers.

## References

- Supersedes [ADR 0012](0012-python-runtime-and-library-stack.md).
- [ADR 0048](0048-pre-effect-audit-and-test-boundary.md), [ADR 0052](0052-tool-surface-3-0-0-and-non-goals.md), [ADR 0053](0053-non-interfering-telemetry-and-usability-isolation.md), [ADR 0054](0054-generic-mail-mediation-scope-boundary.md).
