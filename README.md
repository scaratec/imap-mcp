# imap-mcp

A security-focused [Model Context Protocol][mcp] server that mediates LLM
access to IMAP mailboxes under strict, auditable policy. Designed for
agents that need to read, classify, move, and archive mail — but must be
prevented from reading what they shouldn't, moving what they shouldn't,
or leaving no trace of what they did.

[mcp]: https://modelcontextprotocol.io/

## Status

**Design phase.** This repository currently contains the architectural
decision records (ADRs) under [`docs/adr/`](docs/adr/) and no executable
code. Implementation tracks 22 ADRs that define the V1 scope; the first
runnable preview is planned once the ADRs are finalized.

## What this is

`imap-mcp` sits between one or more IMAP accounts and one or more LLM
agents. Every tool call from an agent is evaluated by an in-process
Policy Decision Point before any IMAP command is issued, and every
response is redacted against a declarative policy before it is handed
back. The server speaks MCP over stdio or HTTP/SSE; the agent is a
standard MCP client.

```
┌──────────────────────────┐
│  LLM agent (MCP client)  │
└────────────┬─────────────┘
             │ MCP
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

## Why this exists

Giving an LLM raw IMAP credentials is unsafe. A real mailbox contains
invoices, contracts, health records, banking notifications, legal
correspondence, and private messages side by side. An agent may be
prompt-injected, may reach conclusions its operator never anticipated,
or may simply compose a plausible-looking request that the mailbox
owner would not have authorized.

Three properties follow from that premise, and they are the design
axes of this project:

- **Access is decided by policy, not by the agent.** A declarative
  access-control layer is interposed between every tool call and the
  mailbox. The agent cannot escalate itself; it sees what the policy
  grants and nothing else.
- **Destructive and cross-mailbox operations are transactional.** An
  agent that moves mail between accounts must not be able to lose it,
  duplicate it silently, or leave the system in an unrecoverable state
  after a crash.
- **The server is auditable end-to-end.** Every decision, every
  allow, every deny, every transaction transition is recorded in a
  tamper-evident log whose schema is closed and content-leak-free.

## What it does (V1 features)

### Policy-based access control

Default-deny at three levels:

- `AccountPolicy` — whether a caller may see an account at all.
- `FolderPolicy` — per folder within that account.
- `SenderRule[]` — fine-grained rules within the folder.

Lower levels cannot widen upper levels. A folder with no matching
policy is invisible. A sender with no matching rule in a whitelist
folder is invisible. See [ADR 0001](docs/adr/0001-default-deny-hierarchical-policy.md).

### Linear visibility levels

Each rule grants exactly one level from:

```
NONE < COUNT < METADATA < ENVELOPE < HEADERS < BODY < FULL
```

`ENVELOPE` exposes sender, recipient, subject, date. `BODY` adds
plain-text and HTML bodies. `FULL` adds attachments. Nothing below
the granted level is exposed; nothing above is implied. See
[ADR 0002](docs/adr/0002-linear-visibility-levels.md).

### Whitelist and blacklist folder modes

Each folder declares its mode explicitly:

```yaml
- folder: INBOX/Rechnungen
  mode: whitelist
  default: NONE
  rules:
    - match: { from_domain: hornbach.de }
      grant: FULL

- folder: INBOX
  mode: blacklist
  default: ENVELOPE
  rules:
    - match: { from_domain: bank.de }
      cap: NONE
```

Mixing `grant` and `cap` rules inside one folder is a parse-time
error. See [ADR 0003](docs/adr/0003-whitelist-blacklist-folder-modes.md).

### Sender-rule matcher grammar

A closed, statically auditable set of predicates: `from`,
`from_domain`, `to`, `to_contains`, `subject_contains`,
`has_attachment`, `newer_than`, `older_than`, `size_gt`, `size_lt`.
Predicates within a rule are AND-combined; OR is expressed by
multiple rules. No regex, no code evaluation, no body-content
predicates. See [ADR 0004](docs/adr/0004-sender-rule-matcher-grammar.md).

### Per-folder write capabilities

Five orthogonal boolean capabilities per folder, separate from the
read-side visibility:

| Capability         | Meaning |
|--------------------|---------|
| `mark_seen`        | Toggle the `\Seen` flag |
| `mark_tagged`      | Set `\Flagged` and user labels/keywords |
| `move_out`         | Remove messages from this folder |
| `accept_incoming`  | Accept messages moved/copied in |
| `draft_append`     | Append newly composed drafts |

Supports the **archive-without-read** pattern (deposit-only folders
the agent can never read again) and the **drafts-without-read**
pattern (the agent may write drafts but not re-read them). See
[ADR 0005](docs/adr/0005-per-folder-write-capabilities.md).

### Transactional cross-account moves

Intra-account moves use native `MOVE` (RFC 6851) and are atomic on
the server. Cross-account moves use a write-ahead-log-backed saga
with idempotent recovery:

```
BEGIN → FETCH source → APPEND target → VERIFY → DELETE source → COMMIT
```

A crash anywhere in the sequence is recoverable to a consistent
state. Message-ID is the primary idempotency key, with a SHA-256
content hash retained for forensics. The WAL lives in a local
SQLite database. Ambiguous cases escalate to an operator-visible
`needs_operator` state rather than silently guessing. See
[ADR 0006](docs/adr/0006-cross-account-move-via-saga.md),
[ADR 0007](docs/adr/0007-sqlite-as-wal-store.md),
[ADR 0008](docs/adr/0008-idempotency-via-message-id-and-hash.md).

### OAuth2 with per-account scope minimization

OAuth2 Authorization-Code flow (RFC 8252, with PKCE) for Google and
Microsoft 365. Scopes are declared per account and are themselves a
second authorization layer beneath the server's policy — an account
configured with a read-only scope cannot have writes performed
against it even if a policy bug would otherwise allow them. Service
accounts with domain-wide delegation are explicitly rejected.
Refresh tokens are always stored through the configured secret
store; access-token persistence is a per-account deployment choice.
See [ADR 0009](docs/adr/0009-oauth2-authorization-code-with-scope-minimization.md),
[ADR 0010](docs/adr/0010-configurable-token-cache-strategy.md).

### Pluggable secret store

A narrow `SecretStore` interface lets operators pick the right backend
for their environment without the server ever implementing its own
cryptography. V1 ships:

- `file_dir` — plaintext files whose confidentiality is provided by
  the surrounding system (git-crypt, sops-encrypted repo, LUKS).
- `env_var` — read-only, for Docker/Kubernetes/CI deployments.
- `gpg_file` — individual GPG-encrypted files using the operator's key.

Further backends (`sops`, `gcp_secret_manager`, `hashicorp_vault`,
`keyring`) are interface-compatible for later addition without
touching server code. See
[ADR 0011](docs/adr/0011-pluggable-secret-store-backend.md).

### Gmail as a first-class provider

Gmail's label model is exposed explicitly rather than pretending to
be standard IMAP. `describe_policy` flags such accounts with
`semantics: gmail-labels`; `search` exposes a
`canonical_all_mail_uid` for deduplication across label folders;
intra-account moves are implemented as label swaps; cross-account
fetches deterministically source from `[Gmail]/All Mail`. A
Gmail-only `list_labels` read tool is available. See
[ADR 0019](docs/adr/0019-gmail-label-semantics.md).

### Response transparency

Silent redaction is incompatible with LLM callers, who will turn
filtered results into confident but wrong conclusions. Every
response carries:

- **Hidden counts** — `hidden_accounts_count`,
  `hidden_folders_count`, `filtered_out` — so the caller knows its
  view is incomplete.
- **Categorical reason codes** — `folder_hidden`,
  `sender_not_whitelisted`, `capability_missing`, and siblings —
  so a DENY tells the caller broadly *why* without leaking which
  rule or pattern matched.
- **Per-field flags** on `fetch_envelope`, `fetch_headers`,
  `fetch_body`, `fetch_attachment` indicating what was redacted
  and why.

A `describe_policy()` meta-tool lets an agent read its own policy
profile — the accounts, folders, visibility levels, and
capabilities available to it — without probing by trial and error.
See [ADR 0017](docs/adr/0017-response-transparency-for-filtered-data.md).

### Tamper-evident audit log

All PDP decisions (allow and deny) and all saga transitions are
written to a JSONL log with a SHA-256 hash chain spanning daily
files, `fsync` per record, day-granular rotation, and an optional
external root-hash hook for off-host tamper evidence. A strict
no-content-leak rule excludes message bodies, subject text,
attachment filenames, OAuth tokens, and cleartext sender addresses
in sender-filtering DENY records. Default retention is 90 days hot,
275 days warm (gzipped), auto-delete at 365 days; all values
configurable. See [ADR 0021](docs/adr/0021-audit-log-format.md),
[ADR 0022](docs/adr/0022-audit-retention-and-access-model.md).

### Identifiable callers

Every MCP session authenticates as a named caller (`caller_id`). V1
supports two authentication types:

- `stdio_trusted` — the orchestrator running the server subprocess
  sets the identity via argv/env. Appropriate when the orchestrator
  is the trust anchor.
- `shared_token` — bearer token verified with constant-time
  comparison. Required for HTTP/SSE transports and permitted on
  stdio for additional discipline.

Caller identity is immutable for the duration of a session. No
impersonation primitive exists. See [ADR 0015](docs/adr/0015-caller-identity-and-authentication.md).

### Policy as reviewable code

Policies and account configuration are YAML files in a Git
repository. Reload is atomic on `SIGHUP`; validation failure
preserves the previous state. There is no MCP or HTTP admin API
for policy mutation — all changes pass through the repository's
code-review workflow and `git blame` attribution. See
[ADR 0014](docs/adr/0014-policy-as-git-versioned-yaml.md).

## MCP tool surface

Sixteen tools, each gated on exactly one visibility level or one
capability. See [ADR 0016](docs/adr/0016-mcp-tool-set.md) for the
full specification.

**Read (8):** `list_accounts`, `list_folders`, `folder_stats`,
`search`, `fetch_envelope`, `fetch_headers`, `fetch_body`,
`fetch_attachment`.

**Write (5):** `mark_seen`, `mark_tagged`, `move`, `copy`,
`create_draft`.

**Meta (3):** `describe_policy`, `get_transaction_status`,
`get_caller_identity`.

### Deliberately not offered

The following are formally out of scope and documented as non-goals.
Feature requests for any of them are redirected to the ADR process.

- Destructive: `delete`, `expunge`, setting `\Deleted` directly.
- Structural: `create_folder`, `rename_folder`, `delete_folder`,
  account CRUD.
- Policy-bypass: `raw_imap_command`, `fetch_raw_rfc822`,
  impersonation.
- Scope creep: MCP resource subscriptions for mail, cross-account
  search, batching tools.
- Administrative: policy reload, token rotation, audit read — these
  belong to an operator-side CLI, not to MCP.

See [ADR 0018](docs/adr/0018-non-goal-tool-surface.md).

## Implementation stack

- **Language:** Python 3.11+.
- **IMAP:** `aioimaplib`.
- **MCP:** the official `mcp` SDK (stdio, SSE, HTTP transports).
- **Storage:** `aiosqlite` for WAL; YAML for config.
- **Validation:** `pydantic` v2 with strict mode.
- **OAuth:** `httpx` + a small first-party flow implementation.
- **Testing:** `pytest` and `behave` (BDD style consistent with
  the adjacent `imap-agent` project).

See [ADR 0012](docs/adr/0012-python-runtime-and-library-stack.md).

## Relationship to imap-agent

`imap-mcp` is generic and domain-agnostic. The existing `imap-agent`
project (invoice processing, PDF extraction, LangGraph workflow, Gemini
LLM) is a planned future client of this server, not a component.
Invoice, OCR, and workflow-engine code does not live here and will
never live here. See [ADR 0020](docs/adr/0020-imap-agent-as-future-client.md).

## Threat model (summary)

The primary adversary is a prompt-injected or otherwise misbehaving
LLM caller operating within an otherwise trusted operator
environment. The server's defences against that adversary are:

- Default-deny at every policy level.
- No tool that can request operations policy cannot authorize
  (no raw IMAP, no raw RFC822 fetch, no impersonation).
- Visibility-level gating enforced before any IMAP command is
  issued.
- Constant-time comparison for all token checks.
- Bounded retry with escalation to operator for saga failures.
- Append-only, tamper-evident audit of every decision.

Secondary concerns (operator error, credential leakage, supply-chain
compromise) are discussed in individual ADRs where relevant. This
project does not claim to defend against a compromised operator
environment; file-system access by the server's user is sufficient
to defeat most of the above.

## Documentation

The design is captured in full in [`docs/adr/`](docs/adr/). The
[index](docs/adr/README.md) lists all accepted ADRs in order. Read
them as:

- **0000** — the ADR process itself.
- **0001–0005** — the policy core.
- **0006–0008** — transactions and idempotency.
- **0009–0011** — authentication and secret management.
- **0012–0014** — runtime, connection pooling, configuration.
- **0015–0020** — identity, tool surface, scope, Gmail,
  relationship to `imap-agent`.
- **0021–0022** — audit format and retention.

## Status and contributions

The repository is currently private while the design is finalized. A
public release is planned once the V1 architecture stabilizes and the
first reference implementation is runnable.

## License

GPL-3.0-or-later. See [`LICENSE`](LICENSE).
