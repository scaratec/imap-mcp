# imap-mcp

A security-focused [Model Context Protocol][mcp] server that mediates LLM
access to IMAP mailboxes under strict, auditable policy. Designed for
agents that need to read, classify, move, and archive mail — but must be
prevented from reading what they shouldn't, moving what they shouldn't,
or leaving no trace of what they did.

[mcp]: https://modelcontextprotocol.io/

## Status

**V1 — all 192 BDD scenarios green, 0 skipped.** The server is
runnable on stdio and HTTP/SSE transports. Gmail and standard IMAP
providers are supported.

## Quick start

```bash
# Install
pip install sc-imap-mcp
# Or from source:
cd server && pip install -e .

# Create a config directory (see Configuration below)
mkdir -p /etc/imap-mcp/{policies}

# Run on stdio (for Claude Desktop, Cline, etc.)
IMAP_MCP_CONFIG_DIR=/etc/imap-mcp \
IMAP_MCP_CALLER_ID=my-agent \
  imap-mcp --transport stdio

# Run on HTTP (for multi-agent setups)
IMAP_MCP_CONFIG_DIR=/etc/imap-mcp \
  imap-mcp --transport http --host 127.0.0.1 --port 8080
```

## Architecture

```
┌──────────────────────────┐
│  LLM agent (MCP client)  │
└────────────┬─────────────┘
             │ MCP (stdio or HTTP/SSE)
┌────────────▼─────────────────────────────────────────┐
│  imap-mcp                                            │
│  ┌─────────────────────────────────────────────────┐ │
│  │ Policy Decision Point  — default-deny           │ │
│  │ Redaction / Transparency Layer                  │ │
│  │ Transaction Manager (WAL + saga)                │ │
│  │ IMAP Core · Connection Pool · OAuth2 adapters   │ │
│  │ Pluggable Secret Store                          │ │
│  │ Append-only Audit Log with hash chain           │ │
│  └─────────────────────────────────────────────────┘ │
└─────────┬───────────────────┬────────────────────────┘
          │ IMAP              │ IMAP
┌─────────▼────┐   ┌──────────▼────┐
│ Account A    │   │ Account B     │
│ (e.g. Gmail) │   │ (e.g. Dovecot)│
└──────────────┘   └───────────────┘
```

## Configuration

The server reads its configuration from a directory of YAML files
pointed to by `IMAP_MCP_CONFIG_DIR`.

```
config/
├── accounts.yaml        # IMAP accounts, secret store, audit, WAL
├── callers.yaml         # MCP callers and their auth
└── policies/
    ├── invoice-agent.yaml
    └── overview-bot.yaml
```

### accounts.yaml

Defines every IMAP account the server may connect to, plus the
secret store backend, audit log, and WAL configuration.

```yaml
accounts:
  - id: company-mail
    provider: imap-standard       # or "google" for Gmail
    host: imap.example.com
    port: 993
    auth:
      type: password              # or "xoauth2"
      secret_ref: secret://accounts/company-mail/password
    token_cache: memory_only      # or "persist_all" (OAuth only)

  - id: gmail-invoices
    provider: google
    host: imap.gmail.com
    port: 993
    auth:
      type: xoauth2
      secret_ref: secret://accounts/gmail-invoices/refresh_token
      oauth_scope: https://mail.google.com/
    token_cache: persist_all

secret_store:
  backend: file_dir               # or "env_var" or "gpg_file"
  path: /etc/imap-mcp/secrets

audit:
  directory: /var/log/imap-mcp/audit
  hot_days: 90                    # days before gzip compression
  warm_days: 275                  # additional days as .gz
  delete_after_days: 365          # total age before deletion
  external_root_hook: /usr/local/bin/audit-root-notify

wal:
  path: /var/lib/imap-mcp/wal.db
```

#### Account fields

| Field | Required | Default | Description |
|---|---|---|---|
| `id` | yes | — | Unique identifier referenced in policies |
| `provider` | no | `imap-standard` | `imap-standard`, `google`, or `google-mock` |
| `host` | no | `127.0.0.1` | IMAP server hostname |
| `port` | no | `143` | IMAP port (993 for IMAPS) |
| `auth.type` | yes | — | `password` or `xoauth2` |
| `auth.secret_ref` | yes | — | Reference to the secret store (e.g. `secret://accounts/x/password`) |
| `auth.oauth_scope` | no | — | OAuth2 scope for xoauth2 accounts |
| `token_cache` | no | `memory_only` | `memory_only` (access tokens in RAM only) or `persist_all` (also persisted) |

#### Secret store backends

| Backend | Description | Config fields |
|---|---|---|
| `file_dir` | Plaintext files; confidentiality from the surrounding system (git-crypt, SOPS, LUKS) | `path` |
| `env_var` | Read-only from environment variables. Secret path segments become `UPPER_SNAKE_CASE` with `__` separators. Read-only — OAuth bootstrap refuses to start against this backend. | — |
| `gpg_file` | Per-file GPG decryption using the operator's key | `path`, `recipient`, `gnupghome` |

Secret references use the format `secret://path/segments`. For `file_dir`,
this resolves to `{path}/path/segments`. For `env_var`, `secret://callers/invoice-agent/token`
becomes the environment variable `IMAP_MCP_SECRET__CALLERS__INVOICE_AGENT__TOKEN`.

### callers.yaml

Defines every MCP caller (agent) that may connect to the server.

```yaml
callers:
  - id: invoice-agent
    policy: invoice-policy
    auth:
      type: shared_token
      token_secret_ref: secret://callers/invoice-agent/token

  - id: overview-bot
    policy: overview-policy
    auth:
      type: stdio_trusted
```

#### Caller auth types

| Type | Transport | Mechanism |
|---|---|---|
| `stdio_trusted` | stdio only | Caller ID set via `IMAP_MCP_CALLER_ID` env var by the orchestrator. Forbidden on HTTP transport (startup error). |
| `shared_token` | stdio + HTTP | Bearer token verified with constant-time comparison. On HTTP: `Authorization: Bearer <token>` header. |

Caller identity is immutable for the session duration. No impersonation
primitive exists.

### policies/\<name\>.yaml

Each policy file defines what one caller may see and do.

```yaml
name: invoice-policy
accounts:
  company-mail:
    - path: INBOX/Invoices
      mode: whitelist
      default: NONE
      mark_seen: true
      move_out: true
      rules:
        - match: { from_domain: hornbach.de }
          grant: FULL
        - match: { from_domain: amazon.de }
          grant: FULL

    - path: Archive/Invoices-2026
      mode: whitelist
      default: NONE
      accept_incoming: true
      rules: []

  gmail-invoices:
    - path: Rechnungen
      mode: whitelist
      default: NONE
      mark_seen: true
      move_out: true
      rules:
        - match: { from_domain: hornbach.de }
          grant: FULL
```

#### Folder policy fields

| Field | Required | Default | Description |
|---|---|---|---|
| `path` | yes | — | IMAP folder path (e.g. `INBOX`, `INBOX/Invoices`, `[Gmail]/All Mail`) |
| `mode` | yes | — | `whitelist` (default=NONE, rules grant access) or `blacklist` (default>NONE, rules cap access) |
| `default` | yes | — | Default visibility level when no rule matches |
| `rules` | no | `[]` | Sender-specific overrides (see below) |
| `mark_seen` | no | `false` | Can toggle `\Seen` flag |
| `mark_tagged` | no | `false` | Can set keywords and `\Flagged` |
| `move_out` | no | `false` | Can remove messages from this folder |
| `accept_incoming` | no | `false` | Can receive messages moved/copied in |
| `draft_append` | no | `false` | Can append new drafts |

#### Visibility levels

Each rule grants exactly one level from this hierarchy:

```
NONE < COUNT < METADATA < ENVELOPE < HEADERS < BODY < FULL
```

| Level | What is exposed |
|---|---|
| `NONE` | Nothing (message is invisible) |
| `COUNT` | Message count only (`folder_stats`) |
| `METADATA` | UIDs, sizes, flags (`search`) |
| `ENVELOPE` | From, To, Subject, Date (`fetch_envelope`) |
| `HEADERS` | Full RFC 5322 header block (`fetch_headers`) |
| `BODY` | Plain-text and HTML bodies (`fetch_body`) |
| `FULL` | Everything including attachments (`fetch_attachment`) |

#### Sender rule grammar

Rules use a closed set of predicates. Predicates within one rule are
AND-combined; multiple rules in a folder are OR-combined.

| Predicate | Type | Example |
|---|---|---|
| `from` | exact email | `alice@example.com` |
| `from_domain` | domain (case-insensitive, trailing-dot tolerant) | `hornbach.de` |
| `to` | exact email | `billing@company.com` |
| `to_contains` | substring | `team` |
| `subject_contains` | substring (case-insensitive, NFC-normalized) | `rechnung` |
| `has_attachment` | boolean | `true` |
| `newer_than` | duration | `30d` |
| `older_than` | duration | `90d` |
| `size_gt` | bytes | `10000` |
| `size_lt` | bytes | `1500` |

In `whitelist` mode, rules use `grant: <level>`. In `blacklist` mode,
rules use `cap: <level>`. Mixing both in one folder is a parse-time
error.

## MCP tool surface

Seventeen tools, each gated on exactly one visibility level or one
capability.

### Read tools (9)

| Tool | Min visibility | Description |
|---|---|---|
| `list_accounts` | — | List visible accounts + `hidden_accounts_count` |
| `list_folders` | COUNT | List visible folders + `hidden_folders_count` |
| `list_labels` | COUNT | Gmail only: list labels with flags |
| `folder_stats` | COUNT | Message counts per visibility level |
| `search` | METADATA | Search with `matched_total` / `matched_visible` / `filtered_out` |
| `fetch_envelope` | ENVELOPE | From, To, Subject, Date + redaction metadata |
| `fetch_headers` | HEADERS | Full RFC 5322 headers |
| `fetch_body` | BODY | Plain-text and HTML bodies |
| `fetch_attachment` | FULL | MIME attachment bytes |

### Write tools (5)

| Tool | Required capability | Description |
|---|---|---|
| `mark_seen` | `mark_seen` | Toggle `\Seen` flag |
| `mark_tagged` | `mark_tagged` | Add/remove keywords |
| `move` | `move_out` + `accept_incoming` | Move message (intra-account: native MOVE; cross-account: saga) |
| `copy` | `accept_incoming` | Copy message to target folder |
| `create_draft` | `draft_append` | Append RFC 5322 draft |

### Meta tools (3)

| Tool | Description |
|---|---|
| `describe_policy` | Caller's own policy profile (accounts, folders, capabilities, hidden counts). Never reveals rule patterns or other callers. |
| `get_caller_identity` | Resolved `caller_id` for the current session |
| `get_transaction_status` | WAL state of a cross-account move saga |

### Deliberately absent

`delete`, `expunge`, `raw_imap_command`, `fetch_raw_rfc822`, cross-account
search, MCP resource subscriptions, folder CRUD, policy reload via MCP.
See [ADR 0018](docs/adr/0018-non-goal-tool-surface.md).

## Environment variables

| Variable | Required | Description |
|---|---|---|
| `IMAP_MCP_CONFIG_DIR` | yes | Path to the configuration directory |
| `IMAP_MCP_CALLER_ID` | stdio_trusted only | Caller identity for stdio transport |
| `IMAP_MCP_HTTP_HOST` | no | Bind address for HTTP (default: `127.0.0.1`) |
| `IMAP_MCP_HTTP_PORT` | no | Port for HTTP (default: `0` = ephemeral) |
| `IMAP_MCP_APPEND_TIMEOUT` | no | Timeout in seconds for IMAP APPEND |
| `IMAP_MCP_OAUTH_CLIENT_ID` | no | OAuth2 client ID (default: built-in) |

## OAuth2 bootstrap

For accounts with `auth.type: xoauth2`, run the interactive bootstrap
once per account to obtain the refresh token:

```bash
imap-mcp-oauth-bootstrap --account gmail-invoices
```

This opens a browser for the OAuth consent flow. On success, the
refresh token is stored in the secret store. The server then exchanges
it for access tokens automatically.

PKCE is mandatory. Scope minimization is enforced: a readonly-scope
account refuses write operations regardless of policy.

## Policy reload

Send `SIGHUP` to the running server process. The server re-parses the
entire config directory, validates it, and swaps the in-memory state
atomically. Parse or validation errors preserve the previous policy
and write an audit record with the error. In-flight saga transactions
complete under their original policy; new requests see the new one.

```bash
kill -HUP $(pidof imap-mcp)
```

## Gmail support

Accounts with `provider: google` get explicit Gmail semantics:

- `list_labels` tool available (Gmail only)
- `search` results include `canonical_all_mail_uid` for cross-label deduplication
- Intra-account `move` implemented as label swap (not physical MOVE)
- Cross-account sagas fetch deterministically from `[Gmail]/All Mail`
- `[Gmail]/Trash`, `[Gmail]/Drafts` etc. are policy-addressable folders

## Audit log

Append-only JSONL with SHA-256 hash chain, one file per UTC day.
Strict no-content-leak rule: no message bodies, subjects, attachment
filenames, OAuth tokens, or cleartext sender addresses in DENY records.

```
/var/log/imap-mcp/audit/
├── 2026-05-06.jsonl       # hot (plain, mode 0600)
├── 2026-02-01.jsonl.gz    # warm (gzipped, mode 0400)
└── ...
```

Each record contains: `ts`, `seq`, `prev_hash`, `tool`, `caller_id`,
`decision` (ALLOW/DENY), `reason`, `result`, and tool-specific fields.

## Implementation stack

- **Language:** Python 3.11+
- **IMAP:** `aioimaplib`
- **MCP:** the official `mcp` SDK (stdio + HTTP/SSE)
- **Storage:** `aiosqlite` for WAL; YAML for config
- **Validation:** `pydantic` v2 (strict mode)
- **OAuth:** `httpx` + first-party flow implementation
- **Testing:** `pytest` (property tests) + `behave` (BDD, 192 scenarios)

## Testing

```bash
# BDD suite (requires Docker for IMAP fixtures)
cd bdd && docker compose -f docker/docker-compose.yml up -d
.venv/bin/behave --no-color --format=progress features/

# Server property tests
cd server && .venv/bin/pytest tests/policy/ -q
```

## Documentation

The design is captured in 23 ADRs under [`docs/adr/`](docs/adr/).
Limitation records under [`docs/limitations/`](docs/limitations/)
(all 9 resolved). Error-path analysis at
[`docs/error_path_analysis.md`](docs/error_path_analysis.md).

## License

GPL-3.0-or-later. See [`LICENSE`](LICENSE).
