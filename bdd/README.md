# BDD Test Suite

This directory hosts the executable Gherkin specification (`64` `.feature`
files, zero `@pending` scenarios) and isolated Python test harness for
`imap-mcp` `3.0.0`. It is strictly isolated from `../server/` — no Python
imports or source-tree reads cross the boundary in either direction.

## Isolation & honesty rules (BDD Guidelines v1.8.0)

- Dedicated virtual environment (`bdd/.venv/`) and `pyproject.toml`.
- Zero `from imap_mcp import ...` and zero reading of `../server/`.
- Every write/artifact scenario validates persistence over an independent
  second channel:
  - Direct IMAP queries via `imaplib` against Dovecot (`11143`, `12143`)
    or `mock-gmail`,
  - Wire-level command & connection logs from `support/imap_proxy.py`,
  - Read-only SQLite inspection of `wal.db` (`support/wal_reader.py`),
  - Direct filesystem & hash-chain verification of `audit/*.jsonl`
    (`support/audit_reader.py`),
  - Real OTLP gRPC collector spans (`support/trace_collector.py`).
- Test-only hooks (`@test_hooks`, [ADR-0048](../docs/adr/0048-pre-effect-audit-and-test-boundary.md))
  are enabled only for tagged crash/clock/pause scenarios; discovery and
  non-goal suites run without `IMAP_MCP_TEST_MODE`.

## Running

```sh
cd bdd
docker compose -f docker/docker-compose.yml up -d
.venv/bin/behave --no-color --format=progress features/
```
