# ADR 0012: Python 3.11+ Runtime and Library Stack

- **Status:** Accepted
- **Date:** 2026-04-20
- **Deciders:** Randy Nel Gupta

## Context

The architectural decisions up to this point (policy engine, saga,
OAuth2, secret store, connection pool) are language-agnostic. The
implementation language is not.

Realistic candidates are Python, TypeScript/Node, and Go. Each has
different consequences for library availability, operational deployment,
review effort, and alignment with adjacent work.

Two adjacent facts weigh on the decision:

- The existing `imap-agent` ([ADR 0020]) is Python and uses
  `aioimaplib` in production. It will eventually become a client of
  this server, and a shared IMAP library reduces duplicated work.
- The reference MCP SDK is mature in Python (`mcp` package). Other
  SDKs exist but are less used.

## Decision

The implementation uses **Python 3.11 or newer**, with the following
core libraries:

| Concern                    | Library                          | Rationale |
|----------------------------|----------------------------------|-----------|
| Async IMAP client          | `aioimaplib`                     | Mature async client with IDLE support; used in `imap-agent`; only credible option in Python. |
| MCP protocol               | `mcp` (official SDK)             | Reference implementation; stdio, SSE, HTTP transports included. |
| WAL storage                | `aiosqlite`                      | Async wrapper over the stdlib `sqlite3`; satisfies [ADR 0007]. |
| Configuration & policy     | `pydantic` v2                    | Strict validation, deterministic errors, discriminated unions for mode/`grant`/`cap`. |
| HTTP (OAuth2)              | `httpx`                          | `asyncio`-native, HTTP/2, explicit timeouts. |
| Testing                    | `pytest` and `behave`            | `behave` keeps BDD style consistent with `imap-agent`; `pytest` for unit. |
| Logging                    | `structlog` (or equivalent)      | Structured JSON output aligns with audit format in [ADR 0021]. |

The minimum runtime is Python 3.11 because task groups (PEP 654) and
`ExceptionGroup` simplify saga cancellation and retry logic. 3.12+ is
preferred in deployment but not required.

Packaging uses `pyproject.toml` with `hatch` (or equivalent) and pins
via `uv` or `pip-tools` lockfiles. Distribution in V1 is source + a
single Dockerfile; a wheel on an index is future work.

## Consequences

### Positive

- **Consistency with `imap-agent`.** Operators and contributors do not
  switch mental models between the two. Shared BDD style, shared
  testing idioms.
- **Mature libraries.** Every major concern has a first-choice library
  with no exotic dependencies.
- **Fast iteration on the policy engine.** Pydantic + discriminated
  unions express the whitelist/blacklist folder-mode distinction
  ([ADR 0003]) with runtime validation matching the static schema.
- **Single binary artifact via Docker.** Deployment shape matches the
  wider ecosystem.

### Negative

- **GIL limitations** for CPU-bound workloads. Saga hashing is
  I/O-dominated; the GIL is not a bottleneck for our workload. If it
  ever becomes one, hashing offloads trivially to a thread-pool.
- **No single-binary native distribution.** Go or Rust would produce
  one. The Python-plus-Docker approach is an accepted equivalent.
- **Dependency freshness.** Python package ecosystem moves fast;
  pinning and periodic lock refresh is operational overhead.

### Neutral

- Concurrency model is `asyncio`. No threads beyond a small,
  well-bounded pool (hashing, blocking subprocess calls to GPG).

## Security Implications

- **Supply chain.** Every dependency is a potential supply-chain
  exposure. The stack is small and widely reviewed, but dependency
  pinning, hash-verified installs (`pip install --require-hashes`),
  and scheduled audits (`pip-audit` / `safety`) are required operator
  practices documented in the security manual.
- **Type-checking discipline.** `mypy --strict` (or `pyright strict`)
  is a CI gate. Policy-engine bugs that slip past types are unlikely
  to be the same bugs that would affect Go, but within Python they
  are the main line of defence against silently-ignored constraints.
- **Input parsing.** Pydantic v2's strict mode rejects type coercions
  that might otherwise mask malformed config. This is relied on by
  policy validation to refuse suspect input rather than auto-correct.
- **No dynamic code in config.** `pickle`, `eval`, `yaml.unsafe_load`,
  and any equivalent loader are prohibited. Config parsing uses
  `yaml.safe_load` exclusively.
- **asyncio safety.** Saga-recovery code uses structured concurrency
  (`TaskGroup`), so a failure in one branch cancels siblings; leaked
  tasks with dangling auth state are structurally impossible.
- **Timing side-channels.** `hmac.compare_digest` is used for every
  token comparison. String equality (`==`) on secret material is
  prohibited; this is a documented review-checklist item.

## Alternatives Considered

- **TypeScript / Node.** Rejected primarily on the IMAP library front:
  `imapflow` is the best option and is adequate but qualitatively
  behind `aioimaplib`. Consistency with `imap-agent` further
  disadvantages a language switch for no material benefit.
- **Go.** A strong candidate for a long-running secure daemon.
  Rejected for V1 on cost grounds: no existing Go experience in the
  adjacent projects; the productivity penalty of re-learning a stack
  is not justified until Python shows concrete limitations. Revisited
  if/when the project demonstrates such limitations.
- **Rust.** Same reasoning as Go, stronger on correctness, weaker on
  ecosystem breadth for IMAP / OAuth / MCP; deferred indefinitely.
- **Mixed (Go core + Python plugins).** Rejected as premature
  optimization; adds two stacks' worth of review burden to a V1 that
  has not yet proven single-stack limits.

## References

- [ADR 0003] — pydantic-friendly discriminated union for folder mode.
- [ADR 0007] — SQLite/aiosqlite WAL.
- [ADR 0020] — `imap-agent` consistency.
- [ADR 0021] — audit-log JSON format (structlog output).
- `aioimaplib`: <https://github.com/bamthomas/aioimaplib>
- MCP Python SDK: <https://github.com/modelcontextprotocol/python-sdk>

[ADR 0003]: 0003-whitelist-blacklist-folder-modes.md
[ADR 0007]: 0007-sqlite-as-wal-store.md
[ADR 0020]: 0020-imap-agent-as-future-client.md
[ADR 0021]: 0021-audit-log-format.md
