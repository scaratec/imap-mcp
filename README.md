# imap-mcp

A security-focused [Model Context Protocol][mcp] server (`sc-imap-mcp`
**`3.0.0`**, `tool_set_version: "3.0.0"`) that mediates LLM agent access
to IMAP mailboxes under strict message-centric authorization,
non-interference, flow monotonicity, cryptographic draft provenance, and
tamper-evident hash-chained auditing.

[mcp]: https://modelcontextprotocol.io/

## Security boundary & deployment posture

> **Important ([LIM 0019](docs/limitations/0019-policy-integrity-requires-out-of-process-trust-boundary.md), [ADR 0051](docs/adr/0051-loopback-kubernetes-sidecar-topology.md)):**
> `imap-mcp` enforces policy strictly across the MCP protocol and
> attachment-sink boundaries. However, if you run `imap-mcp` over local
> `stdio` under the **same OS user** as a CLI agent that has unrestricted
> host shell or file-write permissions, that agent can edit
> `~/.config/imap-mcp/policies/*.yaml` directly on disk.
> - **Adversarial / production sandbox:** Run `imap-mcp` under a dedicated
>   OS user or as a loopback Kubernetes sidecar container with read-only
>   `/config`, `/secrets`, and `/sink` mounts ([guide](docs/ops/kubernetes-sidecar-deployment.md)).
> - **Workstation convenience (`stdio`):** Restrict shell/file-edit
>   access to `~/.config/imap-mcp` in your agent harness.

## Quick start

```bash
# Install via pipx (isolated environment, global CLI)
pipx install "sc-imap-mcp[tracing]"

# Or from local source
pipx install --force "./server[tracing]"
```

### Configure (`~/.config/imap-mcp`)

Each server instance serves **strictly one configured caller** in
`callers.yaml` ([ADR 0049](docs/adr/0049-single-caller-boundary-and-validated-loading.md)):

```text
~/.config/imap-mcp/
├── accounts.yaml        # IMAP accounts, secret_store, audit, wal, attachment_sink
├── callers.yaml         # Exactly ONE caller entry per instance
└── policies/
    └── my-policy.yaml   # Message-centric folder & rule grants
```

#### `accounts.yaml`

```yaml
accounts:
  - id: user@company.com
    provider: google              # or "imap-standard"
    host: imap.gmail.com
    port: 993                     # 993 = implicit TLS (IMAP4_SSL)
    auth:
      type: xoauth2               # or "password"
      secret_ref: secret://accounts/user@company.com/refresh_token
      oauth_scope: https://mail.google.com/
    token_cache: persist_all      # or "memory_only"

  - id: accounting@company.com
    provider: imap-standard
    host: imap.company.com
    port: 993
    user: mail2223                # optional explicit IMAP login username
    auth:
      type: password
      secret_ref: secret://accounts/accounting@company.com/password

secret_store:
  backend: file_dir               # "file_dir", "env_var", or "gpg_file"
  path: ~/.config/imap-mcp/secrets
  acknowledge_plaintext: true     # required for file_dir (ADR 0050)

audit:
  directory: ~/.config/imap-mcp/audit
  hot_days: 90
  delete_after_days: 365

wal:
  path: ~/.config/imap-mcp/wal.db

attachment_sink:
  directory: ~/Desktop/tmp/imap-mcp-outdir
```

> **Note on Gmail / M365 OAuth scopes ([ADR 0050](docs/adr/0050-secret-stores-and-oauth-posture.md)):**
> Google IMAP (`https://mail.google.com/`) and Microsoft 365 IMAP do not
> offer a read-only IMAP OAuth scope. On those providers, the `imap-mcp`
> policy is your sole read/write authorization boundary.

#### `callers.yaml`

```yaml
callers:
  - id: my-agent
    policy: my-policy
    auth:
      type: stdio_trusted         # stdio only; HTTP requires shared_token
```

#### `policies/my-policy.yaml`

```yaml
name: my-policy
accounts:
  user@company.com:
    - path: INBOX
      mode: whitelist
      default: NONE
      mark_seen: true
      move_out: true
      rules:
        - match: { flagged: true }
          grant: FULL
        - match: { from_domain: hornbach.de }
          grant: FULL

    - path: "[Gmail]/Sent Mail"
      mode: whitelist
      default: NONE
      rules:
        - match: { flagged: true }
          grant: FULL
        - match: { provenance: own }
          grant: FULL
        - match: { to_domain: hornbach.de }
          grant: FULL

    - path: "[Gmail]/Drafts"
      mode: blacklist
      default: FULL
      draft_append: true
      draft_edit: true
      move_out: true
      draft_recipients:
        allowed_domains: [company.com, hornbach.de]
      rules: []

    - path: "[Gmail]/Trash"
      mode: blacklist
      default: METADATA
      accept_incoming: true
      rules: []
```

## Policy model (3.0.0)

### Linear visibility levels ([ADR 0002](docs/adr/0002-linear-visibility-levels.md), [ADR 0043](docs/adr/0043-non-interference-transparency-and-search.md))

```text
NONE < COUNT < METADATA < ENVELOPE < HEADERS < BODY < FULL
```

- **`NONE` is strictly non-interfering:** Hidden accounts, folders, and
  `NONE` messages never alter any caller-visible count, pagination field,
  or error type (`not_found` is returned identically for missing, foreign,
  and `< METADATA` objects; [ADR 0044](docs/adr/0044-normalized-envelope-and-code-registry.md)).
- **Gmail multi-label composition ([ADR 0040](docs/adr/0040-message-centric-policy-and-gmail-composition.md)):**
  Account-wide `blacklist` `cap` rules apply across all folders, and
  `[Gmail]/All Mail` or `[Gmail]/Starred` never escalate visibility above
  a message's specific labels.

### Closed predicate grammar ([ADR 0041](docs/adr/0041-direction-aware-rule-grammar-and-provenance.md))

| Predicate | Min `criteria` floor | Matches |
|---|---|---|
| `from_address` / `from_domain` | `ENVELOPE` | Exact parsed `From:` `addr-spec` or DNS/IDNA domain |
| `to_address` / `to_domain` | `ENVELOPE` | Any parsed `To:` `addr-spec` or exact domain |
| `cc_address` / `cc_domain` | `ENVELOPE` | Any parsed `Cc:` `addr-spec` or exact domain |
| `recipient_address` / `recipient_domain` | `ENVELOPE` | Any parsed `To:`, `Cc:`, or `Bcc:` address/domain |
| `all_recipients_in_domains` | `ENVELOPE` | Non-empty recipients and **every** recipient in domain list |
| `subject_contains` | `ENVELOPE` | Case-folded Unicode-NFC substring in decoded `Subject:` |
| `has_attachment` | `BODY` | At least one MIME attachment part ([ADR 0047](docs/adr/0047-per-caller-symlink-safe-attachment-delivery.md)) |
| `flagged` | `METADATA` | IMAP `\Flagged` star (read-only to agents; human curation signal) |
| `provenance: "own"` | `METADATA` | Authentic message/draft authored by this `caller_id` ([ADR 0042](docs/adr/0042-cryptographic-and-wal-provenance.md)) |
| `newer_than` / `older_than` | `METADATA` | `INTERNALDATE` age (`1s`..`36500d` / `100y`) |
| `since` / `before` | `METADATA` | `INTERNALDATE` UTC calendar bounds (`YYYY-MM-DD`) |
| `size_gt` / `size_lt` | `METADATA` | RFC822 size in bytes (`1..1073741824`) |

### Folder write capabilities & invariants ([ADR 0045](docs/adr/0045-write-visibility-floors-and-flow-monotonicity.md))

| Capability | Governing tools | Per-message read floor & invariant |
|---|---|---|
| `mark_seen` | `mark_seen`, `bulk_mark_seen` | `eff_vis >= METADATA` |
| `mark_tagged` | `mark_tagged`, `bulk_mark_tagged` | `eff_vis >= ENVELOPE`; user keywords only (`\Flagged` and all `\`-system flags forbidden) |
| `move_out` | `move`, `discard_draft` | `eff_src >= ENVELOPE` and `eff_dst <= eff_src` (`relocation_would_escalate` otherwise) |
| `copy_out` | `copy` | `eff_src >= ENVELOPE` and `eff_dst <= eff_src` |
| `accept_incoming` | target of `move`, `copy`, `discard_draft` | Destination folder must grant `accept_incoming: true` |
| `draft_append` | `create_draft`, `create_reply_draft` | `\Drafts` folder only; reply requires source `eff_vis >= BODY`; enforces `draft_recipients` |
| `draft_edit` | `update_draft` | `\Drafts` folder + verified `X-SC-Provenance` ownership; enforces `draft_recipients` |

## MCP Tool Surface 3.0.0 (27 tools)

### Read tools (13)

`list_accounts`, `list_folders`, `list_labels` (Google), `folder_stats`,
`search`, `list_messages`, `fetch_envelope`, `fetch_headers`,
`fetch_body`, `list_attachments`, `fetch_attachment`, `list_drafts`,
`get_draft`.

### Write tools (10)

`mark_seen`, `bulk_mark_seen`, `mark_tagged`, `bulk_mark_tagged`, `move`,
`copy`, `create_draft`, `create_reply_draft`, `update_draft`,
`discard_draft`.

### Meta tools (4)

`describe_policy`, `get_caller_identity`, `get_transaction_status`,
`tool_surface_info`.

### Upgrading from 2.x to 3.0.0

1. **Retired tools & capabilities:** `add_attachment`,
   `replace_attachment`, `delete_attachment`, and `modify_message` are
   removed ([ADR 0045](docs/adr/0045-write-visibility-floors-and-flow-monotonicity.md),
   [ADR 0052](docs/adr/0052-tool-surface-3-0-0-and-non-goals.md)). Pass
   structured `attachments` to `create_draft` or `update_draft` for own
   drafts. Add `copy_out: true` on folders from which `copy` is allowed.
2. **Predicate keys:** Replace `from:` with `from_address:`, `to:` with
   `to_address:` (or `recipient_address:`), and `to_contains:` with exact
   `to_domain:` / `recipient_domain:` ([ADR 0041](docs/adr/0041-direction-aware-rule-grammar-and-provenance.md)).
3. **Secret store & single caller:** Add `acknowledge_plaintext: true`
   under `secret_store` when using `file_dir` ([ADR 0050](docs/adr/0050-secret-stores-and-oauth-posture.md)),
   and keep exactly one caller entry per `callers.yaml` ([ADR 0049](docs/adr/0049-single-caller-boundary-and-validated-loading.md)).
4. **Non-interfering responses:** `matched_total`, `filtered_out`,
   `outside_scope_total`, `hidden_*_count`, `canonical_all_mail_uid`,
   `matched_rule_index`, and `list_folders.message_count` are replaced by
   `total_visible`, `returned_count`, static `completeness`, and Gmail
   `object_id` ([ADR 0040](docs/adr/0040-message-centric-policy-and-gmail-composition.md),
   [ADR 0043](docs/adr/0043-non-interference-transparency-and-search.md)).
5. **Per-caller read-only attachment delivery:** `fetch_attachment` writes
   `<sink>/<caller_id>/<uuid4_hex>_<name>.<ext>` (`0400`) via `openat` +
   `O_NOFOLLOW | O_EXCL` and returns `saved_to` + `saved_path`
   ([ADR 0047](docs/adr/0047-per-caller-symlink-safe-attachment-delivery.md)).
   Clean aged artifacts periodically via `systemd-tmpfiles` or
   `find <sink> -type f -mtime +30 -delete` ([LIM 0015](docs/limitations/0015-attachment-sink-cleanup-out-of-scope.md)).

## OAuth2 bootstrap & remediation

Run `imap-mcp-oauth-bootstrap` interactively on a workstation to mint a
refresh token (or clear a persisted `needs_rebootstrap` state after
`invalid_grant`, [ADR 0059](docs/adr/0059-oauth2-pkce-and-durable-account-health.md), [ADR 0064](docs/adr/0064-durable-account-health-and-token-lifecycle.md)):

```bash
IMAP_MCP_CONFIG_DIR=~/.config/imap-mcp \
IMAP_MCP_OAUTH_CLIENT_ID=your-client-id.apps.googleusercontent.com \
IMAP_MCP_OAUTH_CLIENT_SECRET=your-client-secret \
  imap-mcp-oauth-bootstrap --account user@company.com
```

## Environment variables

| Variable | Required | Description |
|---|---|---|
| `IMAP_MCP_CONFIG_DIR` | yes | Path to the configuration directory |
| `IMAP_MCP_CALLER_ID` | `stdio_trusted` | Caller identity for stdio transport |
| `IMAP_MCP_OAUTH_CLIENT_ID` | `xoauth2` | OAuth2 client ID |
| `IMAP_MCP_OAUTH_CLIENT_SECRET` | `xoauth2` | OAuth2 client secret |
| `IMAP_MCP_SERVER_HMAC_KEY` | `env_var` store | 64-hex-char HMAC key when using read-only `env_var` secret store |
| `IMAP_MCP_APPEND_TIMEOUT` | no | Seconds to wait for a tagged IMAP `APPEND` response before `append_timeout` (default: `30`) |
| `IMAP_MCP_POOL_MAX_SIZE` | no | Pooled IMAP connections per account per process (default: `2`, [ADR 0055](docs/adr/0055-bounded-per-account-connection-pool.md)) |
| `IMAP_MCP_POOL_IDLE_TTL` | no | Idle connection TTL in seconds (default: `120`) |
| `IMAP_MCP_POOL_MAX_AGE` | no | Maximum connection age in seconds (default: `1800`) |
| `IMAP_MCP_POOL_ACQUIRE_TIMEOUT` | no | Seconds to wait before `pool_exhausted` (default: `30`) |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | no | OTLP collector endpoint (e.g. `http://localhost:4317`) |
| `OTEL_RESOURCE_ATTRIBUTES` | no | Extra OTel resource attributes |
| `IMAP_MCP_TRACE_PAYLOADS` | no | `off`, `redacted` (default), or `full` ([ADR 0053](docs/adr/0053-non-interfering-telemetry-and-usability-isolation.md)) |
| `IMAP_MCP_TRACE_PAYLOAD_MAX_CHARS` | no | Max chars per payload in `full` mode (default: `16384`) |

## Documentation

- **ADRs (65 records, `0000`–`0064`):** [`docs/adr/README.md`](docs/adr/README.md)
- **Limitation Records (`0001`–`0024`):** [`docs/limitations/README.md`](docs/limitations/README.md)
- **Closed Code Registry:** `docs/contract/registry.yaml` ([ADR 0062](docs/adr/0062-closed-reason-and-error-code-registry.md))
- **Error-Path Analysis:** [`docs/error_path_analysis.md`](docs/error_path_analysis.md)
- **Spec-Audit Archive:** [`docs/spec-audit/`](docs/spec-audit/)
- **Roadmap:** [`docs/ROADMAP.md`](docs/ROADMAP.md)
- **Operations:** [`docs/ops/kubernetes-sidecar-deployment.md`](docs/ops/kubernetes-sidecar-deployment.md), [`docs/ops/arc-runner-setup.md`](docs/ops/arc-runner-setup.md)

## License

GPL-3.0-or-later. See [`LICENSE`](LICENSE).
