# ADR 0052: MCP Tool Surface 3.0.0

- **Status:** Accepted
- **Date:** 2026-11-17
- **Deciders:** Randy Nel Gupta

## Context

Surface `2.2.0` ([ADR 0016](0016-mcp-tool-set.md), [ADR 0018](0018-non-goal-tool-surface.md), [ADR 0026](0026-tool-surface-consistency.md)) grew to 30 tools while carrying three received-mail attachment-rewrite tools (`add_attachment`, `replace_attachment`, `delete_attachment`) without an ADR, allowing extra undeclared arguments on zero-arg tools despite `additionalProperties: false`, and advertising single-capability metadata on two-folder tools (`move`, `copy`, `discard_draft`) that actually require both source and target capabilities.

## Decision

We publish **MCP Tool Surface `3.0.0` (`sc-imap-mcp` `3.0.0`) comprising 27 tools (13 read, 10 write, 4 meta)** with strict `additionalProperties: false` schema validation (`-32602`), explicit `required_capabilities` array metadata, and a closed permanent non-goal list.

### 1. Complete 3.0.0 tool inventory (27 tools)

- **Read tools (13):** `list_accounts` (`(none)`), `list_folders` (`COUNT`), `list_labels` (`COUNT`, Google only), `folder_stats` (`COUNT`), `search` (`METADATA`), `list_messages` (`METADATA`), `fetch_envelope` (`ENVELOPE`), `fetch_headers` (`HEADERS`), `fetch_body` (`BODY`), `list_attachments` (`BODY`), `fetch_attachment` (`FULL`), `list_drafts` (`METADATA`), `get_draft` (`BODY`).
- **Write tools (10) and `required_capabilities` metadata:**
  - `mark_seen`: `["mark_seen"]` (message floor `METADATA`)
  - `bulk_mark_seen`: `["mark_seen"]` (message floor `METADATA`)
  - `mark_tagged`: `["mark_tagged"]` (message floor `ENVELOPE`)
  - `bulk_mark_tagged`: `["mark_tagged"]` (message floor `ENVELOPE`)
  - `move`: `["move_out", "accept_incoming"]` (message floor `ENVELOPE`)
  - `copy`: `["copy_out", "accept_incoming"]` (message floor `ENVELOPE`)
  - `create_draft`: `["draft_append"]`
  - `create_reply_draft`: `["draft_append"]` (source message floor `BODY`)
  - `update_draft`: `["draft_edit"]`
  - `discard_draft`: `["move_out", "accept_incoming"]`
- **Meta tools (4):** `describe_policy`, `get_caller_identity`, `get_transaction_status`, `tool_surface_info` (each `category: "meta"`, `reason: "authenticated"`, omitting `minimum_visibility` and `required_capabilities`).

### 2. Strict input validation (`-32602`) and non-goals (`-32601`)

- Every tool and nested input object enforces `additionalProperties: false`. Missing required fields, wrong JSON types, CR/LF in header strings, and unknown or retired keys return JSON-RPC `-32602 Invalid params` before any IMAP connection opens.
- **Permanent non-goals (`-32601 Unknown tool`):** `delete`, `hard_delete`, `delete_draft`, `expunge`, `store_deleted_flag`, `add_attachment`, `replace_attachment`, `delete_attachment`, `create_folder`, `rename_folder`, `delete_folder`, `create_account`, `delete_account`, `raw_imap_command`, `fetch_raw_rfc822`, `impersonate`, `execute_on_behalf`, `bypass_redaction`, `subscribe_to_new_mail`, `search_across_accounts`, `reload_policy`, `rotate_tokens`, `drain_pools`, `get_audit_log`, `get_server_logs`, `get_server_metrics`, `get_server_config`, `initiate_oauth`. (`bulk_move` is not a permanent non-goal; it remains deferred under [LIM 0011](../limitations/0011-bulk-move-deferred.md) and therefore also answers `-32601` today.)

## Consequences

### Positive

- **Accurate, smaller 27-tool surface.** Retires received-mail forgery tools and publishes complete `required_capabilities` lists on `tools/list`.
- **Uniform parameter hygiene.** Zero-argument and multi-argument tools reject undeclared properties identically.

### Negative

- Callers from `2.x` must pin `3.0.0` and migrate off retired tools and fields.

### Neutral

- `tool_surface_info` lists both the `2.0.0` ([ADR 0028](0028-attachment-file-sink-delivery.md)) and `3.0.0` ([ADR 0040](0040-message-centric-policy-and-gmail-composition.md)–[ADR 0061](0061-python-runtime-and-strict-verification-stack.md)) breaking cuts.

## Security Implications

- **Attack surface.** Removes three destructive rewrite tools and closes JSON parameter-smuggling vectors.
- **Trust boundaries.** Keeps administrative, secret-management, and audit-reading operations permanently off the MCP bus.
- **Data exposure.** Every surviving tool obeys [ADR 0040](0040-message-centric-policy-and-gmail-composition.md)–[ADR 0057](0057-immutable-received-mail-and-structured-drafts.md).
- **Failure modes.** Unknown tools fail with `-32601`; invalid parameters fail with `-32602`.
- **Auditability.** Both `-32601` (`unknown_tool`) and `-32602` (`invalid_arguments`) are recorded in the hash chain ([ADR 0048](0048-pre-effect-audit-and-test-boundary.md)).

## Alternatives Considered

- **Ignore extra arguments on zero-argument tools (`describe_policy`).** Rejected: hides client bugs and weakens schema enforcement.
- **Keep `add/replace/delete_attachment` as deprecated aliases.** Rejected: preserves received-mail tampering code paths.
- **Ship as `2.4.0` instead of `3.0.0`.** Rejected: removing tools, capabilities, and fields is a SemVer major break.

## References

- Supersedes [ADR 0016](0016-mcp-tool-set.md), [ADR 0018](0018-non-goal-tool-surface.md), and [ADR 0026](0026-tool-surface-consistency.md).
- [LIM 0011](../limitations/0011-bulk-move-deferred.md) — `bulk_move` deferral.
