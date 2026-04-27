# BDD Test Suite

This directory hosts the behavior-driven specification and test harness for
`imap-mcp`. It is **fully isolated** from the server implementation under
`../server/` — no Python imports cross the boundary in either direction.

## Isolation rules

- Own virtual environment: `bdd/.venv/`.
- Own `pyproject.toml` with its own dependency set.
- No `from imap_mcp import ...`. The server is a black box exercised
  through:
  - the MCP protocol over a stdio subprocess,
  - direct filesystem inspection of the audit log (`docs/adr/0021`),
  - direct read of the SQLite WAL (`docs/adr/0007`) for saga verification.
- No shared helpers, no shared type definitions, no symlinks.

This isolation is a deliberate design choice aligned with the project's
BDD guidelines (circular-test prohibition) and with the user's explicit
requirement.

## Setup

```sh
cd bdd
python3.11 -m venv .venv
. .venv/bin/activate
pip install -e '.[dev]'
```

## Running

```sh
behave                                   # full suite
behave features/policy/                  # one group
behave -n "default-deny"                 # one scenario by name
```

## Structure

```
bdd/
├── pyproject.toml
├── environment.py              # behave hooks (server lifecycle, IMAP fixture)
├── features/
│   ├── policy/                 # default-deny, visibility, whitelist, ...
│   ├── transactions/           # moves, saga, recovery
│   ├── auth/                   # caller identity, OAuth2
│   ├── tool_surface/           # MCP discovery, transparency, non-goals
│   ├── providers/              # Gmail label semantics
│   ├── audit/                  # log format, retention
│   └── steps/                  # modular step implementations
└── support/
    ├── mcp_client.py           # stdio-subprocess MCP client wrapper
    ├── imap_fixture.py         # local dovecot (docker) fixture
    ├── policy_builder.py       # YAML generator
    └── audit_reader.py         # JSONL + hash-chain parser
```

## Dependencies of the harness on the server

Exactly two:

1. The compiled server binary (`imap-mcp` entry point from
   `../server/pyproject.toml`) reachable via subprocess.
2. The documented on-disk shapes of the audit log and WAL, which are part
   of the server's specification.

Neither is a Python import. Changes to server internals that preserve
these two contracts must not require changes in this directory.
