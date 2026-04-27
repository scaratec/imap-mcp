# imap-mcp server

Reference implementation of the server described in
[`../docs/adr/`](../docs/adr/) and [`../README.md`](../README.md).

This directory is **self-contained** — no imports from `../bdd/` or any
sibling. The test suite under `../bdd/` exercises this server as a black
box through the MCP protocol.

## Setup

```sh
cd server
python3.11 -m venv .venv
. .venv/bin/activate
pip install -e '.[dev]'
```

## Entry points

- `imap-mcp` — MCP server (stdio by default, SSE/HTTP via flags)
- `imap-mcp-oauth-bootstrap --account <id>` — interactive OAuth2 bootstrap

## Structure

```
server/
├── pyproject.toml
└── src/imap_mcp/
    ├── __main__.py
    ├── config/            # pydantic schemas, loader, SIGHUP handling
    ├── policy/            # PDP, matcher, visibility & capability logic
    ├── imap/              # aioimaplib wrapper, pool, Gmail adaptation
    ├── saga/              # WAL, transaction state machine, recovery
    ├── auth/              # caller auth, OAuth2 XOAUTH2 adapters
    ├── secrets/           # SecretStore backends
    ├── transport/         # MCP tool surface, redaction layer
    └── audit/             # JSONL writer, hash chain, retention
```

Each submodule's responsibility and rationale is spelled out in the
corresponding ADR under `../docs/adr/`.
