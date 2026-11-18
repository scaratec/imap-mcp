# Error Path Analysis (3.0.0)

This document quantifies the error and security-boundary paths of the
system under test (`imap-mcp` `3.0.0`) per BDD Guidelines §4.5
("Systematische Ermittlung von Fehlerpfaden").

### Deferred classification definitions

- **`deferred A` (Unit / parser / OS-fault level):** Uniform schema
  bounds, upstream SDK transport framing, low-level RFC 3501 / MIME parser
  malformed-byte edges, or kernel `EIO`/`ENOSPC` faults exercised by
  deterministic `pytest` unit tests under `server/tests/` rather than
  end-to-end `behave` subprocesses.
- **`deferred B` (Out-of-scope / sub-millisecond race / live-provider edge):**
  Explicit non-goals (`IDLE` push), sub-millisecond internal races
  verified by unit tests, or live-Gmail UI concurrency edges outside
  deterministic local fixtures.

*(Mock self-validation scenarios that test `mock-gmail` itself rather than
`imap-mcp` are listed separately in the Appendix and are never counted
toward SUT coverage.)*

## Summary table

| #   | Layer                                                                 | Total | Covered | Deferred | Gap |
|-----|-----------------------------------------------------------------------|------:|--------:|---------:|----:|
| L1  | MCP input validation (`ADR 0052`)                                     |    22 |       8 |       14 |   0 |
| L2  | MCP protocol                                                          |     8 |       2 |        6 |   0 |
| L3  | Caller authentication & single-caller boundary (`ADR 0049`)           |    12 |      11 |        1 |   0 |
| L4  | Policy evaluation — hierarchy & folder gates (`ADR 0040`, `ADR 0044`) |    22 |      20 |        2 |   0 |
| L5  | IMAP communication per account (`ADR 0046`, `ADR 0055`)               |    14 |       8 |        6 |   0 |
| L6  | IMAP response parsing (`ADR 0056`)                                    |     9 |       2 |        7 |   0 |
| L7  | Data integrity & canonical content verification (`ADR 0042`, `0046`)  |    11 |       5 |        6 |   0 |
| L8  | OAuth2 PKCE & durable health lifecycle (`ADR 0059`, `ADR 0064`)       |    11 |       7 |        4 |   0 |
| L9  | Saga, SQLite WAL leases & startup recovery (`ADR 0046`)               |    29 |      25 |        4 |   0 |
| L10 | Pluggable secret stores (`ADR 0050`)                                  |    12 |       9 |        3 |   0 |
| L11 | Pre-effect audit chain, retention & test gate (`ADR 0048`, `0058`)    |    16 |      15 |        1 |   0 |
| L12 | Validated configuration & generation loading (`ADR 0049`)             |    14 |      13 |        1 |   0 |
| L13 | Bounded connection pool (`ADR 0055`)                                  |    12 |      11 |        1 |   0 |
| L14 | Gmail multi-label & localized SUT semantics (`ADR 0040`, `ADR 0056`)  |    32 |      29 |        3 |   0 |
| L15 | Sidecar deployment, `/healthz` & projected reload (`ADR 0051`)        |    16 |      16 |        0 |   0 |
| L16 | Drafts, replies, calendar windows & sink (`ADR 0043`–`0047`, `0063`)  |    38 |      37 |        1 |   0 |
| L17 | Non-interfering OpenTelemetry spans (`ADR 0053`)                      |    27 |      27 |        0 |   0 |
| L18 | Non-interference, Write<=Read & flow monotonicity (`ADR 0040`–`0046`) |    18 |      18 |        0 |   0 |
|     | **Total**                                                             | **323** | **263** | **60** | **0** |

---

## L1 — MCP input validation (`ADR 0052`)

| ID    | Error case                                                       | Status     | Reference |
|-------|------------------------------------------------------------------|------------|-----------|
| L1.1  | Missing required argument (`account`, `folder`, `uid`, `part_id`)| covered    | `tool_surface/required_arguments.feature` |
| L1.2  | Extra / undeclared argument (`additionalProperties: false`)      | covered    | `tool_surface/required_arguments.feature`, `non_goal_rejection.feature` |
| L1.3  | Argument wrong JSON type (incl. boolean rejected for integer)    | covered    | `tool_surface/required_arguments.feature` |
| L1.4  | UID <= 0                                                         | deferred A | Pydantic schema unit tests |
| L1.5  | UID > 2^32 - 1                                                   | deferred A | Pydantic schema unit tests |
| L1.6  | Folder path empty string                                         | deferred A | Pydantic schema unit tests |
| L1.7  | Folder path with NUL or control characters                       | deferred A | Pydantic schema unit tests |
| L1.8  | Folder path exceeds IMAP mailbox-name limit                      | deferred A | Pydantic schema unit tests |
| L1.9  | Account id unknown / ungranted                                   | covered    | `policy/default_deny.feature` |
| L1.10 | Account id contains whitespace / control characters              | deferred A | Pydantic schema unit tests |
| L1.11 | Search `criteria` not a JSON object                              | deferred A | Pydantic schema unit tests |
| L1.12 | Search `criteria` key outside 3.0 grammar (`from`, `to_contains`)| covered    | `tool_surface/required_arguments.feature` |
| L1.13 | Search `criteria` value wrong type (remaining types)             | deferred A | Representative covered in L1.3; remaining in unit tests |
| L1.14 | Search `criteria` empty object `{}`                              | covered    | `policy/blacklist_folder_mode.feature`, `tool_surface/list_messages.feature` |
| L1.15 | `tx_id` for `get_transaction_status` unknown or foreign          | covered    | `transactions/cross_account_move_saga.feature` |
| L1.16 | `tx_id` malformed string                                         | deferred A | Pydantic schema unit tests |
| L1.17 | `rfc822` payload of `create_draft` unparseable                   | deferred A | Draft parser unit tests |
| L1.18 | `rfc822` payload empty                                           | deferred A | Draft parser unit tests |
| L1.19 | `rfc822` payload exceeds `MAX_APPEND_SIZE`                       | deferred A | Draft parser unit tests |
| L1.20 | `mark_tagged` mode outside `{add, remove, replace}`              | deferred A | Pydantic schema unit tests |
| L1.21 | `mark_tagged` tags list empty                                    | deferred A | Pydantic schema unit tests |
| L1.22 | `fetch_attachment` `part_id` out of range for UID                | covered    | `tool_surface/fetch_attachment_content.feature` |

---

## L2 — MCP protocol

| ID   | Error case                                               | Status     | Reference |
|------|----------------------------------------------------------|------------|-----------|
| L2.1 | Unknown tool / non-goal method (`-32601`)                | covered    | `tool_surface/non_goal_rejection.feature` |
| L2.2 | Params missing for JSON-RPC method                       | deferred A | Handled by MCP SDK |
| L2.3 | Malformed non-JSON line on stdio                         | deferred A | Handled by MCP SDK |
| L2.4 | Notification sent where RPC request required             | deferred A | Handled by MCP SDK |
| L2.5 | Second `Initialize` / mid-session identity switch        | covered    | `auth/caller_authentication.feature` |
| L2.6 | Tool call before `Initialize`                            | deferred A | Handled by MCP SDK |
| L2.7 | JSON-RPC batch framing                                   | deferred A | Handled by MCP SDK |
| L2.8 | Unsupported `protocolVersion` in `Initialize`            | deferred A | Handled by MCP SDK |

---

## L3 — Caller authentication & single-caller boundary (`ADR 0049`)

| ID    | Error case                                                 | Status     | Reference |
|-------|------------------------------------------------------------|------------|-----------|
| L3.1  | `stdio_trusted`: unset `IMAP_MCP_CALLER_ID`                | covered    | `auth/caller_authentication.feature` |
| L3.2  | `stdio_trusted`: mismatched `IMAP_MCP_CALLER_ID`           | covered    | `auth/caller_authentication.feature` |
| L3.3  | `shared_token`: empty bearer token                         | covered    | `auth/caller_authentication.feature` |
| L3.4  | `shared_token`: wrong bearer token                         | covered    | `auth/caller_authentication.feature` |
| L3.5  | `shared_token`: prefix-matching bearer token               | covered    | `auth/caller_authentication.feature` |
| L3.6  | `shared_token`: valid token + HTTP `caller_addr` audit     | covered    | `auth/caller_authentication.feature` |
| L3.7  | Mid-session identity switch rejected (`identity_immutable`)| covered    | `auth/caller_authentication.feature` |
| L3.8  | `stdio_trusted` on HTTP transport fatal at startup         | covered    | `auth/caller_authentication.feature` |
| L3.9  | Sub-millisecond token rotation mid-call                    | deferred B | SIGHUP Secret rotation covered in L15.16; race unit-tested |
| L3.10 | `callers.yaml` with zero callers fatal at startup          | covered    | `auth/caller_authentication.feature` |
| L3.11 | `callers.yaml` with >= 2 callers fatal at startup          | covered    | `auth/caller_authentication.feature` |
| L3.12 | Caller without `auth` block fatal at startup               | covered    | `auth/caller_authentication.feature` |

---

## L4 — Policy evaluation (`ADR 0040`, `ADR 0044`)

| ID    | Error case                                                   | Status     | Reference |
|-------|--------------------------------------------------------------|------------|-----------|
| L4.1  | No `AccountPolicy` -> `DENY / account_hidden`                | covered    | `policy/default_deny.feature` |
| L4.2  | No `FolderPolicy` -> `DENY / folder_hidden`                  | covered    | `policy/default_deny.feature`, `tool_surface/folder_path_contract.feature` |
| L4.3  | Whitelist folder, no rule matches -> opaque `not_found`      | covered    | `policy/whitelist_folder_mode.feature` |
| L4.4  | Whitelist folder, matching rule grants requested level       | covered    | `policy/whitelist_folder_mode.feature` |
| L4.5  | Rule grants >= `METADATA` but below tool minimum             | covered    | `policy/visibility_levels.feature` |
| L4.6  | Blacklist folder, no cap rule matches -> folder default      | covered    | `policy/blacklist_folder_mode.feature` |
| L4.7  | Blacklist folder, cap reduces level to `METADATA`            | covered    | `policy/blacklist_folder_mode.feature` |
| L4.8  | Blacklist folder, cap to `NONE` -> opaque `not_found`        | covered    | `policy/blacklist_folder_mode.feature` |
| L4.9  | Multiple whitelist rules match -> max grant regardless of order | covered | `policy/whitelist_folder_mode.feature` |
| L4.10 | Multiple blacklist caps match -> strictest cap wins          | covered    | `policy/blacklist_folder_mode.feature` |
| L4.11 | Required source capability (`move_out` / `copy_out`) missing | covered    | `policy/write_capabilities.feature` |
| L4.12 | Required target capability (`accept_incoming`) missing       | covered    | `policy/write_capabilities.feature` |
| L4.13 | Capabilities present and write succeeds                      | covered    | `policy/write_capabilities.feature` |
| L4.14 | Narrow OAuth scope + write capability rejected at startup    | covered    | `auth/oauth2_bootstrap.feature` |
| L4.15 | Folder `max_visibility` below tool/criteria floor            | covered    | `policy/visibility_levels.feature`, `policy/non_interference_and_field_gates.feature` |
| L4.16 | Folder at `COUNT` allows `folder_stats`, denies `search`     | covered    | `policy/visibility_levels.feature` |
| L4.17 | Hot-reload during in-flight saga                             | deferred B | Cross-reference entry (counted as `covered` in L9.29 & L12.9; listed as deferred here so `covered` is not double-counted) |
| L4.18 | Malformed policy mode/rule invariant rejected                | covered    | `policy/whitelist_folder_mode.feature`, `policy/blacklist_folder_mode.feature` |
| L4.19 | Policy references unknown account ID                         | deferred A | Config validator unit tests (`server/tests/`) |
| L4.20 | Policy grants folder absent on IMAP -> `folder_absent`       | covered    | `tool_surface/folder_path_contract.feature` |
| L4.21 | `callers.yaml` references missing policy file -> rejected    | covered    | `deployment/projected_config_reload.feature` |
| L4.22 | Policy declares retired `modify_message` capability          | covered    | `policy/write_capabilities.feature` |

---

## L5 — IMAP communication (`ADR 0046`, `ADR 0055`)

| ID    | Error case                                                  | Status     | Reference |
|-------|-------------------------------------------------------------|------------|-----------|
| L5.1  | TCP connection refused (`transient_imap_failure`)           | covered    | `transactions/cross_account_move_saga.feature` |
| L5.2  | Implicit TLS (`IMAP4_SSL` port 993) handshake failure       | deferred A | Tracked by [`LIM-0010`](limitations/0010-production-imap-bdd-gaps.md); unit-tested |
| L5.3  | `LOGIN` rejected with bad password                          | covered    | `auth/explicit_imap_user.feature` |
| L5.4  | `LOGIN` rejected: account disabled                          | deferred A | Same code path as L5.3; unit-tested |
| L5.5  | Disconnect mid-`SELECT`                                     | deferred A | Transport unit tests |
| L5.6  | Disconnect mid-`FETCH`                                      | deferred A | Transport unit tests |
| L5.7  | `IDLE` protocol error                                       | deferred A | `IDLE` not used (`ADR 0052`) |
| L5.8  | Connection closed after `APPEND` before tagged response     | covered    | `tool_surface/create_draft_error_visibility.feature` |
| L5.9  | `APPEND` response delayed beyond `append_timeout`           | covered    | `transactions/cross_account_move_saga.feature`, `tool_surface/create_draft_error_visibility.feature` |
| L5.10 | Server lacks both `MOVE` and `UIDPLUS` (`unsupported_imap_capability`) | covered | `transactions/intra_account_move.feature` |
| L5.11 | Server lacks `MOVE`, uses safe `UID EXPUNGE` fallback       | covered    | `transactions/intra_account_move.feature` |
| L5.12 | Capability set shrinks after reconnect                      | deferred A | Capability checked per session; unit-tested |
| L5.13 | `UIDVALIDITY` changes (`uidvalidity_changed`)               | covered    | `transactions/intra_account_move.feature` |
| L5.14 | `SELECT` / `EXAMINE` returns `NO` (`select_failed`)         | covered    | `tool_surface/folder_path_contract.feature`, `tool_surface/reason_code_contract.feature` |

---

## L6 — IMAP response parsing (`ADR 0056`)

| ID   | Error case                                                  | Status     | Reference |
|------|-------------------------------------------------------------|------------|-----------|
| L6.1 | `FETCH` `FLAGS` missing closing parenthesis                 | deferred A | Parser unit tests |
| L6.2 | `LIST` mailbox name with unbalanced quotes                  | deferred A | Parser unit tests |
| L6.3 | `SEARCH` returns non-numeric UID token                      | deferred A | Parser unit tests |
| L6.4 | Malformed `APPENDUID` response                              | deferred A | Falls back to `SEARCH`; unit-tested |
| L6.5 | Missing `Message-ID`, `Date`, or display-name in headers    | covered    | `tool_surface/create_reply_draft.feature` |
| L6.6 | Unexpected `NIL` token in `FETCH` structure                 | deferred A | Parser unit tests |
| L6.7 | Case-variant standard `INBOX` spelling                      | deferred A | Normalized in `imap_core`; unit-tested |
| L6.8 | `\Noselect` mailbox in `LIST`                               | deferred A | Parser unit tests |
| L6.9 | Malformed Modified UTF-7 in `LIST` (`mutf7_decode_fallback`)| covered    | `providers/imap_mailbox_names_utf8.feature` |

---

## L7 — Data integrity & canonical content verification (`ADR 0042`, `ADR 0046`)

| ID    | Error case                                                       | Status     | Reference |
|-------|------------------------------------------------------------------|------------|-----------|
| L7.1  | Message lacks `Message-ID` header                                | covered    | `transactions/saga_crash_recovery.feature`, `tool_surface/create_reply_draft.feature` |
| L7.2  | Target shares `Message-ID` + 4 KiB prefix but differs in tail    | covered    | `transactions/saga_crash_recovery.feature` |
| L7.3  | Unusual legal RFC 5322 `Message-ID` characters                   | deferred A | Parser unit tests |
| L7.4  | Folded multi-line RFC 5322 headers                               | deferred A | Stdlib `email.policy` + unit tests |
| L7.5  | Raw 8-bit MIME body without `Content-Transfer-Encoding`          | deferred A | Parser unit tests |
| L7.6  | Unparseable `Date:` header                                       | deferred A | Time predicates use `INTERNALDATE` (`ADR 0041`); unit-tested |
| L7.7  | External draft edit alters body or strips `X-SC-Provenance`      | covered    | `policy/provenance_own_and_draft_ownership.feature`, `tool_surface/update_draft_errors.feature` |
| L7.8  | Message-ID-less fallback unique by exact size + `canonical_sha256`| covered   | `transactions/saga_crash_recovery.feature` |
| L7.9  | Message-ID-less fallback ambiguous -> `needs_operator`           | covered    | `transactions/saga_crash_recovery.feature` |
| L7.10 | Attachment MIME type disagrees with filename extension           | deferred A | MIME walker unit tests |
| L7.11 | Deeply nested multipart MIME (> 8 levels)                        | deferred A | MIME walker unit tests |

---

## L8 — OAuth2 PKCE & durable health lifecycle (`ADR 0059`, `ADR 0064`)

| ID    | Error case                                                       | Status     | Reference |
|-------|------------------------------------------------------------------|------------|-----------|
| L8.1  | PKCE bootstrap happy path writes `refresh_token`                 | covered    | `auth/oauth2_bootstrap.feature` |
| L8.2  | User denies consent (`access_denied` -> `user_denied`)           | covered    | `auth/oauth2_bootstrap.feature` |
| L8.3  | PKCE `code_verifier` rejected by token endpoint                  | covered    | `auth/oauth2_bootstrap.feature` |
| L8.4  | Interactive callback timeout                                     | deferred A | CLI unit tests |
| L8.5  | OAuth callback `state` mismatch rejected before token exchange   | covered    | `auth/oauth2_bootstrap.feature` |
| L8.6  | `invalid_grant` persists `needs_rebootstrap` across restart & SIGHUP | covered | `auth/oauth2_bootstrap.feature` |
| L8.7  | Proactive 80% token refresh + connection pool drain              | covered    | `auth/oauth2_bootstrap.feature` |
| L8.8  | Token endpoint omits expected scope                              | deferred A | Auth unit tests |
| L8.9  | Token endpoint HTTP 5xx / network timeout                        | deferred A | Auth unit tests |
| L8.10 | Reactive single XOAUTH2 refresh retry on first auth failure      | covered    | `auth/oauth2_bootstrap.feature` |
| L8.11 | Refresh-token `secret_sha256` change clears `needs_rebootstrap`  | deferred A | Bootstrap-clear covered in L8.6; fingerprint rotation unit-tested |

---

## L9 — Saga, SQLite WAL leases & startup recovery (`ADR 0046`)

| ID    | Error case                                                       | Status     | Reference |
|-------|------------------------------------------------------------------|------------|-----------|
| L9.1  | Cross-account move happy path commits & scrubs `staged_rfc822`   | covered    | `transactions/cross_account_move_saga.feature` |
| L9.2  | Target `APPEND` rejected (`append_rejected`), source intact      | covered    | `transactions/cross_account_move_saga.feature` |
| L9.3  | Target `APPEND` delayed past timeout, deduplicated on recovery   | covered    | `transactions/cross_account_move_saga.feature` |
| L9.4  | Target server unreachable (`transient_imap_failure`)             | covered    | `transactions/cross_account_move_saga.feature` |
| L9.5  | Source `UID EXPUNGE` fails once, recovery completes cleanup      | covered    | `transactions/cross_account_move_saga.feature` |
| L9.6  | `retry_limit` exhausted -> `needs_operator`                      | covered    | `transactions/cross_account_move_saga.feature`, `transactions/draft_revision_recovery.feature` |
| L9.7  | Crash after `BEGIN` before `FETCH` -> `aborted`                  | covered    | `transactions/saga_crash_recovery.feature` |
| L9.8  | Crash after `FETCH` before `APPEND` -> replays & scrubs WAL      | covered    | `transactions/saga_crash_recovery.feature` |
| L9.9  | Crash after `APPEND` before `staged` -> verifies `canonical_sha256` | covered | `transactions/saga_crash_recovery.feature`, `transactions/draft_revision_recovery.feature` |
| L9.10 | Crash after `DELETE` before `COMMIT` -> commits cleanly          | covered    | `transactions/saga_crash_recovery.feature`, `transactions/draft_revision_recovery.feature` |
| L9.11 | Target already holds identical canonical copy -> skips `APPEND`  | covered    | `transactions/saga_crash_recovery.feature` |
| L9.12 | Fallback without `Message-ID` unique                             | covered    | `transactions/saga_crash_recovery.feature` |
| L9.13 | Fallback without `Message-ID` ambiguous -> `needs_operator`      | covered    | `transactions/saga_crash_recovery.feature` |
| L9.14 | Cross-account `copy` retains source and commits                  | covered    | `transactions/cross_account_move_saga.feature` |
| L9.15 | Intra-account Gmail label swap                                   | covered    | `providers/gmail_label_semantics.feature` |
| L9.16 | Concurrent move of same source UID in one folder                 | deferred A | Handled by IMAP UID absence (`not_found`) + WAL lease; unit-tested |
| L9.17 | SQLite WAL disk full (`ENOSPC`)                                  | deferred A | OS-level SQLite unit tests |
| L9.18 | Corrupted `wal.db` file at startup                               | deferred A | SQLite integrity unit tests |
| L9.19 | Clock skew between host and IMAP server                          | deferred A | Sagas match content hashes, not local clock |
| L9.20 | Automatic startup recovery: interrupted post-`APPEND` commits    | covered    | `transactions/startup_saga_recovery.feature` |
| L9.21 | Automatic startup recovery: interrupted pre-`APPEND` aborts      | covered    | `transactions/startup_saga_recovery.feature` |
| L9.22 | Automatic startup recovery: unreachable target leaves server up  | covered    | `transactions/startup_saga_recovery.feature` |
| L9.23 | Clean startup with empty WAL emits zero recovery events          | covered    | `transactions/startup_saga_recovery.feature` |
| L9.24 | Saga at `retry_limit` escalates without new target `APPEND`      | covered    | `transactions/startup_saga_recovery.feature` |
| L9.25 | Startup recovery audits chained `saga_transition` across restart | covered    | `transactions/startup_saga_recovery.feature` |
| L9.26 | Missing `wal.db` at first boot initializes `0600` cleanly        | covered    | `transactions/startup_saga_recovery.feature` |
| L9.27 | Second restart is idempotent on already committed sagas          | covered    | `transactions/startup_saga_recovery.feature` |
| L9.28 | Multi-instance SQLite lease serializes concurrent startup & draft CAS | covered | `transactions/startup_saga_recovery.feature`, `tool_surface/update_draft.feature` |
| L9.29 | Pre-`APPEND` policy revocation aborts cleanly (`saga_aborted`)   | covered    | `policy/policy_reload.feature` |

---

## L10 — Pluggable secret stores (`ADR 0050`)

| ID     | Error case                                                       | Status     | Reference |
|--------|------------------------------------------------------------------|------------|-----------|
| L10.1  | `file_dir`: secret read with `acknowledge_plaintext: true`       | covered    | `auth/secret_store_backends.feature` |
| L10.2  | `file_dir`: missing secret file -> `auth_failed`                 | covered    | `auth/secret_store_backends.feature` |
| L10.3  | `file_dir`: unreadable permissions on secret file                | deferred A | SecretStore unit tests |
| L10.4  | `file_dir`: base path is a regular file                          | deferred A | SecretStore unit tests |
| L10.5  | `env_var`: matching environment variable set                     | covered    | `auth/secret_store_backends.feature` |
| L10.6  | `env_var`: environment variable unset                            | covered    | `auth/secret_store_backends.feature` |
| L10.7  | `env_var`: rejects OAuth bootstrap & `persist_all`               | covered    | `auth/secret_store_backends.feature` |
| L10.8  | `gpg_file`: decryption succeeds with loopback passphrase         | covered    | `auth/secret_store_backends.feature` |
| L10.9  | `gpg_file`: caller token wrong passphrase -> `auth_failed`       | covered    | `auth/secret_store_backends.feature` |
| L10.10 | `gpg_file`: `gpg` binary absent from `PATH`                      | deferred A | SecretStore unit tests |
| L10.11 | `file_dir` without `acknowledge_plaintext: true` fatal at startup| covered    | `auth/secret_store_backends.feature` |
| L10.12 | `gpg_file`: account credential wrong passphrase -> `secret_unavailable`| covered | `auth/secret_store_backends.feature` |

---

## L11 — Pre-effect audit chain, retention & test-mode gate (`ADR 0048`, `ADR 0058`)

| ID     | Error case                                                       | Status     | Reference |
|--------|------------------------------------------------------------------|------------|-----------|
| L11.1  | Read `ALLOW` `single` record & write `intent`+`outcome` pair     | covered    | `audit/audit_log_format.feature` |
| L11.2  | No-content-leak across bodies, subjects, filenames & active OAuth| covered    | `audit/audit_log_format.feature` |
| L11.3  | Multi-instance concurrent & interleaved continuous hash chain    | covered    | `audit/audit_multi_instance.feature` |
| L11.4  | Tampering with intermediate record detected by chain verifier    | covered    | `audit/audit_log_format.feature` |
| L11.5  | UTC midnight day rotation (`eof_day` + `final_hash` + `0400`)    | covered    | `audit/audit_log_format.feature`, `audit/audit_multi_instance.feature` |
| L11.6  | Keyed HMAC `search_query_digest` + `suppressed_by_policy`        | covered    | `audit/audit_log_format.feature` |
| L11.7  | Keyed HMAC `from_domain_digest` on capped message                | covered    | `audit/audit_log_format.feature` |
| L11.8  | Permissions (`0700` dir, `0600` active, `0400` sealed/warm)      | covered    | `audit/audit_log_format.feature`, `audit/audit_retention.feature` |
| L11.9  | Out-of-band deletion of active file (`audit_file_missing`)       | covered    | `audit/audit_retention.feature` |
| L11.10 | Safe `external_root_hook` without shell or leaked secret env vars| covered    | `audit/audit_retention.feature` |
| L11.11 | Mid-write kernel `EIO` during `fsync`                            | deferred A | OS-fault unit test |
| L11.12 | Unwritable audit aborts write tool at `intent` before IMAP effect| covered    | `audit/audit_multi_instance.feature` |
| L11.13 | Automatic Hot -> Warm -> Deleted retention on UTC day seal       | covered    | `audit/audit_retention.feature` |
| L11.14 | JSON-RPC `-32602` schema error writes `invalid_arguments` record | covered    | `audit/audit_log_format.feature` |
| L11.15 | All 6 test-hook env vars without `IMAP_MCP_TEST_MODE=1` fatal    | covered    | `audit/audit_log_format.feature` |
| L11.16 | Unexpected runtime exception logs `internal_error` + `error_class`| covered   | `audit/audit_log_format.feature` |

---

## L12 — Validated configuration & generation loading (`ADR 0049`)

| ID     | Error case                                                       | Status     | Reference |
|--------|------------------------------------------------------------------|------------|-----------|
| L12.1  | Valid configuration loads cleanly (incl. `~` expansion)          | covered    | All feature Backgrounds + config unit tests |
| L12.2  | YAML syntax error rejected at startup and on `SIGHUP`            | covered    | `policy/whitelist_folder_mode.feature`, `policy/policy_reload.feature`, `deployment/projected_config_reload.feature` |
| L12.3  | Whitelist mode with non-`NONE` default rejected                  | covered    | `policy/whitelist_folder_mode.feature` |
| L12.4  | Blacklist mode with `NONE` default rejected                      | covered    | `policy/blacklist_folder_mode.feature` |
| L12.5  | Whitelist folder with `cap` rule rejected                        | covered    | `policy/whitelist_folder_mode.feature` |
| L12.6  | Blacklist folder with `grant` rule rejected                      | covered    | `policy/blacklist_folder_mode.feature` |
| L12.7  | Unreachable rule (`grant: NONE` in whitelist) rejected           | covered    | `policy/whitelist_folder_mode.feature` |
| L12.8  | Unknown or retired 2.x predicate in rule rejected                | covered    | `policy/sender_rule_matcher.feature` |
| L12.9  | `SIGHUP` reload swaps state atomically while staged saga finishes| covered    | `policy/policy_reload.feature` |
| L12.10 | `SIGHUP` reload with semantic error preserves previous policy    | covered    | `policy/policy_reload.feature` |
| L12.11 | Adding folder policy at runtime surfaces folder in `list_folders`| covered    | `policy/policy_reload.feature` |
| L12.12 | OAuth scope change on reload transitions to `needs_rebootstrap`  | covered    | `policy/policy_reload.feature` |
| L12.13 | Removing account on reload drains pooled connections (`LOGOUT`)  | covered    | `policy/policy_reload.feature` |
| L12.14 | Unstable `..data` symlink across 3 mid-read swaps fails closed   | deferred B | Sub-millisecond kubelet race tested in `server/tests/` |

---

## L13 — Bounded connection pool (`ADR 0055`)

| ID     | Error case                                                       | Status     | Reference |
|--------|------------------------------------------------------------------|------------|-----------|
| L13.1  | Empty pool opens 1 authenticated connection                      | covered    | `performance/connection_pool.feature` |
| L13.2  | Sequential calls reuse pooled connection after `NOOP`            | covered    | `performance/connection_pool.feature` |
| L13.3  | Failed `NOOP` evicts dead socket and opens replacement           | covered    | `performance/connection_pool.feature` |
| L13.4  | Exhausted `acquire_timeout` returns `ALLOW + ERROR pool_exhausted`| covered   | `performance/connection_pool.feature` |
| L13.5  | `max_age` expiry logs out and replaces connection                | covered    | `performance/connection_pool_expiry.feature` |
| L13.6  | `idle_ttl` expiry logs out and replaces connection               | covered    | `performance/connection_pool_expiry.feature` |
| L13.7  | OAuth token refresh drains account pool (`pool_drain`)           | covered    | `auth/oauth2_bootstrap.feature` |
| L13.8  | Long-lived `IDLE` push watcher                                   | deferred B | Non-goal per `ADR 0052` |
| L13.9  | Separate accounts maintain isolated pools                        | covered    | `performance/connection_pool.feature` |
| L13.10 | Reused connection issues `SELECT` on folder switch               | covered    | `performance/connection_pool.feature` |
| L13.11 | Concurrent calls bounded by default `max_size=2`                 | covered    | `performance/connection_pool.feature` |
| L13.12 | Clean server shutdown sends `LOGOUT` on pooled connections       | covered    | `performance/connection_pool.feature` |

---

## L14 — Gmail multi-label & localized SUT semantics (`ADR 0040`, `ADR 0056`)

| ID     | Error case                                                                 | Status     | Reference |
|--------|----------------------------------------------------------------------------|------------|-----------|
| L14.1  | `describe_policy` reports `semantics="gmail-labels"`                       | covered    | `providers/gmail_label_semantics.feature` |
| L14.2  | Multi-label message correlates via `object_id`, omits `canonical_all_mail_uid` | covered | `providers/gmail_label_semantics.feature` |
| L14.3  | `list_labels` & `fetch_envelope.labels` filter out ungranted labels        | covered    | `providers/gmail_label_semantics.feature` |
| L14.4  | `list_labels` denied on non-Google provider (`tool_not_applicable_for_provider`) | covered | `providers/gmail_label_semantics.feature`, `tool_surface/reason_code_contract.feature` |
| L14.5  | Intra-account move adds target label before removing source, keeps 3rd label| covered   | `providers/gmail_label_semantics.feature` |
| L14.6  | Cross-account saga fetches RFC822 from `[Gmail]/All Mail`                  | covered    | `providers/gmail_label_semantics.feature` |
| L14.7  | Unlisted `[Gmail]/Trash` omitted without `hidden_folders_count`            | covered    | `providers/gmail_label_semantics.feature` |
| L14.8  | Move target is a brand-new user label not yet created on Gmail             | deferred B | Live-Gmail manual smoke test |
| L14.9  | Direct move into `[Gmail]/All Mail`                                        | deferred B | Live-Gmail manual smoke test |
| L14.10 | Concurrent external Gmail web UI label mutation mid-call                   | deferred B | Live-Gmail manual smoke test |
| L14.11 | First-call `search` resolves canonical `[Gmail]/Sent Mail`                 | covered    | `providers/localized_gmail_resolution.feature` |
| L14.12 | First-call `list_messages` resolves canonical `[Gmail]/Trash`              | covered    | `providers/localized_gmail_resolution.feature` |
| L14.13 | First-call `folder_stats` resolves canonical `[Gmail]/All Mail`            | covered    | `providers/localized_gmail_resolution.feature` |
| L14.14 | First-call `create_draft` appends to localized `[Gmail]/Entw&APw-rfe`      | covered    | `providers/localized_gmail_resolution.feature` |
| L14.15 | Label-swap move canonical -> canonical on localized account                | covered    | `providers/localized_gmail_resolution.feature` |
| L14.16 | Label-swap move localized -> localized                                     | covered    | `providers/localized_gmail_resolution.feature` |
| L14.17 | Label-swap move canonical -> localized                                     | covered    | `providers/localized_gmail_resolution.feature` |
| L14.18 | Missing source folder in label swap -> `error.type="folder_absent"`        | covered    | `providers/localized_gmail_resolution.feature` |
| L14.19 | Cross-account saga resolves localized `[Gmail]/Alle Nachrichten`           | covered    | `providers/localized_gmail_resolution.feature` |
| L14.20 | Canonical-only policy governs localized caller argument                    | covered    | `providers/localized_gmail_resolution.feature` |
| L14.21 | Localized-only policy governs canonical caller argument                    | covered    | `providers/localized_gmail_resolution.feature` |
| L14.22 | Discrepant duplicate entries fail closed (`policy_alias_conflict`)         | covered    | `providers/localized_gmail_resolution.feature` |
| L14.23 | Responses always echo canonical folder path                                | covered    | `providers/localized_gmail_resolution.feature` |
| L14.24 | `update_draft` on canonical `[Gmail]/Drafts` cleans up localized wire draft| covered    | `providers/gmail_draft_lifecycle.feature` |
| L14.25 | `discard_draft` moves all authentic revisions to localized `[Gmail]/Trash` | covered    | `providers/gmail_draft_lifecycle.feature` |
| L14.26 | Move `INBOX` -> `[Gmail]/Trash` sends atom `\Trash`                        | covered    | `providers/gmail_draft_lifecycle.feature` |
| L14.27 | Move `INBOX` -> `[Gmail]/Spam` sends atom `\Spam`                          | covered    | `providers/gmail_draft_lifecycle.feature` |
| L14.28 | Move draft from `[Gmail]/Drafts` to user label removes draft               | covered    | `providers/gmail_draft_lifecycle.feature` |
| L14.29 | Calendar windows & `window_limited` completeness on Gmail                  | covered    | `providers/gmail_search_and_counts.feature` |
| L14.30 | `has_attachment` in `search` and `list_messages` at `BODY` visibility      | covered    | `providers/gmail_search_and_counts.feature` |
| L14.31 | `list_folders` omits `message_count`; `folder_stats` reports `visible_count`| covered   | `providers/gmail_search_and_counts.feature` |
| L14.32 | Identical canonical + localized duplicate policy entries collapse cleanly  | covered    | `providers/localized_gmail_resolution.feature` |

---

## L15 — Sidecar deployment, `/healthz` & projected reload (`ADR 0051`)

| ID     | Error case                                                       | Status  | Reference |
|--------|------------------------------------------------------------------|---------|-----------|
| L15.1  | `GET /healthz` returns `200 {"status": "ok"}` when healthy       | covered | `deployment/health_probe.feature` |
| L15.2  | Unwritable audit dir flips `GET /healthz` to `503 unavailable`   | covered | `deployment/health_probe.feature` |
| L15.3  | `GET /healthz` succeeds without credentials                      | covered | `deployment/health_probe.feature` |
| L15.4  | Wrong bearer token on `/healthz` still returns `200`, 0 audits   | covered | `deployment/health_probe.feature` |
| L15.5  | `/healthz` body contains only `"status"`, zero metadata leaks    | covered | `deployment/health_probe.feature` |
| L15.6  | `POST`/`PUT`/`DELETE` on `/healthz` return `404`                 | covered | `deployment/health_probe.feature` |
| L15.7  | Neighbouring routes (`/health`, `/healthz/detail`, `/readyz`, `/metrics`, `/admin`) `404` | covered | `deployment/health_probe.feature` |
| L15.8  | Probe traffic writes zero records to the audit chain             | covered | `deployment/health_probe.feature` |
| L15.9  | ConfigMap `..data` swap without `SIGHUP` keeps old policy        | covered | `deployment/projected_config_reload.feature` |
| L15.10 | ConfigMap `..data` swap + `SIGHUP` activates new generation      | covered | `deployment/projected_config_reload.feature` |
| L15.11 | Deleting superseded timestamp directory does not break reload    | covered | `deployment/projected_config_reload.feature` |
| L15.12 | Multiple swaps before one `SIGHUP` apply newest generation       | covered | `deployment/projected_config_reload.feature` |
| L15.13 | Malformed YAML in swapped ConfigMap rejected, preserves old      | covered | `deployment/projected_config_reload.feature` |
| L15.14 | Switching caller to newly projected policy file succeeds         | covered | `deployment/projected_config_reload.feature` |
| L15.15 | Removing referenced policy file rejected, preserves old          | covered | `deployment/projected_config_reload.feature` |
| L15.16 | Re-projected Secret volume rotates `shared_token` on `SIGHUP`    | covered | `deployment/projected_config_reload.feature` |

---

## L16 — Drafts, replies, calendar windows & per-caller attachment sink (`ADR 0043`–`0047`, `ADR 0063`)

| ID     | Error case                                                       | Status     | Reference |
|--------|------------------------------------------------------------------|------------|-----------|
| L16.1  | `discard_draft` happy path on standard IMAP `\Trash`             | covered    | `tool_surface/discard_draft.feature` |
| L16.2  | `discard_draft` happy path on localized German Gmail             | covered    | `tool_surface/discard_draft.feature` |
| L16.3  | `discard_draft` sweeps authentic residue revisions together      | covered    | `tool_surface/discard_draft.feature` |
| L16.4  | `discard_draft` without `move_out` on drafts denied              | covered    | `tool_surface/discard_draft.feature` |
| L16.5  | `discard_draft` without `accept_incoming` on trash denied        | covered    | `tool_surface/discard_draft.feature` |
| L16.6  | `discard_draft` on account lacking `\Trash` -> `trash_unavailable`| covered   | `tool_surface/discard_draft.feature` |
| L16.7  | `discard_draft` on unknown handle -> `not_found`                 | covered    | `tool_surface/discard_draft.feature` |
| L16.8  | `discard_draft` leaves human draft with same subject untouched   | covered    | `tool_surface/discard_draft.feature` |
| L16.9  | `create_draft` description presents format choice neutrally      | covered    | `tool_surface/create_draft_format.feature` |
| L16.10 | `create_draft` reports `text/plain`                              | covered    | `tool_surface/create_draft_format.feature` |
| L16.11 | `create_draft` reports `text/html`                               | covered    | `tool_surface/create_draft_format.feature` |
| L16.12 | `create_draft` reports `multipart/alternative`                   | covered    | `tool_surface/create_draft_format.feature` |
| L16.13 | Structured `create_draft` with `body_text`                       | covered    | `tool_surface/create_draft_format.feature` |
| L16.14 | Structured `create_draft` with `body_html` (+ plain fallback)    | covered    | `tool_surface/create_draft_format.feature` |
| L16.15 | Structured `create_draft` with both `body_text` and `body_html`  | covered    | `tool_surface/create_draft_format.feature` |
| L16.16 | Non-ASCII subject & HTML body with ASCII-only wire headers       | covered    | `tool_surface/create_draft_format.feature` |
| L16.17 | Passing both `rfc822` and structured fields -> `-32602`          | covered    | `tool_surface/create_draft_format.feature` |
| L16.18 | Passing neither `rfc822` nor body field -> `-32602`              | covered    | `tool_surface/create_draft_format.feature` |
| L16.19 | `create_reply_draft` with `reply_html` sanitizes blockquote      | covered    | `tool_surface/create_draft_format.feature` |
| L16.20 | Static `completeness` `"window_limited"` & `"complete"`          | covered    | `tool_surface/search_completeness.feature` |
| L16.21 | Static `completeness` `"window_and_policy_limited"` & `"policy_may_hide"` | covered | `tool_surface/search_completeness.feature` |
| L16.22 | `outside_scope_total` absent across all scopes                   | covered    | `tool_surface/search_completeness.feature` |
| L16.23 | Calendar window `since` + `before` (incl. exact UTC midnight)    | covered    | `tool_surface/search_completeness.feature` |
| L16.24 | Calendar window `since` only                                     | covered    | `tool_surface/search_completeness.feature` |
| L16.25 | Non-ISO or impossible calendar date (`2026-02-30`) -> `-32602`   | covered    | `tool_surface/search_completeness.feature` |
| L16.26 | `since >= before` -> `-32602`                                    | covered    | `tool_surface/search_completeness.feature` |
| L16.27 | Calendar bound mixed with duration -> `-32602`                   | covered    | `tool_surface/search_completeness.feature` |
| L16.28 | `search` on authorized missing folder -> `folder_absent`         | covered    | `tool_surface/search_completeness.feature` |
| L16.29 | `list_messages` on authorized missing folder -> `folder_absent`  | covered    | `tool_surface/search_completeness.feature` |
| L16.30 | Structured `attachments` on `create_draft` and `update_draft`    | covered    | `tool_surface/create_draft_format.feature`, `tool_surface/update_draft.feature` |
| L16.31 | Symlink escape blocked (`O_NOFOLLOW`) on sink base & caller dir  | covered    | `tool_surface/fetch_attachment_sink.feature` |
| L16.32 | `tools/list` static description omits host sink path (`LIM 0016`)| covered    | `tool_surface/fetch_attachment_sink.feature` |
| L16.33 | `update_draft` duplicate handle/revision -> `draft_ambiguous`    | covered    | `tool_surface/update_draft_errors.feature` |
| L16.34 | `update_draft` stale/future `base_revision` -> `draft_revision_conflict` | covered | `tool_surface/update_draft_errors.feature` |
| L16.35 | `create_reply_draft` empty body -> `empty_reply_text`            | covered    | `tool_surface/create_reply_draft.feature` |
| L16.36 | `fetch_attachment` unset sink -> `sink_not_configured`           | covered    | `tool_surface/fetch_attachment_sink.feature` |
| L16.37 | `fetch_attachment` missing/read-only dir -> `sink_not_writable`  | covered    | `tool_surface/fetch_attachment_sink.feature` |
| L16.38 | `fetch_attachment` `ENOSPC` partial write unlinks incomplete file| deferred A | OS-fault unit test (`server/tests/`) |

---

## L17 — Non-interfering OpenTelemetry spans (`ADR 0053`)

| ID     | Error case                                                       | Status  | Reference |
|--------|------------------------------------------------------------------|---------|-----------|
| L17.1  | Default payload mode is `redacted`                               | covered | `observability/tracing_payload_mode.feature` |
| L17.2  | `redacted` scrubs draft content & recipients from `mcp.request`  | covered | `observability/tracing_payload_mode.feature` |
| L17.3  | `redacted` scrubs subjects & addresses from `mcp.response`       | covered | `observability/tracing_payload_mode.feature` |
| L17.4  | `full` records complete request and response                     | covered | `observability/tracing_payload_mode.feature` |
| L17.5  | `full` truncates oversized payload with explicit marker          | covered | `observability/tracing_payload_mode.feature` |
| L17.6  | `off` omits payload attributes while preserving timing           | covered | `observability/tracing_payload_mode.feature` |
| L17.7  | Active mode attribute recorded (`off`)                           | covered | `observability/tracing_payload_mode.feature` |
| L17.8  | Active mode attribute recorded (`redacted`)                      | covered | `observability/tracing_payload_mode.feature` |
| L17.9  | Active mode attribute recorded (`full`)                          | covered | `observability/tracing_payload_mode.feature` |
| L17.10 | Secrets absent from spans (`off`)                                | covered | `observability/tracing_payload_mode.feature` |
| L17.11 | Secrets absent from spans (`redacted`)                           | covered | `observability/tracing_payload_mode.feature` |
| L17.12 | Secrets absent from spans (`full`)                               | covered | `observability/tracing_payload_mode.feature` |
| L17.13 | Unknown `IMAP_MCP_TRACE_PAYLOADS` value refused at startup       | covered | `observability/tracing_payload_mode.feature` |
| L17.14 | `OTEL_RESOURCE_ATTRIBUTES` recorded on every span                | covered | `observability/tracing_spans.feature` |
| L17.15 | `service.name`, `service.version`, `service.instance.id` present | covered | `observability/tracing_spans.feature` |
| L17.16 | `mcp.client.name` & `mcp.client.version` recorded on tool spans  | covered | `observability/tracing_spans.feature` |
| L17.17 | `mcp.response.size_bytes` matches exact UTF-8 wire length        | covered | `observability/tracing_spans.feature` |
| L17.18 | Listing spans record `visible`/`returned`/`has_more`, omit `matched` | covered | `observability/tracing_spans.feature` |
| L17.19 | Policy `DENY` sets `domain.outcome="rejected"`, status `UNSET`   | covered | `observability/tracing_spans.feature` |
| L17.20 | `ALLOW + ERROR` sets `error.type` and span status `ERROR`        | covered | `observability/tracing_spans.feature` |
| L17.21 | IMAP wire commands appear as ordered `imap.*` child spans        | covered | `observability/tracing_spans.feature` |
| L17.22 | Queued spans flush on stdin EOF (60s BSP schedule delay)         | covered | `observability/tracing_spans.feature` |
| L17.23 | Queued spans flush on `SIGTERM` (60s BSP schedule delay)         | covered | `observability/tracing_spans.feature` |
| L17.24 | `redacted` scrubs attachment filenames & paths on `fetch_attachment` | covered | `observability/tracing_payload_mode.feature` |
| L17.25 | Unreachable OTLP collector never fails MCP tool calls            | covered | `observability/tracing_spans.feature` |
| L17.26 | Oversized/control-char `clientInfo` sanitized to 128 ASCII chars | covered | `observability/tracing_spans.feature` |
| L17.27 | Two-world `NONE` non-interference on `tool.search` span attributes| covered | `observability/tracing_spans.feature` |

---

## L18 — Authorization side-channels, `Write <= Read` & flow monotonicity (`ADR 0040`–`0046`)

| ID     | Error case                                                                 | Status  | Reference |
|--------|----------------------------------------------------------------------------|---------|-----------|
| L18.1  | Two-world `NONE` non-interference on broad & targeted `search`             | covered | `policy/non_interference_and_field_gates.feature` |
| L18.2  | Two-world `NONE` non-interference on `list_messages`, `folder_stats`, `bulk_mark_tagged` | covered | `policy/non_interference_and_field_gates.feature` |
| L18.3  | Indistinguishable `not_found` + zero mutation for `< METADATA` vs missing UID | covered | `policy/non_interference_and_field_gates.feature` |
| L18.4  | Folder-level predicate floor denial (`METADATA` + `subject_contains`, `ENVELOPE` + `has_attachment`) | covered | `policy/non_interference_and_field_gates.feature` |
| L18.5  | Per-message predicate floor filtering in mixed-visibility folders (`search` & `bulk_mark_*`) | covered | `policy/non_interference_and_field_gates.feature`, `tool_surface/bulk_mark_seen.feature`, `tool_surface/bulk_mark_tagged.feature` |
| L18.6  | `Write <= Read` floor on `mark_seen` (`COUNT` -> `not_found`)              | covered | `policy/write_visibility_and_flow_monotonicity.feature` |
| L18.7  | `Write <= Read` floor on `mark_tagged`, `move`, `copy` (`METADATA` -> `visibility_below_ENVELOPE`) | covered | `policy/write_visibility_and_flow_monotonicity.feature` |
| L18.8  | Per-message `BODY` floor & MIME attachment/`message/rfc822` exclusion on `create_reply_draft` | covered | `tool_surface/create_reply_draft.feature` |
| L18.9  | Flow monotonicity (`relocation_would_escalate`) on intra-`move`, intra-`copy`, saga, Gmail label-add | covered | `policy/write_visibility_and_flow_monotonicity.feature` |
| L18.10 | Source `copy_out` capability required on `copy`                            | covered | `policy/write_capabilities.feature` |
| L18.11 | Read-only `\Flagged` (`add`/`remove`/`replace` & bulk) + system flags (`forbidden_flag`) | covered | `policy/flagged_predicate.feature`, `tool_surface/non_goal_rejection.feature` |
| L18.12 | Gmail account-wide `blacklist` cap-override & `[Gmail]/All Mail` non-escalation + positive control | covered | `policy/write_visibility_and_flow_monotonicity.feature` |
| L18.13 | Gmail `\Trash`/`\Spam` move requires `move_out` on all active specific labels | covered | `policy/write_visibility_and_flow_monotonicity.feature` |
| L18.14 | `draft_recipients` allowlist blocks `To`/`Cc`/`Bcc` across structured, `rfc822`, reply, and update | covered | `policy/write_visibility_and_flow_monotonicity.feature` |
| L18.15 | `provenance: "own"` grants read continuity via HMAC header and via WAL `Message-ID`+digest | covered | `policy/provenance_own_and_draft_ownership.feature` |
| L18.16 | External body edit or stripped `X-SC-Provenance` on own draft -> `draft_not_editable` | covered | `policy/provenance_own_and_draft_ownership.feature`, `tool_surface/update_draft_errors.feature` |
| L18.17 | Foreign/unsigned `X-SC-Draft-Id` -> `not_found` & `discard_draft` blocked outside `\Drafts` | covered | `policy/provenance_own_and_draft_ownership.feature`, `tool_surface/update_draft_errors.feature` |
| L18.18 | Cross-caller handle isolation on `draft_id` and `tx_id` (`not_found`)      | covered | `policy/provenance_own_and_draft_ownership.feature`, `transactions/cross_account_move_saga.feature` |

---

## Appendix: Mock Fixture Fidelity Checks (not counted toward SUT error paths)

Per BDD Guidelines §7.2, eight fixture checks (12 expanded scenarios)
verify that `bdd/mock-gmail` itself enforces real Gmail wire behaviour
rather than wishful shortcuts. They are excluded from the SUT totals above:

1. Localized mock refuses English `[Gmail]/Drafts` (`providers/localized_gmail_resolution.feature`)
2. Mock refuses quoted `("\Trash")` with `BAD` and accepts atom `(\Trash)` (`providers/gmail_draft_lifecycle.feature`)
3. Mock draft carries `\Draft` and `X-GM-LABELS ()` (`providers/gmail_draft_lifecycle.feature`)
4. Mock auto-expunges `+FLAGS (\Deleted)` immediately (`providers/gmail_draft_lifecycle.feature`)
5. Mock advertises `MOVE` and `UIDPLUS` only after authentication (`providers/gmail_draft_lifecycle.feature`)
6. Mock evaluates `UID SEARCH` `BEFORE`/`ON`/`SINCE`/`SENT*` per RFC 3501 (`providers/gmail_search_and_counts.feature`)
7. Mock renders `BODYSTRUCTURE` multipart + attachment dispositions (`providers/gmail_search_and_counts.feature`)
8. Mock reports `STATUS MESSAGES` count (`providers/gmail_search_and_counts.feature`)
